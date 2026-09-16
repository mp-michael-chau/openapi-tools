# OpenAPI MCP Server

This project is a Python server based on the Model Context Protocol (MCP). Its main purpose is to let AI Coding Agents (such as Cursor, Windsurf, and Claude Desktop) dynamically and precisely retrieve large OpenAPI specification files (e.g., ThingsBoard API) without stuffing the entire JSON into the conversation. This drastically reduces token consumption and prevents the Agent from hallucinating.

## ✨ Key Features

* **Multi-system support**: Manage multiple API documents (e.g., ThingsBoard, ERP, Payment API) simultaneously through a single MCP server.
* **Precise retrieval**: Multi-keyword AND search across paths, summaries, descriptions, operationIds, tags, and parameter names — with result counts and pagination, never silent truncation.
* **Inline `$ref` resolution**: Component schema references in request/response bodies are expanded in place (depth-limited, cycle-safe), so the agent sees actual fields instead of opaque `$ref` pointers. Use `get_schema` to drill deeper.
* **Auth & server metadata**: Server base URLs and `securitySchemes` (JWT header format, API-key prefix format, …) are exposed through `get_api_overview`, so generated clients authenticate correctly.
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

To get the ThingsBoard spec, download it from a running ThingsBoard instance (the SpringDoc endpoint):

```bash
# From the public ThingsBoard demo server
curl -o thingsboard-api.json https://demo.thingsboard.io/v3/api-docs

# Or from your own instance
curl -o thingsboard-api.json https://<your-thingsboard-host>/v3/api-docs
```

You can also browse the interactive docs of your instance at `https://<your-thingsboard-host>/swagger-ui`.

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

The Agent combines the tools below, typically in this order:

1. `list_available_apis()` — see which API systems are loaded (name, title, version, server URLs, operation/tag/schema counts).
2. `get_api_overview(api_name)` — table of contents: title, version, server URLs, **authentication schemes** (e.g. JWT via `X-Authorization: Bearer …`, or `ApiKey <value>` prefix format), and all tags with operation counts.
3. `search_endpoints(api_name, query, tag?, include_deprecated?, offset?, limit?)` — multi-keyword AND search (case-insensitive) over path, summary, description, operationId, tags, and parameter names. Each result line looks like:

   ```
   POST /api/device — Create Or Update Device (saveDevice) [device-controller]
   ```

   The first line shows the total match count and the visible range (e.g. `137 matches, showing 1–30 — more available, pass offset=30`), so nothing is silently truncated. Deprecated endpoints are excluded unless `include_deprecated=True`.
4. `get_endpoint_details(api_name, path, method?, resolve_refs?, max_depth?, include_error_responses?)` — parameters, request body, security, and (by default) only 2xx responses for one operation. `path` also accepts an **operationId** (e.g. `saveDevice`) and the leading slash is optional; unknown paths get close-match suggestions. `$ref`s to component schemas are inlined up to `max_depth` (default 3); deeper or cyclic refs stay as `{"$ref": "..."}` markers. When a schema is inlined for a ref, its `discriminator.mapping` table is stripped (only `propertyName` is kept) so large polymorphic lookup tables are not repeated per field; fetch the schema directly with `get_schema` to see its mapping.
5. `get_schema(api_name, name, resolve_refs?, max_depth?)` — fetch a component schema by exact name (e.g. `Device`, `NameConflictPolicy`) with the same depth-limited, cycle-safe ref expansion; unknown names get suggestions. The schema's own `discriminator.mapping` (if any) is preserved.
6. `search_schemas(api_name, query)` — find schemas by name (multi-keyword AND) and get `name (type) — description` summary lines to jump into `get_schema`.

Example: to write the "add a device" script, the agent runs `list_available_apis` → `get_api_overview` (auth + base URL) → `search_endpoints("thingsboard", "create device")` → `get_endpoint_details("thingsboard", "saveDevice")` (request body shows the expanded `Device` fields) — and it has everything it needs without ever loading the raw JSON.

---

## 🧪 Running the Tests

The project venv is managed with [uv](https://docs.astral.sh/uv/). Install the dev dependency and run the suite:

```bash
uv pip install -r requirements-dev.txt
uv run pytest tests/
```

No install at all? One-off run: `uv run --with pytest pytest tests/`.

Tests run against the real spec when `thingsboard-api.json` is present (skipped otherwise) plus a synthetic mini-spec for edge cases such as self-referencing schemas.

---

## ⚠️ Limitations

* **OpenAPI 3.x only.** `$ref` resolution covers `#/components/schemas/...`. Swagger 2.0 specs (`#/definitions/...`) load and search fine, but refs are not expanded.
* Registering a spec only requires it to be valid JSON with a `paths` object; other fields (servers, securitySchemes, components) are surfaced when present.

---

This README covers the most essential deployment and usage information.
