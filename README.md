# OpenAPI MCP Server

This project is a Python server based on the Model Context Protocol (MCP). Its main purpose is to let AI Coding Agents (such as Cursor, Windsurf, and Claude Desktop) dynamically and precisely retrieve large OpenAPI specification files (e.g., ThingsBoard API) without stuffing the entire JSON into the conversation. This drastically reduces token consumption and prevents the Agent from hallucinating.

## ✨ Key Features

* **Multi-system support**: Manage multiple API documents (e.g., ThingsBoard, ERP, Payment API) simultaneously through a single MCP server.
* **Precise retrieval**: The Agent searches for endpoints via tools and only fetches the schemas it needs, enabling zero-overhead API integration.
* **Low latency and low cost**: Avoids transferring MB-sized API documents with every prompt.

---

## 🛠️ Installation and Environment Setup

1. **System requirements**: Make sure Python 3.10 or later is installed.
2. **Install dependencies**:
Run the following command in your terminal to install the official MCP SDK:
```bash
pip install mcp

```


3. **Place API documents**:
Put your OpenAPI spec file (e.g., `thingsboard-api.json`) in the same directory as `mcp_openapi_server.py`. API spec files are not included in this repository — `*-api.json` is ignored by `.gitignore`.

---

## ⚙️ Server Configuration

Open `mcp_openapi_server.py` and add or remove API document mappings in the `API_REGISTRY` dictionary at any time:

```python
API_REGISTRY = {
    "thingsboard": "thingsboard-api.json",
    "my_other_api": "other-api.json"
}

```

---

## 🚀 Hooking Up to a Coding Agent

This server runs in `stdio` mode, making it easy to integrate with mainstream AI IDEs.

### Setup in Cursor

1. Open Cursor Settings > **Features** > **MCP Servers**.
2. Click **+ Add New MCP Server**.
3. Fill in the following information:
* **Name**: `MultiAPI_MCP`
* **Type**: `command`
* **Command**: `python /absolute/path/to/this/project/mcp_openapi_server.py`


4. Save and confirm the server shows a green light.

### Setup in Windsurf

1. Edit the `~/.codeium/windsurf/mcp_config.json` file.
2. Add the following configuration:
```json
{
  "mcpServers": {
    "MultiAPI_MCP": {
      "command": "python",
      "args": ["/absolute/path/to/this/project/mcp_openapi_server.py"]
    }
  }
}

```


3. Restart Windsurf.

---

## 💡 How to Interact with the Agent

Once configured, you can give commands like the following directly in the chat:

> "Check the ThingsBoard API documentation and write me a Python script to add a new device."

The Agent will automatically invoke the following tools to handle it:

* `list_available_apis`: Check which API systems are currently available.
* `search_endpoints`: Find relevant routes by keyword.
* `get_endpoint_details`: Get the detailed Request/Response structure of a specific route.

---

This README covers the most essential deployment and usage information.
