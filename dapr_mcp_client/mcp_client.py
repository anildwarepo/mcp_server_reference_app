import asyncio
import uuid
import re
import json
import sys
import os
from contextlib import AsyncExitStack
from typing import Optional

from fastapi import params
import httpx
from mcp import ClientSession, ListToolsResult
from mcp.client.streamable_http import streamablehttp_client
from collections import defaultdict
from .sse_bus import SESSIONS, sse_event, JSONRPC, publish_progress, publish_message, associate_user_session, session_for_user
from shared.models import parse_notification_json, ProgressNotification, MessageNotification
import mcp.types as types
from mcp.shared.session import RequestResponder   

class MCPClient:
    def __init__(self, mcp_endpoint: str):
        self.mcp_endpoint = mcp_endpoint
        self.exit_stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None
        self.session_id: Optional[str] = None
        self.mcp_tools: Optional[ListToolsResult] = None
        self._sse_task: Optional[asyncio.Task] = None
        self._broadcast_session_id: str | None = None


    async def _on_incoming(
        self,
        msg: RequestResponder[types.ServerRequest, types.ClientResult]
            | types.ServerNotification
            | Exception,
    ) -> None:
        # Errors from the stream
        if isinstance(msg, Exception):
            print(f"[mcp] incoming exception: {msg!r}", file=sys.stderr)
            return

        # Server requests (sampling/elicitation/etc). Ignore unless you support them.
        if isinstance(msg, RequestResponder):
            return

        # Server notifications (what you want)
        if isinstance(msg, types.ServerNotification):
            root = msg.root
            method = getattr(root, "method", None)
            params = getattr(root, "params", None)

            # Build the JSON shape your existing parser expects
            if hasattr(params, "model_dump"):
                params_json = params.model_dump(mode="json")
            else:
                params_json = params

            payload = {"jsonrpc": "2.0", "method": method, "params": params_json}

            try:
                notif = parse_notification_json(json.dumps(payload))
            except Exception as e:
                print(f"[mcp] notification parse error: {e}", file=sys.stderr)
                return

            # PROGRESS
            if isinstance(notif, ProgressNotification):
                pct   = float(notif.params.progress)
                token = notif.params.progressToken
                target = session_for_user(notif.user_id) or self._broadcast_session_id
                if target:
                    await self._broadcast_progress(pct, target, token)
                return

            # MESSAGE
            if isinstance(notif, MessageNotification):
                target = session_for_user(notif.user_id) or self._broadcast_session_id
                texts  = [d.text for d in notif.params.data]
                level  = notif.params.level
                if target:
                    await self._broadcast_assistant(" ".join(t for t in texts if t), level, target)
                return

    async def _broadcast_progress(self, progress: float, target: Optional[str] = None, token: Optional[str] = None) -> None:
        target = target or self._broadcast_session_id
        print(f"mcp_client.py _broadcast_progress session_id {target}")
        if target:
            await publish_progress(target, token, progress)

    async def _broadcast_assistant(self, text: str, level: Optional[str] = None, target: Optional[str] = None) -> None:
        target = target or self._broadcast_session_id
        print(f"mcp_client.py _broadcast_assistant session_id {target}")
        if target:
            await publish_message(target, text, level)

    def set_broadcast_session(self, session_id: str) -> None:
        self._broadcast_session_id = session_id
    
    
    async def connect(self, session_id: str, start_sse: bool = False) -> None:
        """
        Open the Streamable HTTP JSON-RPC channel (and optional SSE listener)
        and list tools. Must be closed via `await aclose()` from the same task.
        """
        self.exit_stack = AsyncExitStack()
        await self.exit_stack.__aenter__()  # enter now; we'll explicitly aclose later
        self.session_id = session_id #str(uuid.uuid4())
        headers = {"Mcp-Session-Id": self.session_id}

        # JSON-RPC duplex channel over Streamable HTTP
        streamable_http_client = streamablehttp_client(url=self.mcp_endpoint, headers=headers)
        read, write, _ = await self.exit_stack.enter_async_context(streamable_http_client)

        # Create the JSON-RPC session on the same exit stack
        #self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read, write, message_handler=self._on_incoming)
        )
        
        await self.session.initialize()
        await self.session.send_ping()

        self.session._message_handler
        # Discover tools
        self.mcp_tools = await self.session.list_tools()

    async def aclose(self) -> None:
        """
        Close SSE (if running) and the AsyncExitStack that owns the stream,
        **from the same task** that created it.
        """
        # Stop SSE first so the HTTP GET isn’t dangling
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            finally:
                self._sse_task = None

        if self.exit_stack is not None:
            # This ensures the async generator context is closed in the same task
            await self.exit_stack.aclose()
            self.exit_stack = None





