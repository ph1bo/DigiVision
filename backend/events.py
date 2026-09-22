from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class EventHub:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, token: str) -> None:
        await websocket.accept()
        self._clients[token].add(websocket)

    def disconnect(self, websocket: WebSocket, token: str) -> None:
        self._clients[token].discard(websocket)

    async def broadcast(self, token: str, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for client in list(self._clients[token]):
            try:
                await client.send_json(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self.disconnect(client, token)


event_hub = EventHub()
run_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
