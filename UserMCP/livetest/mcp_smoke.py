"""UserMCP smoke test — exercise all 12 v0.5.0 tools against a live server.

Reads MCP_URL and MCP_API_KEY from env. Opens one SSE session, initializes
MCP, iterates tools with minimal test args, classifies each response as
OK / DEGRADED / ERROR, prints a summary table. Exits 0 on clean run, 1
if any tool errors.

stdlib-only by design — pulls no deps into the test surface.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

MCP_URL = os.environ.get("MCP_URL", "http://localhost:13282")

# Reject file:/ftp:/etc. before urlopen sees them (B310 hardening).
if not MCP_URL.startswith(("http://", "https://")):
    raise SystemExit(f"MCP_URL must be http(s), got: {MCP_URL!r}")
API_KEY = os.environ.get("MCP_API_KEY", "")
TIMEOUT = 15  # search_my_data with embedding fallback can run 10+s

ENVELOPE_KEYS = {"data", "coverage", "sources", "disclaimer", "next_actions"}


def open_session() -> Tuple[socket.socket, str]:
    host = MCP_URL.split("://", 1)[1].split(":")[0]
    port = int(MCP_URL.rsplit(":", 1)[1])
    sock = socket.create_connection((host, port), timeout=TIMEOUT)
    req = (
        f"GET /sse HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Accept: text/event-stream\r\n"
        f"Authorization: Bearer {API_KEY}\r\n\r\n"
    )
    sock.sendall(req.encode())
    sock.settimeout(TIMEOUT)
    buf = b""
    while b"session_id" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("SSE closed before endpoint event")
        buf += chunk
    for line in buf.decode(errors="ignore").split("\n"):
        line = line.strip()
        if line.startswith("data: /"):
            return sock, line[6:].strip()
    raise RuntimeError("no endpoint event in SSE prelude")


def post_jsonrpc(endpoint: str, msg: Dict[str, Any]) -> None:
    req = urllib.request.Request(
        f"{MCP_URL}{endpoint}",
        data=json.dumps(msg).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )
    try:
        # MCP_URL scheme is validated to be http/https at module load
        urllib.request.urlopen(req, timeout=TIMEOUT)  # nosec B310
    except urllib.error.HTTPError as e:
        body = e.read()[:500].decode(errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def drain_for_id(sock: socket.socket, target_id: int, deadline: float) -> Optional[Dict[str, Any]]:
    buf = b""
    while time.time() < deadline:
        sock.settimeout(max(0.5, deadline - time.time()))
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            return None
        buf += chunk
        needle = f'"id":{target_id}'.encode()
        if needle not in buf:
            continue
        for line in buf.decode(errors="ignore").split("\n"):
            line = line.strip()
            if line.startswith("data: ") and f'"id":{target_id}' in line:
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
    return None


def classify(tool: str, response: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    if response is None:
        return "ERROR", "no response within timeout"
    if "error" in response:
        err = response["error"]
        return "ERROR", f"JSON-RPC {err.get('code')}: {err.get('message','')[:120]}"
    result = response.get("result", {})
    content = result.get("content", [])
    if not content:
        return "ERROR", "empty content array"
    text = content[0].get("text", "")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return "ERROR", f"non-JSON text: {text[:80]}"

    missing = ENVELOPE_KEYS - set(obj.keys())
    if missing:
        return "ERROR", f"envelope missing keys: {sorted(missing)}"

    data = obj.get("data")
    gaps = obj.get("coverage", {}).get("gaps", []) if isinstance(obj.get("coverage"), dict) else []

    # send_feedback reports success inside data
    if tool == "send_feedback":
        if isinstance(data, dict) and data.get("success") is True:
            return "OK", f"feedback id={data.get('id','?')}"
        err = data.get("error") if isinstance(data, dict) else None
        return "ERROR", f"send_feedback failed: {err or 'no success flag'}"

    # get_document with an invented UUID: the 404 being reported as a gap is
    # correct behavior — classify as OK if the only gap is the lookup miss.
    if tool == "get_document" and gaps:
        gap_text = json.dumps(gaps[0]).lower() if not isinstance(gaps[0], str) else gaps[0].lower()
        if "404" in gap_text or "not found" in gap_text:
            return "OK", "graceful 404 on invented document_id"

    # A gap pointing at an HTTP 500 is a real upstream error, not just missing data.
    for gap in gaps:
        gap_text = json.dumps(gap).lower() if not isinstance(gap, str) else gap.lower()
        if "500" in gap_text or "unexpected response format" in gap_text:
            sample = json.dumps(gap) if not isinstance(gap, str) else gap
            return "ERROR", f"upstream: {sample[:140]}"

    if gaps:
        sample = gaps[0]
        sample_str = json.dumps(sample) if not isinstance(sample, str) else sample
        return "DEGRADED", f"gaps={len(gaps)}: {sample_str[:120]}"

    detail_parts = []
    if isinstance(data, dict):
        counts = {
            k: (len(v) if isinstance(v, (list, dict)) else 1)
            for k, v in data.items()
            if v not in (None, [], {}, "")
        }
        if counts:
            detail_parts.append(", ".join(f"{k}={counts[k]}" for k in list(counts)[:4]))
    return "OK", "; ".join(detail_parts) or "envelope ok"


TOOL_CALLS: List[Tuple[str, Dict[str, Any]]] = [
    ("get_my_profile", {}),
    ("get_my_active_regimen", {}),
    ("get_my_clinical_history", {}),
    ("get_vitals_timeline", {"days": 30}),
    ("get_lab_history", {}),
    ("get_wearable_summary", {"days": 30}),
    ("get_recent_activity", {"days": 14, "kind": "all", "limit": 10}),
    ("get_adherence_report", {"days": 30}),
    ("get_nutrition_report", {"days": 7}),
    ("search_my_data", {"q": "blood pressure", "k": 5}),
    ("get_document", {"document_id": "00000000-0000-0000-0000-000000000000"}),
    ("send_feedback", {
        "content": f"smoke test run at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} (pid={os.getpid()})",
        "feedback_type": "bug",
    }),
]


def main() -> int:
    if not API_KEY:
        print("ERROR: MCP_API_KEY not set in environment", file=sys.stderr)
        return 2

    sock, endpoint = open_session()
    print(f"SSE session: {endpoint.split('session_id=')[-1][:12]}...  server={MCP_URL}\n")

    post_jsonrpc(endpoint, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp_smoke", "version": "0.1"},
        },
    })
    # drain the initialize response
    drain_for_id(sock, 1, time.time() + 5)
    post_jsonrpc(endpoint, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    results = []
    for idx, (tool, args) in enumerate(TOOL_CALLS, start=10):
        t0 = time.time()
        try:
            post_jsonrpc(endpoint, {
                "jsonrpc": "2.0", "id": idx,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            })
            resp = drain_for_id(sock, idx, time.time() + TIMEOUT)
        except Exception as exc:
            resp = None
            err = str(exc)[:120]
            results.append((tool, "ERROR", f"exception: {err}", time.time() - t0))
            continue
        status, detail = classify(tool, resp)
        results.append((tool, status, detail, time.time() - t0))

    sock.close()

    print(f"{'Tool':<28}  {'Status':<8}  {'ms':>5}  Detail")
    print("-" * 100)
    for tool, status, detail, elapsed in results:
        print(f"{tool:<28}  {status:<8}  {int(elapsed*1000):>5}  {detail}")

    errors = sum(1 for _, s, _, _ in results if s == "ERROR")
    degraded = sum(1 for _, s, _, _ in results if s == "DEGRADED")
    ok = sum(1 for _, s, _, _ in results if s == "OK")
    print("-" * 100)
    print(f"Summary: {ok} OK, {degraded} DEGRADED, {errors} ERROR  (of {len(results)} tools)")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
