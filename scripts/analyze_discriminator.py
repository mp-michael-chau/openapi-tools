import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp_openapi_server as server


def strip_key(node, key):
    if isinstance(node, dict):
        return {k: strip_key(v, key) for k, v in node.items() if k != key}
    if isinstance(node, list):
        return [strip_key(x, key) for x in node]
    return node


def count_key(node, key):
    if isinstance(node, dict):
        return (1 if key in node else 0) + sum(count_key(v, key) for v in node.values())
    if isinstance(node, list):
        return sum(count_key(x, key) for x in node)
    return 0


def mapping_sizes(node, acc):
    if isinstance(node, dict):
        if "mapping" in node and isinstance(node["mapping"], dict):
            acc.append(len(node["mapping"]))
        for v in node.values():
            mapping_sizes(v, acc)
    elif isinstance(node, list):
        for x in node:
            mapping_sizes(x, acc)
    return acc


idx = server.API_INDEXES["thingsboard"]
schemas = idx["schemas"]
affected = [n for n in schemas if count_key(schemas[n], "mapping") > 0]
print(f"schemas with discriminator.mapping: {len(affected)}/{len(schemas)}")
print()

OPS = ["login", "saveDevice", "saveAsset", "saveEntityView", "findAlarmDataByQuery"]
print(f"{'operationId':26} {'full B':>8} {'no-mapping B':>12} {'saved':>7} {'mappings':>8} {'entries each'}")
for op in OPS:
    out = server._get_endpoint_details(idx, op)
    header, _, body = out.partition("\n")
    parsed = json.loads(body)
    stripped_body = json.dumps(strip_key(parsed, "mapping"), indent=2, ensure_ascii=False)
    full_len = len(header) + 1 + len(body)
    stripped_len = len(header) + 1 + len(stripped_body)
    entries = mapping_sizes(parsed, [])
    ent = ",".join(str(e) for e in entries[:6]) + ("..." if len(entries) > 6 else "")
    print(
        f"{op:26} {full_len:8d} {stripped_len:12d} "
        f"{100 * (full_len - stripped_len) / full_len:6.1f}% {len(entries):8d} {ent}"
    )
