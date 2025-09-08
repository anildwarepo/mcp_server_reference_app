## MCP Server running as Dapr Sidecar

### MCP Server support StreamableHttp

Make sure you have Dapr installed and initialized. You can run the following command to start the Dapr sidecar for your FastAPI application:

Make sure you can connect to Cosmos DB over Private Endpoint. Run VPN Client.

```bash
cd dapr_cosmos_mcp_server
.venv\Scripts\activate
dapr run --app-id cosmos_dapr_actor --dapr-http-port 3500 --app-port 3000 -- uvicorn --port 3000 mcp_fastapi_server:app

cd autonomous_agents_dapr_mcp\dapr_cosmos_mcp_server
dapr run --app-id cosmos_dapr_actor --dapr-http-port 3500 --app-port 3000 -- uvicorn --app-dir .. dapr_cosmos_mcp_server.mcp_fastapi_server:app --port 3000 
