import json
import os
import sys
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("ThingsBoard_OpenAPI_Server")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_REGISTRY = {
    "thingsboard": os.path.join(BASE_DIR, "thingsboard-api.json")
}

loaded_apis = {}
for name, filepath in API_REGISTRY.items():
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            loaded_apis[name] = json.load(f)
    except FileNotFoundError:
        sys.stderr.write(f"[Error] API File not found: {filepath}\n")
        loaded_apis[name] = {"paths": {}}

@mcp.tool()
def list_available_apis() -> str:
    """List all available API systems documented in this server."""
    return "Available APIs: " + ", ".join(API_REGISTRY.keys())

@mcp.tool()
def search_endpoints(api_name: str, keyword: str) -> str:
    """Search for API endpoints by keyword (searches both URL path and summary)."""
    if api_name not in loaded_apis:
        return f"API '{api_name}' not found. Please check list_available_apis()."
    
    results = []
    paths = loaded_apis[api_name].get("paths", {})
    keyword_lower = keyword.lower()
    
    for path, methods in paths.items():
        for method, details in methods.items():
            summary = details.get("summary", "")
            if keyword_lower in path.lower() or keyword_lower in summary.lower():
                results.append(f"{method.upper()} {path} - {summary}")
    
    if not results:
        return f"No endpoints found matching '{keyword}' in {api_name}."
    return "\n".join(results[:50])

@mcp.tool()
def get_endpoint_details(api_name: str, path: str, method: str) -> str:
    """Get the full schema details (parameters, request body, responses) for a specific endpoint."""
    if api_name not in loaded_apis:
         return f"API '{api_name}' not found."
         
    methods = loaded_apis[api_name].get("paths", {}).get(path, {})
    details = methods.get(method.lower())
    
    if not details:
        return f"Endpoint {method.upper()} {path} not found."
    
    return json.dumps(details, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()