from __future__ import annotations

import os
import sys
import socket
from dataclasses import dataclass
from pathlib import Path


def user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "com.digivision.demo"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "com.digivision.demo"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "com.digivision.demo"


SOURCE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = user_data_dir() if getattr(sys, "frozen", False) or os.environ.get("DIGIVISION_DESKTOP") else SOURCE_ROOT / "data"
DATA_DIR = Path(os.environ.get("DIGIVISION_DATA_DIR", _DEFAULT_DATA_DIR)).expanduser().resolve()
# 打包后相对路径以数据目录为基准，不依赖构建机源码路径。
ROOT_DIR = DATA_DIR if getattr(sys, "frozen", False) or os.environ.get("DIGIVISION_DESKTOP") else Path(__file__).resolve().parents[1]
DB_PATH = DATA_DIR / "digivision.db"
MODELS_DIR = DATA_DIR / "models"
FRAMES_DIR = DATA_DIR / "frames"
RESULTS_DIR = DATA_DIR / "results"
LOGS_DIR = DATA_DIR / "logs"
UPLOADS_DIR = DATA_DIR / "uploads"
SAMPLES_DIR = ROOT_DIR / "samples"
MVS_IMPORT_DIR = os.environ.get("DIGIVISION_MVS_IMPORT_DIR", "").strip()
MOCK_CAMERA = os.environ.get("DIGIVISION_MOCK_CAMERA", "0").lower() not in {"0", "false", "no"}
AUTH_TOKEN = os.environ.get("DIGIVISION_TOKEN", "")


@dataclass(slots=True)
class RuntimeHandshake:
    host: str
    port: int
    token: str


def ensure_data_dirs() -> None:
    for path in (DATA_DIR, MODELS_DIR, FRAMES_DIR, RESULTS_DIR, LOGS_DIR, UPLOADS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def runtime_handshake() -> RuntimeHandshake:
    return RuntimeHandshake("127.0.0.1", int(os.environ.get("DIGIVISION_PORT", "8000")), AUTH_TOKEN)
