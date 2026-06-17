"""
infrastructure/health.py — HTTP healthcheck для Docker/Railway.

Эндпоинты:
  GET /health  — статус приложения
  GET /healthz — алиас для оркестраторов
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

logger = logging.getLogger("ai_kombain.health")

_started_at = time.monotonic()
_last_heartbeat = _started_at
_server: Optional[asyncio.AbstractServer] = None


def touch_heartbeat() -> None:
    global _last_heartbeat
    _last_heartbeat = time.monotonic()


def _build_payload() -> dict:
    now = time.monotonic()
    return {
        "status": "ok",
        "service": "ai-kombain-bot",
        "uptime_sec": round(now - _started_at, 1),
        "last_heartbeat_sec_ago": round(now - _last_heartbeat, 1),
    }


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        request_line = await reader.readline()
        if not request_line:
            return

        parts = request_line.decode("utf-8", errors="ignore").split()
        path = parts[1] if len(parts) > 1 else "/"

        # Дочитываем заголовки
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        if path in ("/health", "/healthz", "/"):
            body = json.dumps(_build_payload()).encode("utf-8")
            status = "200 OK"
        else:
            body = b'{"error":"not found"}'
            status = "404 Not Found"

        response = (
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8") + body

        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.debug("Health request error: %s", e)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def heartbeat_loop(interval_sec: float = 15.0) -> None:
    """Периодически обновляет heartbeat для мониторинга."""
    while True:
        touch_heartbeat()
        await asyncio.sleep(interval_sec)


async def start_health_server(host: str = "0.0.0.0", port: int = 8080) -> asyncio.AbstractServer:
    global _server
    touch_heartbeat()
    _server = await asyncio.start_server(_handle_client, host, port)
    logger.info("Healthcheck сервер: http://%s:%s/health", host, port)
    return _server


async def run_health_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = await start_health_server(host=host, port=port)
    async with server:
        await server.serve_forever()


async def stop_health_server() -> None:
    global _server
    if _server is not None:
        _server.close()
        await _server.wait_closed()
        _server = None
