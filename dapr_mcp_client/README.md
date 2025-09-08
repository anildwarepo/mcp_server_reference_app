## FastAPI Server MCP Client

```bash
cd dapr_mcp_client
.venv\Scripts\activate
uvicorn dapr_mcp_client_fastapi:app --port 8080

cd autonomous_agents_dapr_mcp\dapr_mcp_client
uvicorn --app-dir .. dapr_mcp_client.dapr_mcp_client_fastapi:app --port 8080 --reload