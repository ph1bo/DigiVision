"""相机真机诊断工具：调用项目真实的 MvsRuntime 路径抓帧并输出质量统计。

用于排查"整屏纯色 / 发黑 / 报错"类问题。逐帧打印 BGR 通道均值与方差、
标记走了哪条解码路径（直通 / SDK 转换 / 本地兜底），并把最后一帧落盘。

用法: ./src-tauri/build-python/Scripts/python.exe scripts/probe_camera.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

import backend.services.camera as cam_mod
from backend.services.camera import MvsRuntime

# --- 像素格式反查表（用模块常量建 name -> value 映射）-----------------------
PT_NAMES = {
    value: name
    for name, value in vars(cam_mod).items()
    if name.startswith("PIXEL_TYPE_") and isinstance(value, int)
}

# --- 给三层路径打点，看每一帧实际走了哪条 -----------------------------------
stats: dict[str, object] = {"direct": 0, "sdk": 0, "fallback": 0, "pixel_type": None, "padding": None}

_orig_direct = cam_mod._direct_from_buffer


def _direct_wrapper(buf, w, h, pt, pad=0):
    stats["pixel_type"] = pt
    stats["padding"] = pad
    out = _orig_direct(buf, w, h, pt, pad)
    if out is not None:
        stats["direct"] = int(stats["direct"]) + 1
    return out


cam_mod._direct_from_buffer = _direct_wrapper

_orig_sdk = MvsRuntime._convert_via_sdk


def _sdk_wrapper(self, cam, buf, w, h, pt, src_len, pad=0):
    stats["pixel_type"] = pt
    stats["padding"] = pad
    out = _orig_sdk(self, cam, buf, w, h, pt, src_len, pad)
    if out is not None:
        stats["sdk"] = int(stats["sdk"]) + 1
    return out


MvsRuntime._convert_via_sdk = _sdk_wrapper

_orig_decode = cam_mod._decode_frame


def _decode_wrapper(buf, w, h, pt, pad=0):
    stats["pixel_type"] = pt
    stats["padding"] = pad
    out = _orig_decode(buf, w, h, pt, pad)
    stats["fallback"] = int(stats["fallback"]) + 1
    return out


cam_mod._decode_frame = _decode_wrapper


def describe(frame: np.ndarray) -> str:
    parts = []
    for idx, ch in enumerate(("B", "G", "R")):
        c = frame[:, :, idx]
        parts.append(f"{ch} mean={c.mean():6.1f} std={c.std():5.1f}")
    return "  ".join(parts)


def main() -> None:
    rt = MvsRuntime()
    devices = rt.enumerate()
    print(f"发现 {len(devices)} 台设备：")
    for d in devices:
        print(f"  - {d.model}  serial={d.serial}  interface={d.interface}  ip={d.ip_address!r}")
    if not devices:
        raise SystemExit("没有发现任何 MVS 设备")

    dev = devices[0]
    print(f"\n打开 {dev.model}（{dev.serial}，{dev.interface}）……")
    t0 = time.monotonic()
    rt.open(dev.serial, dev.interface)
    print(f"打开成功，耗时 {time.monotonic() - t0:.2f}s")

    frames = []
    for i in range(5):
        t1 = time.monotonic()
        frame = rt.grab(dev.serial, timeout_ms=3000)
        dt = time.monotonic() - t1
        frames.append(frame)
        verdict = ""
        mean = float(frame.mean())
        if mean < 8:
            verdict = "  ← 偏黑！"
        elif frame[:, :, 1].mean() > 1.5 * max(frame[:, :, 0].mean(), frame[:, :, 2].mean(), 1.0):
            verdict = "  ← 偏绿！"
        print(f"帧{i + 1}: shape={frame.shape} dtype={frame.dtype} 用时={dt * 1000:.0f}ms")
        print(f"      {describe(frame)}{verdict}")

    pt = stats["pixel_type"]
    print("\n=== 路径统计 ===")
    if pt is not None:
        print(f"像素格式: {PT_NAMES.get(pt, '未知枚举')} (0x{pt:08X})，行填充 padding={stats['padding']} 字节")
    print(f"直通路径帧数: {stats['direct']}   SDK 转换帧数: {stats['sdk']}   本地兜底帧数: {stats['fallback']}")

    out_path = ROOT / "probe_frame.jpg"
    ok = cam_mod.imwrite_safe(out_path, frames[-1]) if hasattr(cam_mod, "imwrite_safe") else None
    if ok is None:
        import cv2
        cv2.imwrite(str(out_path), frames[-1])
    print(f"\n最后一帧已保存: {out_path}")

    rt.close(dev.serial)
    print("相机已关闭")


if __name__ == "__main__":
    main()
