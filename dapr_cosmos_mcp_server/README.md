## MCP Server running as Dapr Sidecar

### MCP Server support StreamableHttp

Make sure you have Dapr installed and initialized. You can run the following command to start the Dapr sidecar for your FastAPI application:

```bash
cd dapr_cosmos_mcp_server
dapr run --app-id cosmos_dapr_actor --dapr-http-port 3500 --app-port 3000 -- uvicorn --port 3000 mcp_fastapi_server:app