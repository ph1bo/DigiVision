from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH, ensure_data_dirs


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cameras (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                vendor TEXT NOT NULL,
                model TEXT NOT NULL,
                serial TEXT NOT NULL,
                interface TEXT NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                remark TEXT NOT NULL DEFAULT '',
                auto_connect INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(serial, interface)
            );
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                manifest_json TEXT NOT NULL,
                package_path TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                service_status TEXT NOT NULL DEFAULT 'stopped',
                last_error TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS flows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                nodes_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                flow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                record_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        camera_columns = {row[1] for row in conn.execute("PRAGMA table_info(cameras)")}
        if "ip_address" not in camera_columns:
            conn.execute("ALTER TABLE cameras ADD COLUMN ip_address TEXT NOT NULL DEFAULT ''")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(flows)")}
        if "sort_order" not in columns:
            conn.execute("ALTER TABLE flows ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        # 仅移除旧版自动生成的虚拟相机；已有流程和模型不删除。
        conn.execute("DELETE FROM cameras WHERE id = 'camera-demo-001' AND serial = 'DEMO000001'")
        conn.execute("UPDATE runs SET status = 'interrupted', result = 'interrupted', finished_at = ? WHERE status IN ('running', 'paused')", (now_iso(),))


def default_flow_nodes() -> list[dict[str, Any]]:
    return [{
        "id": new_id("start"), "type": "start", "position": {"x": 0, "y": 0},
        "data": {"label": "开始", "nodeType": "start", "params": {},
                 "ports": {"builtIn": None, "dynamic": []}},
    }]


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("auto_connect", "enabled"):
        if key in data:
            data[key] = bool(data[key])
    return data
