"""Tests for mcp_openapi_server pure functions and tool wrappers.

Most cases run against the real ThingsBoard spec when it is present
(thingsboard-api.json is gitignored); they are skipped otherwise. Edge cases
(ref cycles, path-level keys, description-only matching) use a synthetic
mini spec so they always run.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp_openapi_server as server  # noqa: E402


MINI_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Mini API", "version": "1.0.0"},
    "servers": [{"url": "https://mini.example.com"}],
    "paths": {
        "/api/widget": {
            # Path-level keys must never be treated as HTTP methods.
            "parameters": [{"name": "globalParam", "in": "query"}],
            "summary": "Widget collection",
            "post": {
                "summary": "Create widget",
                "operationId": "createWidget",
                "tags": ["widgets"],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Widget"}
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            },
            "get": {
                "summary": "List widgets",
                "operationId": "listWidgets",
                "tags": ["widgets"],
                "responses": {"200": {"description": "OK"}},
            },
        },
        "/api/widget/rotate": {
            "put": {
                # No summary: only findable via description.
                "description": "Rotate the widget by the given angle.",
                "operationId": "rotateWidget",
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
    "components": {
        "schemas": {
            "Widget": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "child": {"$ref": "#/components/schemas/Node"},
                    "shape": {"$ref": "#/components/schemas/Shape"},
                },
            },
            # Self-referencing schema for cycle protection.
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/components/schemas/Node"}},
            },
            # Polymorphic schema: mapping must be stripped when inlined,
            # but kept when fetched directly.
            "Shape": {
                "type": "object",
                "discriminator": {
                    "propertyName": "shapeType",
                    "mapping": {
                        "CIRCLE": "#/components/schemas/Shape",
                        "SQUARE": "#/components/schemas/Shape",
                    },
                },
                "properties": {"shapeType": {"type": "string"}},
            },
        },
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "name": "X-API-Key",
                "in": "header",
                "description": "Send header: X-API-Key <your key>",
            }
        },
    },
}


@pytest.fixture()
def mini():
    return server.build_indexes(MINI_SPEC)


@pytest.fixture(scope="session")
def tb():
    if "thingsboard" not in server.API_INDEXES:
        pytest.skip("thingsboard-api.json not present")
    return server.API_INDEXES["thingsboard"]


def details_json(output):
    """get_endpoint_details output = 'METHOD /path' line + JSON body."""
    assert "\n" in output
    return json.loads(output.split("\n", 1)[1])


# --- Searching ---------------------------------------------------------------

def test_search_create_device_finds_post_api_device(tb):
    result = server._search_endpoints(tb, "create device")
    assert "POST /api/device" in result


def test_search_device_shows_total_and_paginates(tb):
    result = server._search_endpoints(tb, "device")
    match = re.match(r"(\d+) matches, showing (\d+)–(\d+)", result)
    assert match, result
    total, start, end = map(int, match.groups())
    assert total > 50
    assert (start, end) == (1, 30)
    assert f"offset={end}" in result

    page2 = server._search_endpoints(tb, "device", offset=30)
    assert re.match(rf"{total} matches, showing 31–60", page2)


def test_description_only_operation_is_searchable(mini):
    result = server._search_endpoints(mini, "rotate")
    assert "PUT /api/widget/rotate" in result


def test_multi_token_and_semantics(mini):
    assert "POST /api/widget" in server._search_endpoints(mini, "create widget")
    assert "No endpoints found" in server._search_endpoints(mini, "create nonexistent")


def test_search_by_tag(mini):
    result = server._search_endpoints(mini, "widget", tag="widgets")
    assert "POST /api/widget" in result and "GET /api/widget" in result
    assert "Unknown tag" in server._search_endpoints(mini, "widget", tag="nope")


def test_search_offset_out_of_range(mini):
    result = server._search_endpoints(mini, "widget", offset=99)
    assert "out of range" in result


# --- Endpoint details & ref resolution ----------------------------------------

def test_endpoint_details_inlines_device_schema(tb):
    result = server._get_endpoint_details(tb, "/api/device", "post")
    body = details_json(result)
    schema = body["requestBody"]["content"]["application/json"]["schema"]
    assert "properties" in schema
    assert "name" in schema["properties"]


def test_endpoint_details_excludes_errors_by_default(tb):
    body = details_json(server._get_endpoint_details(tb, "/api/device", "post"))
    codes = set(body["responses"])
    assert codes and all(c.startswith("2") for c in codes)
    body_all = details_json(
        server._get_endpoint_details(tb, "/api/device", "post", include_error_responses=True)
    )
    assert "400" in body_all["responses"] and "401" in body_all["responses"]


def test_endpoint_details_accepts_operation_id_and_loose_path(tb):
    assert server._get_endpoint_details(tb, "saveDevice").startswith("POST /api/device")
    assert server._get_endpoint_details(tb, "api/device/", "post").startswith("POST /api/device")


def test_endpoint_details_max_depth_keeps_ref_marker(tb):
    body = details_json(
        server._get_endpoint_details(tb, "/api/device", "post", max_depth=0)
    )
    schema = body["requestBody"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/Device"}


def test_resolve_refs_cycle_terminates(mini):
    resolved = server._resolve_refs(
        MINI_SPEC["components"]["schemas"]["Node"],
        MINI_SPEC["components"]["schemas"],
        max_depth=3,
    )
    # The direct self-reference is expanded once, then cut by cycle protection.
    first = resolved["properties"]["child"]
    assert "properties" in first
    assert first["properties"]["child"] == {"$ref": "#/components/schemas/Node"}


def test_inline_ref_strips_discriminator_mapping(mini):
    resolved = json.loads(server._get_schema(mini, "Widget"))
    shape = resolved["properties"]["shape"]
    assert "discriminator" in shape
    assert "mapping" not in shape["discriminator"]
    assert shape["discriminator"]["propertyName"] == "shapeType"


def test_direct_schema_lookup_keeps_mapping(mini):
    shape = json.loads(server._get_schema(mini, "Shape"))
    assert "mapping" in shape["discriminator"]


def test_saveDevice_output_has_no_mapping_baggage(tb):
    result = server._get_endpoint_details(tb, "saveDevice")
    assert '"mapping"' not in result
    assert '"propertyName"' in result


def test_unknown_path_returns_suggestions(tb):
    result = server._get_endpoint_details(tb, "/api/devic", "post")
    assert "Endpoint not found" in result
    assert "/api/device" in result


def test_wrong_method_lists_available(tb):
    result = server._get_endpoint_details(tb, "/api/device/{deviceId}", "put")
    assert "not found" in result
    assert "GET" in result and "DELETE" in result


def test_path_level_parameters_key_not_a_method(mini):
    methods = {(r["path"], r["method"]) for r in mini["ops"]}
    assert ("/api/widget", "post") in methods
    assert ("/api/widget", "get") in methods
    assert all(m != "parameters" for _, m in methods)


# --- Schemas -------------------------------------------------------------------

def test_get_schema_enum(tb):
    schema = json.loads(server._get_schema(tb, "NameConflictPolicy"))
    assert schema["enum"] == ["FAIL", "UNIQUIFY"]


def test_get_schema_suggests_close_names(tb):
    result = server._get_schema(tb, "DeviceProfil")
    assert "not found" in result and "DeviceProfile" in result


def test_search_schemas_multi_token(tb):
    result = server._search_schemas(tb, "device profile")
    assert "DeviceProfile" in result


# --- Deprecated ------------------------------------------------------------------

def test_deprecated_hidden_by_default(tb):
    default = server._search_endpoints(tb, "deviceTypes")
    assert "getDeviceTypes" not in default
    included = server._search_endpoints(tb, "deviceTypes", include_deprecated=True)
    assert "getDeviceTypes" in included and "[DEPRECATED]" in included


# --- Overview / listing / registry ------------------------------------------------

def test_api_overview(tb):
    result = server._get_api_overview(tb)
    assert "ThingsBoard REST API" in result
    assert "https://ioter3.mpiot.com.hk" in result
    assert "Authentication:" in result
    assert "HttpLoginForm" in result and "ApiKeyForm" in result
    assert "device-controller" in result


def test_overview_minimal_spec(mini):
    result = server._get_api_overview(mini)
    assert "Mini API" in result and "https://mini.example.com" in result
    assert "X-API-Key" in result


def test_list_available_apis(tb):
    result = server._list_available_apis()
    assert "thingsboard" in result
    assert "ThingsBoard REST API" in result
    assert "https://ioter3.mpiot.com.hk" in result


def test_unknown_api_error():
    result = server.search_endpoints(api_name="nonexistent", query="device")
    assert "Unknown API 'nonexistent'" in result
    assert server.get_schema(api_name="nonexistent", name="Device").startswith("Unknown API")
