"""
Run:  uvicorn mcp_fastapi_server:app --port 3000 --reload
dapr: dapr run --app-id cosmos_dapr_actor --dapr-http-port 3500 --app-port 3000 -- uvicorn --port 3000 mcp_fastapi_server:app
"""

import asyncio, json, uuid, socket, os, inspect
from typing import Annotated, Optional
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import StreamingResponse, JSONResponse
from contextlib import asynccontextmanager
from dapr.ext.fastapi import DaprActor  
from dapr.actor import ActorProxy, ActorId
import json
from dotenv import load_dotenv
from datetime import timedelta
from .tools import REGISTERED_TOOLS, TOOL_FUNCS, tool
from .sse_bus import SESSIONS, sse_event, JSONRPC, publish_progress, publish_message
from .cosmosdb_helper import cosmosdb_create_item, ensure_container_exists, cosmosdb_query_items
from .task_manager_actor import TaskManagerActor  
from .backup_actor import BackupActor  
from .task_manager_actor_interface import TaskManagerActorInterface

load_dotenv()

POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "unknown")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await actor.register_actor(TaskManagerActor)
    await actor.register_actor(BackupActor)
    await ensure_container_exists()
    try:
        yield
    finally:
        pass

app = FastAPI(lifespan=lifespan)
actor = DaprActor(app)


# ───────────────── tools (unchanged API) ─────────────────────────────────────
@tool
async def create_backup_task(backup_task_details: Annotated[dict, "backup task parameters"]) -> Annotated[str, "create_item Result"]:
    """
    Creates an new item in the Cosmos DB container representing the backup task.
    backup_task_details is a Json object.
    """
    try:
        print("[create_backup_task] Creating backup task with details:", backup_task_details)
        try:
            backup_item_details = json.loads(backup_task_details)
        except json.JSONDecodeError:
            print("[create_backup_task] Invalid JSON format for backup_task_details")
            return "Can you provide a valid JSON string?"
        if(len(backup_item_details) > 0):
            item = backup_item_details[0]
        else:
            item = backup_item_details
        response = await cosmosdb_create_item(item)

        session_id =  backup_item_details[0]["user_id"]
        token = f"create_backup_task/{session_id}"
        await publish_progress(session_id, token, 1 / 5)
        await publish_message(session_id, f"From MCP Server: Creating backup task: step 1 of 5 (session {session_id})")
        print("[create_backup_task] Created item:", response)
        return "Backup task was created successfully"
    except Exception as e:
        print("[create_backup_task] Error creating backup task:", e)
        return "Error creating backup task"

@tool
async def query_backup_tasks(cosmosDbQuery: Annotated[str, "Cosmos DB SQL query that maps to user query"]) -> Annotated[list[dict], "query_backup_tasks Result"]:
    """
    Query backup tasks from the Cosmos DB container.

    backup Item schema:
        {{
        "user_id": "user id provided to you",
        "id": "<GUID>",
        "task": "Backup files",
        "files": ["x", "y", "z"],
        "servers": ["server 1", "server 2", "server 3"],
        "backup_frequency_path": "P1W"
        }}
    User the user_id provided to you to query the backup tasks. 
    """
    try:
        items = await cosmosdb_query_items(cosmosDbQuery)
        print("[query_backup_tasks] Queried items:", items)
        return items
    except Exception as e:
        print("[query_backup_tasks] Error querying items:", e)
        return []

@tool
async def setup_backup_task_agent(user_id: Annotated[str, "User ID for the backup task"]) -> Annotated[str, "setup_backup_task_agent Result"]:
    """
    Set up the backup task agent for the user.
    """
    try:
        #if session_id:
            # Remember which SSE session to use for this user
        #    associate_user_session(user_id, session_id)
        print("[setup_backup_task_agent] Setting up backup task agent for user:", user_id)
        proxy = ActorProxy.create('TaskManagerActor', ActorId(user_id), TaskManagerActorInterface)
        await proxy.SetReminder(True)
        session_id =  user_id
        token = f"Setup Backup Task Job/{session_id}"
        await publish_progress(session_id, token, 2 / 5)
        await publish_message(session_id, f"From MCP Server: Setting up backup task: step 2 of 5 (session {session_id})")
        return "Backup task agent set up successfully"
    except Exception as e:
        print("[setup_backup_task_agent] Error setting up backup task agent:", e)
        return "Error setting up backup task agent"


