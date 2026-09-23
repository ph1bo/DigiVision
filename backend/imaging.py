"""OpenCV 图像读写封装，兼容含非 ASCII（中文）字符的路径。

Windows 上 ``cv2.imread`` / ``cv2.imwrite`` 会按本地 ANSI 编码打开文件，
路径包含中文时静默失败（返回 None / False）。这里改为经 numpy 字节缓冲
读写后再 imdecode / imencode，避免依赖 OpenCV 自身的路径处理。
"""
from __future__ import annotations

import os
import tempfile
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
    """按扩展名编码并以原子方式写入图像；成功返回 True。

    先写到同目录的临时文件再 ``os.replace``，避免「读到写了一半的文件」——
    相机预览帧是固定文件名且会被 HTTP 端并发读取，非原子写入会导致
    偶发的半张图/旧图。
    """
    target = Path(path)
    suffix = target.suffix or ".jpg"
    ok, buffer = cv2.imencode(suffix, image, params or [])
    if not ok:
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as handle:
            tmp_name = handle.name
            handle.write(buffer.tobytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        tmp_name = None
        return True
    except OSError:
        return False
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
