"""OpenAPI MCP Server.

Serves tool-based access to large OpenAPI 3.x specs (e.g. ThingsBoard) so AI
coding agents can search endpoints, resolve $ref schemas inline, and read
auth/server metadata without loading the whole JSON into context.

Stdlib only; requires the `mcp` package to run as a server.
"""

import difflib
import json
import os
import sys

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("OpenAPI_Tools")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_REGISTRY = {
    "thingsboard": os.path.join(BASE_DIR, "thingsboard-api.json"),
}

HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
)

SCHEMA_REF_PREFIX = "#/components/schemas/"

DEFAULT_SEARCH_LIMIT = 30
MAX_SEARCH_LIMIT = 100
SUMMARY_TRUNC = 120


# ---------------------------------------------------------------------------
# Loading and indexing
# ---------------------------------------------------------------------------

def load_spec(filepath):
    """Load an OpenAPI JSON file; return the parsed dict or None on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.stderr.write(f"[Error] API file not found: {filepath}\n")
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"[Error] Invalid JSON in {filepath}: {exc}\n")
    return None


def iter_operations(spec):
    """Yield (path, method, op) for each HTTP-verb entry.

    Only HTTP verb keys are iterated, so path-level keys such as
    `parameters`, `summary`, or `description` are never mistaken for
    methods.
    """
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.lower() in HTTP_METHODS and isinstance(op, dict):
                yield path, method.lower(), op


def build_indexes(spec):
    """Precompute search indexes for one loaded spec."""
    ops = []
    by_tag = {}
    by_operation_id = {}
    for path, method, op in iter_operations(spec):
        param_names = [
            p.get("name", "")
            for p in op.get("parameters") or []
            if isinstance(p, dict)
        ]
        record = {
            "path": path,
            "method": method,
            "summary": op.get("summary") or "",
            "description": op.get("description") or "",
            "operation_id": op.get("operationId") or "",
            "tags": op.get("tags") or [],
            "deprecated": bool(op.get("deprecated")),
            "param_names": [n for n in param_names if n],
            "op": op,
        }
        ops.append(record)
        for tag in record["tags"]:
            by_tag.setdefault(tag, []).append(record)
        if record["operation_id"]:
            by_operation_id[record["operation_id"]] = record
    schemas = (spec.get("components") or {}).get("schemas") or {}
    return {
        "spec": spec,
        "ops": ops,
        "by_tag": by_tag,
        "by_operation_id": by_operation_id,
        "schemas": schemas,
    }


API_INDEXES = {}
for _name, _filepath in API_REGISTRY.items():
    _spec = load_spec(_filepath)
    if _spec is not None:
        API_INDEXES[_name] = build_indexes(_spec)


def _index_for(api_name):
    """Return (index, None) or (None, error message) for an api_name."""
    index = API_INDEXES.get(api_name)
    if index is None:
        available = ", ".join(sorted(API_INDEXES)) or "(none)"
        return None, (
            f"Unknown API '{api_name}'. Available APIs: {available}. "
            "Call list_available_apis() first."
        )
    return index, None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _truncate(text, limit=SUMMARY_TRUNC):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_op_line(record):
    parts = [f"{record['method'].upper()} {record['path']}"]
    summary = record["summary"] or record["description"]
    operation_id = record["operation_id"]
    if summary:
        # Specs often embed the operationId as "(...)" at the end of the
        # summary; drop it there since we print it separately.
        if operation_id and summary.endswith(f"({operation_id})"):
            summary = summary[: -len(operation_id) - 2].rstrip()
        parts.append("— " + _truncate(summary, SUMMARY_TRUNC))
    if operation_id:
        parts.append(f"({operation_id})")
    if record["tags"]:
        parts.append("[" + ", ".join(record["tags"]) + "]")
    if record["deprecated"]:
        parts.append("[DEPRECATED]")
    return " ".join(parts)


def _paginate(matches, offset, limit):
    """Return (header, page_lines) for a list of pre-formatted lines."""
    total = len(matches)
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))
    if offset >= total:
        return None, f"offset={offset} is out of range: {total} results (use offset 0–{max(total - 1, 0)})."
    end = min(offset + limit, total)
    header = f"{total} matches, showing {offset + 1}–{end}"
    if end < total:
        header += f" — more available, pass offset={end}"
    return header, "\n".join(matches[offset:end])


# ---------------------------------------------------------------------------
# Endpoint search (pure)
# ---------------------------------------------------------------------------

def _op_match_score(record, tokens):
    """Rank an operation against lowercase tokens (AND across tokens).

    Returns None when not every token matches somewhere. Rank (lower is
    better): 1 summary, 2 operationId, 3 path, 4 description/tags/params.
    """
    summary = record["summary"].lower()
    operation_id = record["operation_id"].lower()
    path = record["path"].lower()
    haystacks = (
        summary,
        operation_id,
        path,
        record["description"].lower(),
        " ".join(record["tags"]).lower(),
        " ".join(record["param_names"]).lower(),
    )
    for token in tokens:
        if not any(token in h for h in haystacks):
            return None
    if all(token in summary for token in tokens):
        return 1
    if all(token in operation_id for token in tokens):
        return 2
    if all(token in path for token in tokens):
        return 3
    return 4


def _search_endpoints(
    index,
    query,
    tag=None,
    include_deprecated=False,
    offset=0,
    limit=DEFAULT_SEARCH_LIMIT,
):
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return "Empty query: provide at least one keyword."

    candidates = index["ops"]
    if tag:
        tag_lookup = {t.lower(): t for t in index["by_tag"]}
        actual = tag_lookup.get(tag.lower())
        if actual is None:
            message = f"Unknown tag '{tag}'."
            close = difflib.get_close_matches(tag.lower(), tag_lookup, n=3)
            if close:
                message += " Did you mean: " + ", ".join(tag_lookup[c] for c in close) + "?"
            message += "\nAvailable tags: " + ", ".join(sorted(index["by_tag"]))
            return message
        candidates = index["by_tag"][actual]

    scored = []
    for record in candidates:
        if record["deprecated"] and not include_deprecated:
            continue
        score = _op_match_score(record, tokens)
        if score is not None:
            scored.append((score, record["path"], record["method"], record))
    if not scored:
        qualifier = f" under tag '{tag}'" if tag else ""
        return f"No endpoints found matching '{query}'{qualifier}."

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    lines = [_format_op_line(record) for _, _, _, record in scored]
    header, page = _paginate(lines, offset, limit)
    return page if header is None else header + "\n" + page


# ---------------------------------------------------------------------------
# $ref resolution (pure)
# ---------------------------------------------------------------------------

def _strip_inline_mapping(schema):
    """Drop discriminator.mapping from a schema inlined for a $ref.

    The mapping table (e.g. ThingsBoard's 36-entry EntityId map) repeats
    for every inlined Id field and costs far more tokens than the
    information is worth; propertyName is kept. A schema fetched directly
    via get_schema keeps its own mapping.
    """
    if not isinstance(schema, dict):
        return schema
    discriminator = schema.get("discriminator")
    if isinstance(discriminator, dict) and "mapping" in discriminator:
        trimmed = dict(schema)
        trimmed["discriminator"] = {
            k: v for k, v in discriminator.items() if k != "mapping"
        }
        return trimmed
    return schema


def _resolve_refs(node, schemas, max_depth=3, _depth=0, _stack=()):
    """Inline-expand '#/components/schemas/...' refs up to max_depth levels.

    Cycles are cut (a ref already expanded on the current branch is kept as
    a marker), and refs beyond max_depth are left as {"$ref": ...} markers
    so callers can drill down with get_schema. discriminator.mapping is
    stripped from inlined schemas to avoid repeating large polymorphic
    lookup tables. Other ref targets (e.g. Swagger 2.0 '#/definitions/...',
    '#/components/responses/...') are not expanded.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref.startswith(SCHEMA_REF_PREFIX):
                name = ref[len(SCHEMA_REF_PREFIX):]
                if (
                    name in schemas
                    and _depth < max_depth
                    and name not in _stack
                ):
                    expanded = _resolve_refs(
                        schemas[name], schemas, max_depth,
                        _depth + 1, _stack + (name,),
                    )
                    return _strip_inline_mapping(expanded)
            return {"$ref": ref}
        return {
            key: _resolve_refs(value, schemas, max_depth, _depth, _stack)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [
            _resolve_refs(item, schemas, max_depth, _depth, _stack)
            for item in node
        ]
    return node


# ---------------------------------------------------------------------------
# Endpoint lookup (pure)
# ---------------------------------------------------------------------------

def _normalize_path(path):
    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path


def _find_operation(index, path_or_operation_id, method=""):
    """Locate an operation by path (+ method) or by operationId alone.

    Returns (record, None) on success or (None, error message).
    """
    key = (path_or_operation_id or "").strip()
    if not key:
        return None, "No path or operationId provided."
    method = (method or "").strip().lower()

    record = index["by_operation_id"].get(key)
    if record is not None:
        if method and record["method"] != method:
            return None, (
                f"Operation '{key}' exists but its method is "
                f"{record['method'].upper()} (got {method.upper()})."
            )
        return record, None

    path = _normalize_path(key)
    matches = [r for r in index["ops"] if r["path"] == path]
    if matches:
        if method:
            for record in matches:
                if record["method"] == method:
                    return record, None
            verbs = ", ".join(sorted(r["method"].upper() for r in matches))
            return None, (
                f"{method.upper()} {path} not found. "
                f"Available methods for this path: {verbs}."
            )
        if len(matches) == 1:
            return matches[0], None
        verbs = ", ".join(sorted(r["method"].upper() for r in matches))
        return None, (
            f"Path {path} has multiple operations ({verbs}); "
            "also pass a method."
        )

    paths = {r["path"] for r in index["ops"]}
    close_paths = difflib.get_close_matches(path, paths, n=5, cutoff=0.6)
    close_opids = difflib.get_close_matches(
        key, index["by_operation_id"], n=5, cutoff=0.6
    )
    message = f"Endpoint not found: {(method.upper() + ' ') if method else ''}{key}"
    if close_paths:
        message += "\nClosest paths:\n  " + "\n  ".join(close_paths)
    if close_opids:
        message += "\nClosest operationIds:\n  " + "\n  ".join(close_opids)
    message += (
        "\nHint: pass an operationId (e.g. 'saveDevice') as the path argument "
        "to look an operation up directly."
    )
    return None, message


def _operation_view(op, include_error_responses):
    """Project an operation onto the token-friendly fields we return."""
    view = {}
    for key in ("summary", "description", "operationId", "tags", "deprecated"):
        if key in op and op[key] is not None:
            view[key] = op[key]
    for key in ("parameters", "requestBody", "security"):
        if key in op and op[key] is not None:
            view[key] = op[key]
    responses = {}
    for code, resp in (op.get("responses") or {}).items():
        if include_error_responses or str(code).startswith("2"):
            responses[code] = resp
    if responses:
        view["responses"] = responses
    return view


def _get_endpoint_details(
    index,
    path,
    method="",
    resolve_refs=True,
    max_depth=3,
    include_error_responses=False,
):
    record, error = _find_operation(index, path, method)
    if error:
        return error
    view = _operation_view(record["op"], include_error_responses)
    if resolve_refs:
        view = _resolve_refs(view, index["schemas"], max_depth)
    body = json.dumps(view, indent=2, ensure_ascii=False)
    return f"{record['method'].upper()} {record['path']}\n{body}"


# ---------------------------------------------------------------------------
# Schema tools (pure)
# ---------------------------------------------------------------------------

def _get_schema(index, name, resolve_refs=True, max_depth=3):
    schemas = index["schemas"]
    if name not in schemas:
        message = f"Schema '{name}' not found."
        close = difflib.get_close_matches(name, schemas, n=5, cutoff=0.5)
        if close:
            message += " Closest schema names: " + ", ".join(close)
        return message
    schema = schemas[name]
    if resolve_refs:
        schema = _resolve_refs(schema, schemas, max_depth)
    return json.dumps(schema, indent=2, ensure_ascii=False)


def _search_schemas(index, query, offset=0, limit=DEFAULT_SEARCH_LIMIT):
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return "Empty query: provide at least one keyword."
    lines = []
    for name in sorted(index["schemas"]):
        lowered = name.lower()
        if not all(t in lowered for t in tokens):
            continue
        schema = index["schemas"][name]
        type_ = schema.get("type", "")
        label = f"{name} ({type_})" if type_ else name
        detail = schema.get("title") or schema.get("description") or ""
        if detail:
            label += " — " + _truncate(detail, 100)
        lines.append(label)
    if not lines:
        return f"No schemas found matching '{query}'."
    header, page = _paginate(lines, offset, limit)
    return page if header is None else header + "\n" + page


# ---------------------------------------------------------------------------
# Overview / listing (pure)
# ---------------------------------------------------------------------------

def _format_security_scheme(name, scheme):
    type_ = scheme.get("type", "?")
    details = []
    if scheme.get("name"):
        location = scheme.get("in", "?")
        details.append(f"{location} '{scheme['name']}'")
    if scheme.get("scheme"):
        details.append(f"scheme '{scheme['scheme']}'")
    if scheme.get("bearerFormat"):
        details.append(f"format: {scheme['bearerFormat']}")
    line = f"- {name} [{type_}"
    if details:
        line += " — " + ", ".join(details)
    line += "]"
    description = scheme.get("description")
    if description:
        line += "\n  " + _truncate(description, 300)
    return line


def _get_api_overview(index):
    spec = index["spec"]
    info = spec.get("info") or {}
    lines = [
        f"Title: {info.get('title', '?')}",
        f"Version: {info.get('version', '?')}",
    ]
    servers = [s.get("url") for s in spec.get("servers") or [] if s.get("url")]
    lines.append("Servers: " + (", ".join(servers) if servers else "(none)"))

    schemes = (spec.get("components") or {}).get("securitySchemes") or {}
    if schemes:
        lines.append("")
        lines.append("Authentication:")
        lines.extend(_format_security_scheme(n, s) for n, s in schemes.items())

    if index["by_tag"]:
        lines.append("")
        lines.append(f"Tags ({len(index['by_tag'])}):")
        for tag in sorted(index["by_tag"], key=str.lower):
            lines.append(f"  {tag} ({len(index['by_tag'][tag])})")
    return "\n".join(lines)


def _list_available_apis():
    if not API_INDEXES:
        return "No APIs loaded. Check the API_REGISTRY file paths in mcp_openapi_server.py."
    lines = []
    for name in sorted(API_INDEXES):
        index = API_INDEXES[name]
        info = index["spec"].get("info") or {}
        servers = [
            s.get("url") for s in index["spec"].get("servers") or [] if s.get("url")
        ]
        line = (
            f"{name}: {info.get('title', '?')} v{info.get('version', '?')} — "
            f"{len(index['ops'])} operations, {len(index['by_tag'])} tags, "
            f"{len(index['schemas'])} schemas"
        )
        if servers:
            line += "; servers: " + ", ".join(servers)
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP tools (thin wrappers)
# ---------------------------------------------------------------------------

@mcp.tool()
def list_available_apis() -> str:
    """List all available API systems documented in this server."""
    return _list_available_apis()


@mcp.tool()
def get_api_overview(api_name: str) -> str:
    """Get an API's overview: title, version, server URLs, authentication
    schemes, and all tags with operation counts (usable as a table of
    contents)."""
    index, error = _index_for(api_name)
    return error if error else _get_api_overview(index)


@mcp.tool()
def search_endpoints(
    api_name: str,
    query: str,
    tag: str = "",
    include_deprecated: bool = False,
    offset: int = 0,
    limit: int = 30,
) -> str:
    """Search endpoints. The query is split on whitespace into keywords; every
    keyword must match (AND, case-insensitive) in path, summary, description,
    operationId, tag names, or parameter names. Returns one line per endpoint
    with pagination; deprecated endpoints are excluded by default."""
    index, error = _index_for(api_name)
    if error:
        return error
    return _search_endpoints(
        index, query,
        tag=tag or None,
        include_deprecated=include_deprecated,
        offset=max(0, offset),
        limit=limit,
    )


@mcp.tool()
def get_endpoint_details(
    api_name: str,
    path: str,
    method: str = "",
    resolve_refs: bool = True,
    max_depth: int = 3,
    include_error_responses: bool = False,
) -> str:
    """Get one endpoint's parameters, request body, security, and 2xx
    responses. `path` also accepts an operationId (e.g. 'saveDevice'); the
    leading slash is optional. $refs to component schemas are inlined up to
    max_depth; deeper/cyclic refs stay as {"$ref": ...} markers you can
    expand with get_schema. Set include_error_responses=True to include
    4xx/5xx response schemas."""
    index, error = _index_for(api_name)
    if error:
        return error
    return _get_endpoint_details(
        index, path,
        method=method,
        resolve_refs=resolve_refs,
        max_depth=max(0, max_depth),
        include_error_responses=include_error_responses,
    )


@mcp.tool()
def get_schema(
    api_name: str,
    name: str,
    resolve_refs: bool = True,
    max_depth: int = 3,
) -> str:
    """Get a component schema by exact name (e.g. 'Device') with nested
    $refs inlined up to max_depth (cycles are cut, deeper refs stay as
    {"$ref": ...} markers). Returns close name suggestions when not found."""
    index, error = _index_for(api_name)
    if error:
        return error
    return _get_schema(index, name, resolve_refs=resolve_refs, max_depth=max(0, max_depth))


@mcp.tool()
def search_schemas(api_name: str, query: str, offset: int = 0, limit: int = 30) -> str:
    """Search component schemas by name (multi-keyword AND,
    case-insensitive). Returns 'name (type) — description' summary lines;
    use get_schema to fetch the full definition."""
    index, error = _index_for(api_name)
    if error:
        return error
    return _search_schemas(index, query, offset=max(0, offset), limit=limit)


if __name__ == "__main__":
    mcp.run()