@tool
async def slow_count(n: Annotated[int, "The number to count to"], 
                    user_id: Annotated[str, "User ID for the slow count"],
                    session_id: Optional[str] = None) -> dict:
    """
    Runs a slow counting task as a health check for MCP Server. 
    It uses the current user id to maintain session awareness.
    It takes a number n as input provided by the user. 
    e.g slow count 5, slow count from 23
    """
    sid = session_id or user_id or "default"
    token = f"slow_count/{sid}"
    n = int(n)
    async def _runner():
        for i in range(1, n + 1):
            await publish_progress(sid, token, i / n)
            await publish_message(sid, f"slow_count: step {i} of {n} (session {sid})")
            print(f"[slow_count] step {i} of {n} (session {sid})", flush=True)
            await asyncio.sleep(1)

        await publish_progress(sid, token, 1.0)
        await publish_message(sid, f"slow_count done (n={n}, session {sid})")

    asyncio.create_task(_runner())
    return {"content": [{"type": "text", "text": f"Started slow_count({n}), token={token}"}]}



# ─────────────── call_tool wrapper ensures session_id injection ──────────────
async def call_tool(name: str, raw_args: dict, tasks: BackgroundTasks, session_id: str):

    print(f"[call_tool] {name} args={raw_args} session={session_id}", flush=True)

    if name not in TOOL_FUNCS:
        return "Error: Tool not found"

    fn  = TOOL_FUNCS[name]
    sig = inspect.signature(fn)
    args = dict(raw_args)
    if "session_id" in sig.parameters:
        args["session_id"] = session_id
    result = await fn(**args) if inspect.iscoroutinefunction(fn) else fn(**args)
    return result

def _ensure_calltool_result(obj):
    if isinstance(obj, dict) and "content" in obj:
        return obj
    return {"content": [{"type": "text", "text": str(obj)}]}

def _normalize_session_id(raw: str | None, default: str = "default") -> str:
    if not raw:
        return default
    return raw.split(",")[0].strip()

@app.get("/healthz")
async def healthz():
    # super-fast 200 OK for Dapr liveness/readiness
    return Response(status_code=200)


# ───────────────── SSE channel ───────────────────────────────────────────────

@app.get("/mcp")
async def mcp_sse(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    print(f"[@app.get(/mcp)] session={session_id} pod={POD} rev={REV}", flush=True)
    session = await SESSIONS.get_or_create(session_id)

    async def event_stream():
        #yield "event: message\ndata: {}\n\n"
        heartbeat_every = 1.0
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = session.q.get_nowait()
                client_msg = f"{msg}\n\n"
                yield client_msg
            except asyncio.QueueEmpty:
                yield "no message"
                await asyncio.sleep(heartbeat_every)

    return StreamingResponse(event_stream(), media_type="text/event-stream")



# ───────────────── health check ─────────────────────────────────────────────
@app.get("/status")
async def status(request: Request):
    return {"status": "ok"}

# ───────────────── JSON-RPC handler ──────────────────────────────────────────
@app.post("/mcp")
async def mcp_post(req: Request, tasks: BackgroundTasks):
    req_json   = await req.json()
    raw        = req.headers.get("Mcp-Session-Id")
    session_id = _normalize_session_id(raw, default=str(uuid.uuid4()))
    # ensure session exists for any tool that will stream
    await SESSIONS.get_or_create(session_id)

    method = req_json.get("method")
    rpc_id = req_json.get("id")
    print(f"[@app.post(/mcp) POST] method={method} session={session_id} pod={POD} rev={REV}", flush=True)

    match method:
        case "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "fastapi-mcp", "version": "0.1"},
                "capabilities": {"tools": {"listChanged": True, "callTool": True}}, #{"listTools": True, "toolCalling": True, "sse": True},
            }

        case "ping" | "$/ping":
            result = {} #{"pong": True}

        case "workspace/listTools" | "$/listTools" | "list_tools" | "tools/list":
            result = {"tools": REGISTERED_TOOLS}

        case "tools/call" | "$/call":
            tool_name = req_json["params"]["name"]
            raw_args  = req_json["params"].get("arguments", {})
            raw_out   = await call_tool(tool_name, raw_args, tasks, session_id)
            result    = _ensure_calltool_result(raw_out)

        case _ if method in TOOL_FUNCS:
            raw_args = req_json.get("params", {})
            raw_out  = await call_tool(method, raw_args, tasks, session_id)
            result   = _ensure_calltool_result(raw_out)

        case _:
            if rpc_id is None:
                return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
            return JSONResponse(
                content={"jsonrpc": JSONRPC, "id": rpc_id,
                         "error": {"code": -32601, "message": "method not found"}},
                headers={"Mcp-Session-Id": session_id},
                background=tasks,
            )

    return JSONResponse(
        content={"jsonrpc": JSONRPC, "id": rpc_id, "result": result},
        headers={"Mcp-Session-Id": session_id},
        background=tasks,
    )

# ───────────────── session cleanup ───────────────────────────────────────────
@app.delete("/mcp")
async def mcp_delete(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    if session_id:
        deleted = await SESSIONS.delete(session_id)
        return Response(status_code=204 if deleted else 404)
    return Response(status_code=404)
