"""Minimal ASGI application backed by PostgreSQL.

Runs identically on Render (DATABASE_URL from the managed database) and on a
Cloudfall host (DATABASE_URL over the local peer-authenticated socket), which
is what makes it usable as migration evidence: the same /items rows must be
served before and after cutover.
"""

import json
import os

import psycopg


def _items() -> list[dict[str, object]]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        rows = connection.execute(
            "SELECT id, name FROM items ORDER BY id"
        ).fetchall()
    return [{"id": row[0], "name": row[1]} for row in rows]


async def application(scope, receive, send):
    if scope["type"] != "http":
        raise RuntimeError("http only")
    if scope["path"] == "/health":
        status, payload = 200, {"status": "ok"}
    elif scope["path"] == "/items":
        status, payload = 200, {"items": _items()}
    else:
        status, payload = 200, {"service": "render-demo"}
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
