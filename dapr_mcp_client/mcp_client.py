

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
from sse_bus import SESSIONS, sse_event, JSONRPC, publish_progress, publish_message, associate_user_session, session_for_user

class MCPClient:
    def __init__(self, mcp_endpoint: str):
        self.mcp_endpoint = mcp_endpoint
        self.exit_stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None
        self.session_id: Optional[str] = None
        self.mcp_tools: Optional[ListToolsResult] = None
        self._sse_task: Optional[asyncio.Task] = None
        self._broadcast_session_id: str | None = None


    async def _broadcast_progress(self, progress: float, target: Optional[str] = None, token: Optional[str] = None) -> None:
        target = target or self._broadcast_session_id
        print(f"_broadcast_progress session_id {target}")
        if target:
            await publish_progress(target, token, progress)

    async def _broadcast_assistant(self, text: str, level: Optional[str] = None, target: Optional[str] = None) -> None:
        target = target or self._broadcast_session_id
        print(f"_broadcast_assistant session_id {target}")
        if target:
            await publish_message(target, text, level)

    def set_broadcast_session(self, session_id: str) -> None:
        self._broadcast_session_id = session_id
    

    async def progress_listener(self) -> None:
        print(f"[SSE] starting listener for session {self.session_id}", file=sys.stderr, flush=True)
        headers = {
            "Mcp-Session-Id": self.session_id,
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        transport = httpx.AsyncHTTPTransport(retries=0)
        backoff = 2

        def reset_frame():
            return {"event": None, "data_lines": []}

        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=transport, http2=False) as client:
                    async with client.stream("GET", self.mcp_endpoint, headers=headers) as resp:
                        frame = reset_frame()

                        async for raw_line in resp.aiter_lines():
                            # DEBUG: see every wire line
                            #print(f"[SSE] raw line: {raw_line}", file=sys.stderr, flush=True)

                            if raw_line is None:
                                continue
                            line = raw_line.strip("\r")

                            # Blank line → end of frame
                            if line == "":
                                if frame["data_lines"]:
                                    data_str = "\n".join(frame["data_lines"])
                                    try:
                                        root = json.loads(data_str)
                                    except json.JSONDecodeError:
                                        frame = reset_frame()
                                        continue

                                    method = root.get("method")
                                    params = root.get("params") or {}

                                    # PROGRESS
                                    if method == "notifications/progress" or (
                                        "progress" in root and "progressToken" in root
                                    ) or (
                                        "progress" in params and "progressToken" in params
                                    ):
                                        pct = (params.get("progress") if params else root.get("progress"))
                                        token = (params.get("progressToken") if params else root.get("progressToken"))
                                        target = session_for_user(root.get("user_id")) or self._broadcast_session_id
                                        if isinstance(pct, (int, float)) and target:
                                            print(f"session {self.session_id} << progress {pct:.0%}", file=sys.stderr, flush=True)
                                            await self._broadcast_progress(float(pct), target, token)

                                    # MESSAGE
                                    elif method == "notifications/message" and "params" in root:
                                        target = session_for_user(root.get("user_id")) or self._broadcast_session_id
                                        data = params.get("data", [])
                                        texts = [d.get("text") for d in data if isinstance(d, dict) and d.get("type") == "text"]
                                        text = " ".join([t for t in texts if t]) or "(message)"
                                        level = params.get("level")
                                        if target:
                                            print(f"session {self.session_id} << message '{text}'", file=sys.stderr, flush=True)
                                            await self._broadcast_assistant(text, level, target)

                                frame = reset_frame()
                                continue

                            if line.startswith(":"):         # comment/heartbeat
                                continue
                            if line.startswith("event:"):
                                frame["event"] = line[len("event:"):].strip()
                                continue
                            if line.startswith("data:"):
                                frame["data_lines"].append(line[len("data:"):].lstrip())
                                continue
                            # ignore id:, retry:, etc.

            except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                print("[progress-listener] timeout:", exc, file=sys.stderr)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                print("[progress-listener] error:", exc, file=sys.stderr)

            print(f"[progress-listener] reconnecting in {backoff}s …", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


    async def progress_listener1(self) -> None:
        """
        SSE progress listener for the Streamable HTTP server.
        Keeps reconnecting with exponential backoff on disconnects.
        """

        print(f"[SSE] starting listener for session {self.session_id}", file=sys.stderr, flush=True)
        headers = {
            "Mcp-Session-Id": self.session_id,
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        transport = httpx.AsyncHTTPTransport(retries=0)
        backoff = 2
        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=transport, http2=False) as client:
                    async with client.stream("GET", self.mcp_endpoint, headers=headers) as resp:

                        async for raw_line in resp.aiter_lines():
                            print(f"[SSE] raw line: {raw_line}", file=sys.stderr, flush=True)
                            # Ignore comment lines or "event:" lines we don't care about
                            if raw_line.__contains__("ping") or raw_line == '' or raw_line == 'data: {}' or raw_line == 'event: open':
                                continue
                            
                            if not raw_line.startswith("data:"):
                                continue
                            try:
                                payload = raw_line[len("data:"):].lstrip()
                                root = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            
                            print(raw_line)
                            params = root.get("params", {}) or {}
                            method = root.get("method")
                            if method == "notifications/progress" and "params" in root:
                                pct = root["params"].get("progress")
                                token = root["params"].get("progressToken")
                                target = session_for_user(root.get("user_id"))
                                if isinstance(pct, (int, float)):
                                    print(f"session {self.session_id} << slow_count {pct:.0%}", file=sys.stderr, flush=True)
                                    #await publish_progress(target, token, float(pct))
                                    await self._broadcast_progress(float(pct), target, token)
                            elif method == "notifications/message" and "params" in root:
                                    # pull all text chunks
                                target = session_for_user(root.get("user_id"))
                                data = root["params"].get("data", [])
                                texts = [d.get("text") for d in data if isinstance(d, dict) and d.get("type") == "text"]
                                text = " ".join([t for t in texts if t]) or "(message)"
                                level = root["params"].get("level")
                                await self._broadcast_assistant(text, level, target)
                            continue

            except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                print("[progress-listener] timeout:", exc, file=sys.stderr)
            except asyncio.CancelledError:
                # Normal shutdown
                return
            except Exception as exc:
                print("[progress-listener] error:", exc, file=sys.stderr)

            # Reconnect with backoff
            print(f"[progress-listener] reconnecting in {backoff}s …", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


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
        self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        await self.session.send_ping()

        self._sse_task = asyncio.create_task(self.progress_listener())

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





