"""OpenCV 图像读写封装，兼容含非 ASCII（中文）字符的路径。

Windows 上 ``cv2.imread`` / ``cv2.imwrite`` 会按本地 ANSI 编码打开文件，
路径包含中文时静默失败（返回 None / False）。这里改为经 numpy 字节缓冲
读写后再 imdecode / imencode，避免依赖 OpenCV 自身的路径处理。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """读取图像；路径不存在、为空或解码失败时返回 None。"""
    try:
        buffer = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if buffer.size == 0:
        return None
    return cv2.imdecode(buffer, flags)


def imwrite(path: str | Path, image: np.ndarray, params: list[int] | None = None) -> bool:
    """按扩展名编码并写入图像；成功返回 True。"""
    suffix = Path(path).suffix or ".jpg"
    ok, buffer = cv2.imencode(suffix, image, params or [])
    if not ok:
        return False
    try:
        buffer.tofile(str(path))
    except OSError:
        return False
    return True
