from __future__ import annotations

import importlib
import os
import sys
import threading
import time
import math
import socket
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import FRAMES_DIR, MVS_IMPORT_DIR
from .. import imaging


# 以下取值必须与 MVS SDK 的 PixelType_header.py 逐位一致。
# 0x0108/0x0110/0x0210/0x0218 这类值是 GenICam PFNC"压缩编码"，
# 与 GigE Vision 里实际出现在 nPixelType 上的值不同，切勿手写。
# 真值见 C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport\PixelType_header.py
PIXEL_TYPE_MONO8 = 0x01080001           # PixelType_Gvsp_Mono8 = 17301505
PIXEL_TYPE_RGB8 = 0x02180014            # PixelType_Gvsp_RGB8_Packed = 35127316
PIXEL_TYPE_BGR8 = 0x02180015            # PixelType_Gvsp_BGR8_Packed = 35127317
PIXEL_TYPE_YUV422_YUYV = 0x02100032     # PixelType_Gvsp_YUV422_YUYV_Packed = 34603058
PIXEL_TYPE_YUV422_PACKED = 0x0210001F   # PixelType_Gvsp_YUV422_Packed = 34603039
PIXEL_TYPE_BAYER_RG8 = 0x01080009       # PixelType_Gvsp_BayerRG8 = 17301513
PIXEL_TYPE_BAYER_GB8 = 0x0108000A       # PixelType_Gvsp_BayerGB8 = 17301514
PIXEL_TYPE_BAYER_RG10 = 0x0110000D      # PixelType_Gvsp_BayerRG10 = 17825805
PIXEL_TYPE_BAYER_GB10 = 0x0110000E      # PixelType_Gvsp_BayerGB10 = 17825806
PIXEL_TYPE_BAYER_RG12 = 0x01100011      # PixelType_Gvsp_BayerRG12 = 17825809
PIXEL_TYPE_BAYER_GB12 = 0x01100012      # PixelType_Gvsp_BayerGB12 = 17825810
PIXEL_TYPE_BAYER_BG8 = 0x0108000B       # PixelType_Gvsp_BayerBG8 = 17301515
PIXEL_TYPE_BAYER_GR8 = 0x01080008       # PixelType_Gvsp_BayerGR8 = 17301512
PIXEL_TYPE_BAYER_BG10 = 0x0110000F      # PixelType_Gvsp_BayerBG10 = 17825807
PIXEL_TYPE_BAYER_GR10 = 0x0110000C      # PixelType_Gvsp_BayerGR10 = 17825804
PIXEL_TYPE_BAYER_BG12 = 0x01100013      # PixelType_Gvsp_BayerBG12 = 17825811
PIXEL_TYPE_BAYER_GR12 = 0x01100010      # PixelType_Gvsp_BayerGR12 = 17825808

# 海康私有（HB_）枚举，部分相机把 PixelFormat 上报成这一组。
PIXEL_TYPE_HB_MONO8 = 0x81080001
PIXEL_TYPE_HB_RGB8 = 0x82180014
PIXEL_TYPE_HB_BGR8 = 0x82180015
PIXEL_TYPE_HB_YUV422_YUYV = 0x82100032
PIXEL_TYPE_HB_YUV422_PACKED = 0x8210001F
PIXEL_TYPE_HB_BAYER_RG8 = 0x81080009
PIXEL_TYPE_HB_BAYER_GB8 = 0x8108000A
PIXEL_TYPE_HB_BAYER_RG10 = 0x8110000D
PIXEL_TYPE_HB_BAYER_GB10 = 0x8110000E
PIXEL_TYPE_HB_BAYER_RG12 = 0x81100011
PIXEL_TYPE_HB_BAYER_GB12 = 0x81100012

_MONO8_TYPES = {PIXEL_TYPE_MONO8, PIXEL_TYPE_HB_MONO8}
_RGB8_TYPES = {PIXEL_TYPE_RGB8, PIXEL_TYPE_HB_RGB8}
_BGR8_TYPES = {PIXEL_TYPE_BGR8, PIXEL_TYPE_HB_BGR8}
_YUV422_YUYV_TYPES = {PIXEL_TYPE_YUV422_YUYV, PIXEL_TYPE_HB_YUV422_YUYV}
_YUV422_PACKED_TYPES = {PIXEL_TYPE_YUV422_PACKED, PIXEL_TYPE_HB_YUV422_PACKED}
_BAYER_RG8_TYPES = {PIXEL_TYPE_BAYER_RG8, PIXEL_TYPE_HB_BAYER_RG8}
_BAYER_GB8_TYPES = {PIXEL_TYPE_BAYER_GB8, PIXEL_TYPE_HB_BAYER_GB8}
_BAYER_RG10_TYPES = {PIXEL_TYPE_BAYER_RG10, PIXEL_TYPE_HB_BAYER_RG10}
_BAYER_GB10_TYPES = {PIXEL_TYPE_BAYER_GB10, PIXEL_TYPE_HB_BAYER_GB10}
_BAYER_RG12_TYPES = {PIXEL_TYPE_BAYER_RG12, PIXEL_TYPE_HB_BAYER_RG12}
_BAYER_GB12_TYPES = {PIXEL_TYPE_BAYER_GB12, PIXEL_TYPE_HB_BAYER_GB12}

# Bayer10/12 每像素 2 字节，右对齐；解码时需右移 (bits-8) 压到 8 位。
_BAYER_16BIT_BITS: dict[int, int] = {
    **{value: 10 for value in _BAYER_RG10_TYPES | _BAYER_GB10_TYPES},
    **{value: 12 for value in _BAYER_RG12_TYPES | _BAYER_GB12_TYPES},
}


# 海康 MVS 通用错误码（MvErrorDefine_const.py）
MV_E_NODATA = 0x80000007
MV_E_HANDLE = 0x80000000
MV_E_SUPPORT = 0x80000001
MV_E_BUFOVER = 0x80000002
MV_E_BUSY = 0x80000204
MV_E_ACCESS_DENIED = 0x80000203
MV_E_TIMEOUT = 0x8000020C
MV_E_DRIVERATTACH = 0x80000207

_ERROR_NAMES = {
    MV_E_NODATA: "无数据（相机未出图，常见于软触发后未及时取流或曝光/增益为 0）",
    MV_E_HANDLE: "句柄无效",
    MV_E_SUPPORT: "设备不支持该功能",
    MV_E_BUFOVER: "相机缓存已满",
    MV_E_BUSY: "设备忙或网络断开",
    MV_E_ACCESS_DENIED: "设备无访问权限（多被 MVS 客户端或其他程序占用）",
    MV_E_TIMEOUT: "超时",
    MV_E_DRIVERATTACH: "GigE 驱动未绑定",
}


@dataclass(slots=True)
class CameraDevice:
    serial: str
    model: str
    interface: str
    ip_address: str = ""
    vendor: str = "hikvision"
    added: bool = False
    raw: dict[str, Any] | None = None


class MvsUnavailable(RuntimeError):
    pass


class MvsRuntime:
    """海康 MVS Python SDK 适配器。

    直接调用 MvCameraControl_class；没有 SDK 时明确报错，不生成设备或帧。
    """

    def __init__(self) -> None:
        self._import_checked = False
        self._module: Any = None
        self._error = ""
        self._dll_directory_handles: list[Any] = []
        self._handles: dict[str, Any] = {}
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        self._load_module()
        return self._module is not None

    @property
    def error(self) -> str:
        self._load_module()
        return self._error

    def _candidate_paths(self) -> list[str]:
        paths: list[str] = []
        if MVS_IMPORT_DIR:
            paths.append(MVS_IMPORT_DIR)
        candidates = [
            r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport",
            r"C:\Program Files\MVS\Development\Samples\Python\MvImport",
            "/opt/MVS/Samples/aarch64/Python/MvImport",
            "/opt/MVS/Samples/64/Python/MvImport",
            "/Library/Frameworks/MvCameraControl.framework/Resources/Python/MvImport",
        ]
        paths.extend(candidates)
        return paths

    def _candidate_runtime_paths(self) -> list[str]:
        architecture = "Win64_x64" if sys.maxsize > 2**32 else "Win32_i86"
        paths: list[str] = []
        configured = os.environ.get("DIGIVISION_MVS_RUNTIME_DIR", "").strip()
        if configured:
            paths.append(configured)

        common_roots = [
            os.environ.get("CommonProgramFiles(x86)", ""),
            os.environ.get("CommonProgramW6432", ""),
            os.environ.get("CommonProgramFiles", ""),
            r"C:\Program Files (x86)\Common Files",
            r"C:\Program Files\Common Files",
        ]
        install_roots = [
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("ProgramW6432", ""),
            os.environ.get("ProgramFiles", ""),
            r"C:\Program Files (x86)",
            r"C:\Program Files",
        ]
        paths.extend(str(Path(root) / "MVS" / "Runtime" / architecture) for root in common_roots if root)
        paths.extend(str(Path(root) / "MVS" / "Runtime" / architecture) for root in install_roots if root)
        return list(dict.fromkeys(paths))

    def _prepare_windows_runtime(self) -> None:
        if sys.platform != "win32":
            return
        for candidate in self._candidate_runtime_paths():
            runtime_dir = Path(candidate)
            if not (runtime_dir / "MvCameraControl.dll").is_file():
                continue
            runtime_path = str(runtime_dir)
            entries = os.environ.get("PATH", "").split(os.pathsep)
            if runtime_path not in entries:
                current_path = os.environ.get("PATH", "")
                os.environ["PATH"] = runtime_path + (os.pathsep + current_path if current_path else "")
            add_dll_directory = getattr(os, "add_dll_directory", None)
            if add_dll_directory is not None:
                self._dll_directory_handles.append(add_dll_directory(runtime_path))
            return

    def _load_module(self) -> None:
        if self._import_checked:
            return
        self._import_checked = True
        try:
            self._prepare_windows_runtime()
            for path in self._candidate_paths():
                if path and Path(path).exists() and path not in sys.path:
                    sys.path.insert(0, path)
            self._module = importlib.import_module("MvCameraControl_class")
        except Exception as exc:  # MVS 未安装、位数不匹配或依赖缺失都会走这里
            self._error = str(exc)
            self._module = None

    def enumerate(self) -> list[CameraDevice]:
        self._load_module()
        if not self._module:
            raise MvsUnavailable(f"MVS SDK 不可用：{self._error}")

        mvs = self._module
        device_list = mvs.MV_CC_DEVICE_INFO_LIST()
        layer_type = 0
        if hasattr(mvs, "MV_GIGE_DEVICE"):
            layer_type |= mvs.MV_GIGE_DEVICE
        if hasattr(mvs, "MV_USB_DEVICE"):
            layer_type |= mvs.MV_USB_DEVICE
        ret = mvs.MvCamera.MV_CC_EnumDevices(layer_type, device_list)
        if ret != 0:
            raise RuntimeError(f"MVS 枚举失败，错误码 {ret}")

        devices: list[CameraDevice] = []
        for index in range(device_list.nDeviceNum):
            info = device_list.pDeviceInfo[index].contents
            if info.nTLayerType == getattr(mvs, "MV_USB_DEVICE", -1):
                usb = info.SpecialInfo.stUsb3VInfo
                serial = _decode_c_bytes(usb.chSerialNumber)
                model = _decode_c_bytes(usb.chModelName)
                interface = "USB3"
                ip_address = ""
                raw = {"index": index, "tLayerType": int(info.nTLayerType), "serial": serial}
            else:
                gige = info.SpecialInfo.stGigEInfo
                serial = _decode_c_bytes(gige.chSerialNumber)
                model = _decode_c_bytes(gige.chModelName)
                interface = "GigE"
                ip_address = _ipv4_from_mvs(getattr(gige, "nCurrentIp", 0))
                raw = {"index": index, "tLayerType": int(info.nTLayerType), "serial": serial, "ipAddress": ip_address}
            devices.append(CameraDevice(serial=serial, model=model, interface=interface, ip_address=ip_address, raw=raw))
        return devices

    def open(self, serial: str, interface: str = "USB3") -> None:
        self._load_module()
        if not self._module:
            raise MvsUnavailable(f"MVS SDK 不可用：{self._error}")
        with self._lock:
            if serial in self._handles:
                return
            devices = self.enumerate()
            match = next((d for d in devices if d.serial == serial and d.interface == interface), None)
            if match is None:
                raise RuntimeError("设备不在最近一次枚举结果里，请重新枚举")
            info = self._device_info_by_index(int(match.raw["index"])) if match.raw else None
            if info is None:
                raise RuntimeError("未找到 MVS 设备信息")
            cam = self._module.MvCamera()
            ret = cam.MV_CC_CreateHandle(info)
            if ret != 0:
                raise RuntimeError(f"MVS 创建句柄失败，错误码 {ret}")
            ret = cam.MV_CC_OpenDevice(getattr(self._module, "MV_ACCESS_Exclusive", 1), 0)
            if ret != 0:
                cam.MV_CC_DestroyHandle()
                raise RuntimeError(f"MVS 打开设备失败，错误码 {ret}")
            try:
                # 与同事那份可跑通的 C++ 实现保持一致：
                # 1) 用 SDK 自己的像素转换（MV_CC_ConvertPixelTypeEx）而不是 OpenCV 手搓，
                #    所以先把 SDK 内部 Bayer 插值质量调到「最优」并启用平滑，否则
                #    Bayer 相机出来的图会明显偏暗/发黑。
                # 2) GigE 相机设置最优包大小，避免丢包导致图像不完整。
                # 这两步都是 best-effort（单色相机没有 Bayer 节点），失败不阻断连接。
                self._apply_conversion_quality(cam)
                if match.interface == "GigE":
                    self._apply_optimal_packet_size(cam)
                _check_sdk(cam.MV_CC_SetEnumValue("TriggerMode", 0), "关闭触发")
                _check_sdk(cam.MV_CC_SetEnumValue("AcquisitionMode", 2), "设置连续采集")
                _check_sdk(cam.MV_CC_StartGrabbing(), "开始采集")
            except Exception:
                try: cam.MV_CC_CloseDevice()
                finally: cam.MV_CC_DestroyHandle()
                raise
            self._handles[serial] = cam

    def _apply_conversion_quality(self, cam: Any) -> None:
        """把 SDK 内置像素转换的 Bayer 插值质量调到最优（对标 C++ 实现）。"""
        setter = getattr(cam, "MV_CC_SetBayerCvtQuality", None)
        if setter is not None:
            try:
                setter(2)   # 0-快速 1-均衡(默认) 2-最优 3-最优+
            except Exception:
                pass
        filter_setter = getattr(cam, "MV_CC_SetBayerFilterEnable", None)
        if filter_setter is not None:
            try:
                filter_setter(True)
            except Exception:
                pass

    def _apply_optimal_packet_size(self, cam: Any) -> None:
        """GigE 相机设置最优网络包大小（失败不影响使用）。"""
        getter = getattr(cam, "MV_CC_GetOptimalPacketSize", None)
        setter = getattr(cam, "MV_CC_SetIntValueEx", None)
        if getter is None or setter is None:
            return
        try:
            size = int(getter())
        except Exception:
            return
        if size > 0:
            try:
                setter("GevSCPSPacketSize", size)
            except Exception:
                pass

    def _device_info_by_index(self, index: int) -> Any:
        assert self._module is not None
        mvs = self._module
        device_list = mvs.MV_CC_DEVICE_INFO_LIST()
        layer_type = getattr(mvs, "MV_GIGE_DEVICE", 0) | getattr(mvs, "MV_USB_DEVICE", 0)
        ret = mvs.MvCamera.MV_CC_EnumDevices(layer_type, device_list)
        if ret != 0 or index >= device_list.nDeviceNum:
            return None
        import ctypes
        return type(device_list.pDeviceInfo[index].contents).from_buffer_copy(
            ctypes.string_at(device_list.pDeviceInfo[index], ctypes.sizeof(device_list.pDeviceInfo[index].contents)))

    def close(self, serial: str) -> None:
        with self._lock:
            cam = self._handles.pop(serial, None)
            if not cam:
                return
            try:
                cam.MV_CC_StopGrabbing()
            except Exception:
                pass
            try:
                cam.MV_CC_CloseDevice()
            except Exception:
                pass
            try:
                cam.MV_CC_DestroyHandle()
            except Exception:
                pass

    def grab(self, serial: str, timeout_ms: int = 1000, params: dict[str, Any] | None = None) -> np.ndarray:
        with self._lock:
            cam = self._handles.get(serial)
            if cam is None:
                raise RuntimeError("相机未连接")
            if params is not None:
                self._configure(cam, params)
            # 保护性下限：500 万像素 GigE 相机一帧 YUV422 ≈ 10MB，仅网络传输就要
            # ~100ms，软触发后还要等曝光+传输。历史版本前端默认给过 500μs（0.5ms）
            # 的采集超时，预算耗尽后必然报「无数据」。这里对过小的值做钳制，
            # 让旧流程不用改配置也能跑通。
            timeout_ms = max(int(timeout_ms), 500)
            frame_type = getattr(self._module, "MV_FRAME_OUT", None) or getattr(self._module, "MV_CC_IMAGE", None)
            if frame_type is None:
                raise RuntimeError("MVS SDK 缺少 MV_FRAME_OUT 类型")

            # 软触发后第一帧往往还没到达，单次 GetImageBuffer 容易拿到 MV_E_NODATA。
            # 按剩余时间预算重试，而不是直接把"无数据"当失败抛给上层。
            attempts = 0
            max_attempts = 20
            deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
            while True:
                attempts += 1
                st_frame = frame_type()
                ret = cam.MV_CC_GetImageBuffer(st_frame, timeout_ms)
                if ret == 0:
                    break
                # NODATA 才重试；其它错误码（如设备被占用）立即上报，避免空转。
                if ret != MV_E_NODATA or attempts >= max_attempts or time.monotonic() >= deadline:
                    raise RuntimeError(_describe_grab_error(ret, attempts))
                time.sleep(0.02)

            try:
                info = st_frame.stFrameInfo
                width, height = int(info.nWidth), int(info.nHeight)
                pixel_type = int(info.enPixelType)
                size = int(info.nFrameLen)
                # nFrameLen 可能没算上行填充，取两者较大值以保证缓冲足够。
                padding = int(getattr(info, "nPaddingSize", 0) or 0)
                row_bytes = _bytes_per_row(width, pixel_type)
                if row_bytes:
                    size = max(size, (row_bytes + padding) * height)
                address = st_frame.pBufAddr
                ptr = self._module.cast(address, self._module.POINTER(self._module.c_ubyte)) if hasattr(self._module, "cast") else None
                if ptr is None:
                    import ctypes
                    ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_ubyte))
                buffer = np.ctypeslib.as_array(ptr, shape=(size,))

                # 与 C++ 一致：Mono8 / BGR8 本来就是目标格式，直接拷贝即可，
                # 不需要调用 SDK 转换（少一次转换、也少一个失败点）。
                direct = _direct_from_buffer(buffer, width, height, pixel_type, padding)
                if direct is not None:
                    return direct

                # 首选：交给 SDK 做像素转换（与同事可跑通的 C++ 实现一致）。
                # SDK 内部会把 Bayer 去马赛克、YUV 转 BGR、位深压到 8 位，
                # 质量远好于自己用 OpenCV 手搓，也天然处理 padding。
                converted = self._convert_via_sdk(cam, buffer, width, height, pixel_type, size, padding)
                if converted is not None:
                    return converted
                # 回退：SDK 转换不可用/失败时，用本地解码器兜底。
                return _decode_frame(buffer, width, height, pixel_type, padding)
            finally:
                try:
                    cam.MV_CC_FreeImageBuffer(st_frame)
                except Exception:
                    pass

    def _convert_via_sdk(
        self,
        cam: Any,
        buffer: np.ndarray,
        width: int,
        height: int,
        pixel_type: int,
        src_length: int,
        padding: int = 0,
    ) -> np.ndarray | None:
        """调用 MV_CC_ConvertPixelTypeEx 转成 BGR8；不可用时返回 None。

        对应 C++ 里的 MV_CC_ConvertPixelTypeEx 调用。目标格式只取 BGR8
        （OpenCV 的原生布局），单色/彩色都由 SDK 统一处理。
        """
        mvs = self._module
        param_type = getattr(mvs, "MV_CC_PIXEL_CONVERT_PARAM_EX", None)
        if param_type is None or not width or not height:
            return None

        # 目标缓冲：BGR8 = 每像素 3 字节
        dst_size = width * height * 3
        dst = (mvs.c_ubyte * dst_size)()
        param = param_type()
        param.nWidth = width
        param.nHeight = height
        param.enSrcPixelType = pixel_type
        param.pSrcData = mvs.cast(
            buffer.ctypes.data_as(mvs.POINTER(mvs.c_ubyte)),
            mvs.POINTER(mvs.c_ubyte),
        )
        param.nSrcDataLen = int(src_length)
        param.enDstPixelType = PIXEL_TYPE_BGR8
        param.pDstBuffer = mvs.cast(dst, mvs.POINTER(mvs.c_ubyte))
        param.nDstBufferSize = dst_size

        try:
            ret = cam.MV_CC_ConvertPixelTypeEx(param)
        except Exception:
            return None
        if ret != 0:
            return None

        out_len = int(param.nDstLen) or dst_size
        if out_len <= 0 or out_len > dst_size:
            out_len = dst_size
        arr = np.frombuffer(bytes(dst[:out_len]), dtype=np.uint8)
        if arr.size < width * height * 3:
            return None
        # 单色相机转 BGR8 后是 3 通道；Bayer/彩色同样是 3 通道。
        return arr[: width * height * 3].reshape(height, width, 3).copy()


    def _configure(self, cam: Any, params: dict[str, Any]) -> None:
        trigger = params.get("triggerMode", "soft")
        if trigger not in {"soft", "hardware", "continuous"}:
            raise RuntimeError("相机触发模式必须为 soft、hardware 或 continuous")
        # 修改采集参数前停流，设置失败也不继续取旧参数的帧。
        _check_sdk(cam.MV_CC_StopGrabbing(), "停止采集以设置参数")
        _check_sdk(cam.MV_CC_SetEnumValue("TriggerMode", 0 if trigger == "continuous" else 1), "设置触发模式")
        if trigger != "continuous":
            _check_sdk(cam.MV_CC_SetEnumValue("TriggerSource", 7 if trigger == "soft" else 0), "设置触发源")
        for key, sdk_name, auto_name in (("exposure", "ExposureTime", "ExposureAuto"), ("gain", "Gain", "GainAuto")):
            setting = params.get(key) or {}
            if setting.get("enabled", False):
                value = setting.get("value")
                if type(value) not in {int, float} or not math.isfinite(value) or value < 0 or (key == "exposure" and value == 0):
                    raise RuntimeError(f"相机 {key} 参数必须为有效数值")
                _check_sdk(cam.MV_CC_SetEnumValue(auto_name, 0), f"关闭 {key} 自动调节")
                _check_sdk(cam.MV_CC_SetFloatValue(sdk_name, float(value)), f"设置 {key}")
        setting = params.get("line") or {}
        if setting.get("enabled", False):
            value = setting.get("value")
            if type(value) is not int or value <= 0: raise RuntimeError("相机行数必须为正整数")
            _check_sdk(cam.MV_CC_SetIntValue("Height", value), "设置行数")
        _check_sdk(cam.MV_CC_StartGrabbing(), "开始采集")
        # 流刚启动时相机还没排出首帧，立刻取流会拿到 MV_E_NODATA（2147483655）。
        # 这里留一点稳定时间；grab() 里还有基于超时预算的重试兜底。
        time.sleep(0.05)
        if trigger == "soft":
            _check_sdk(cam.MV_CC_SetCommandValue("TriggerSoftware"), "软件触发")

    def close_all(self) -> None:
        for serial in list(self._handles):
            self.close(serial)


class CameraService:
    def __init__(self) -> None:
        self.mvs = MvsRuntime()
        self._runtime_connected: set[str] = set()
        self._last_frame_paths: dict[str, Path] = {}
        self._cameras: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.in_use = lambda camera_id: False

    def vendors(self) -> list[dict[str, Any]]:
        return [{"id": "hikvision", "name": "海康机器人", "sdk": "MVS SDK",
                 "sdkVersion": "MVS SDK" if self.mvs.available else "未检测到 MVS SDK",
                 "available": self.mvs.available, "error": self.mvs.error}]

    def discover(self, vendor: str) -> dict[str, Any]:
        if vendor != "hikvision":
            return {"vendor": vendor, "sdkAvailable": False, "devices": [], "error": "不支持的相机厂商"}
        try:
            devices = self.mvs.enumerate()
            return {"vendor": vendor, "sdkAvailable": True, "devices": [asdict(d) for d in devices], "error": ""}
        except Exception as exc:
            return {"vendor": vendor, "sdkAvailable": self.mvs.available, "devices": [], "error": str(exc)}

    def connect(self, camera: dict[str, Any]) -> None:
        if camera.get("vendor") != "hikvision" or camera.get("serial") == "DEMO000001":
            raise RuntimeError("只支持真实海康相机，请重新枚举设备")
        with self._lock:
            self.mvs.open(str(camera["serial"]), str(camera["interface"]))
            self._runtime_connected.add(str(camera["id"]))
            self._cameras[str(camera["id"])] = dict(camera)

    def disconnect(self, camera: dict[str, Any]) -> None:
        with self._lock:
            camera_id = str(camera["id"])
            if self.in_use(camera_id): raise RuntimeError("流程正在使用该相机，不能断开")
            self.mvs.close(str(camera["serial"]))
            self._runtime_connected.discard(camera_id)
            self._cameras.pop(camera_id, None)
            self._last_frame_paths.pop(camera_id, None)

    def is_connected(self, camera_id: str) -> bool:
        return camera_id in self._runtime_connected

    def grab(self, camera: dict[str, Any], timeout_ms: int = 1000, params: dict[str, Any] | None = None) -> np.ndarray:
        with self._lock:
            camera_id = str(camera["id"])
            if camera_id not in self._runtime_connected:
                raise RuntimeError("相机未连接，请先到相机管理连接")
            frame = self.mvs.grab(str(camera["serial"]), timeout_ms, params)
            if not isinstance(frame, np.ndarray) or frame.size == 0:
                raise RuntimeError("相机返回空帧")
            if params:
                flip_x, flip_y = params.get("flipX", False), params.get("flipY", False)
                if type(flip_x) is not bool or type(flip_y) is not bool:
                    raise RuntimeError("相机翻转参数必须为 Bool")
                if flip_x or flip_y: frame = cv2.flip(frame, -1 if flip_x and flip_y else (1 if flip_x else 0))
                transform = params.get("transform") or {}
                if transform.get("enabled"):
                    mode = transform.get("mode", "none")
                    if mode == "hflip": frame = cv2.flip(frame, 1)
                    elif mode == "vflip": frame = cv2.flip(frame, 0)
                    elif mode in {"rot90_cw", "rot90_ccw", "rot180"}:
                        frame = cv2.rotate(frame, {"rot90_cw": cv2.ROTATE_90_CLOCKWISE, "rot90_ccw": cv2.ROTATE_90_COUNTERCLOCKWISE, "rot180": cv2.ROTATE_180}[mode])
                    elif mode != "none": raise RuntimeError("不支持的相机图像变换")
            self.save_latest_frame(camera_id, frame)
            return frame

    def file_path_for_capture(self, camera_id: str, frame: np.ndarray | None = None) -> Path:
        if frame is not None:
            self.save_latest_frame(camera_id, frame)
        path = self._last_frame_paths.get(camera_id)
        if path and path.exists():
            return path
        raise RuntimeError("没有可用的相机预览帧")

    def save_latest_frame(self, camera_id: str, frame: np.ndarray) -> Path:
        FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        path = FRAMES_DIR / f"{camera_id}.jpg"
        ok = imaging.imwrite(path, frame)
        if not ok:
            raise RuntimeError("相机帧保存失败")
        self._last_frame_paths[camera_id] = path
        return path

    def close_all(self) -> None:
        with self._lock:
            self.mvs.close_all()
            self._runtime_connected.clear()
            self._cameras.clear()
            self._last_frame_paths.clear()


def _check_sdk(result: int, operation: str) -> None:
    if result != 0: raise RuntimeError(f"MVS {operation}失败，错误码 {result}")


def _error_name(code: int) -> str:
    return _ERROR_NAMES.get(code & 0xFFFFFFFF, "未知错误")


def _describe_grab_error(code: int, attempts: int) -> str:
    """把 MVS 错误码翻译成人能看懂的一句话，附带诊断建议。"""
    name = _error_name(code)
    hint = ""
    if (code & 0xFFFFFFFF) == MV_E_NODATA:
        hint = (
            "。可能原因：1) 软触发后相机还没出图就被取流（已自动重试 %d 次）；"
            "2) 曝光时间过长或增益为 0 导致不出图；"
            "3) 采集超时设置过短" % attempts
        )
    elif (code & 0xFFFFFFFF) == MV_E_ACCESS_DENIED:
        hint = "。请关闭 MVS 客户端或其它占用该相机的程序后重试"
    elif (code & 0xFFFFFFFF) == MV_E_DRIVERATTACH:
        hint = "。请在 MVS 里用「GigE 配置工具」绑定网卡驱动"
    return f"MVS 取帧失败，错误码 {code}（0x{code & 0xFFFFFFFF:08X}：{name}）{hint}"


def _bytes_per_row(width: int, pixel_type: int) -> int:
    """返回某像素格式下每行的有效字节数；未知格式返回 0。"""
    if pixel_type in _MONO8_TYPES | _BAYER_RG8_TYPES | _BAYER_GB8_TYPES:
        return width
    if pixel_type in _RGB8_TYPES | _BGR8_TYPES:
        return width * 3
    if pixel_type in _YUV422_YUYV_TYPES | _YUV422_PACKED_TYPES:
        return width * 2
    if pixel_type in _BAYER_16BIT_BITS:
        return width * 2
    return 0


def _decode_c_bytes(value: Any) -> str:
    if isinstance(value, str): return value.split("\x00", 1)[0]
    try: return bytes(value).split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    except (ValueError, TypeError): raise RuntimeError("相机设备字符串格式错误")


def _ipv4_from_mvs(value: Any) -> str:
    """把 MVS GigE 设备信息里的 uint32 IP 转成可读地址。"""
    if isinstance(value, str):
        return value
    try:
        number = int(value)
        if number <= 0 or number > 0xFFFFFFFF:
            return ""
        return socket.inet_ntoa(struct.pack(">I", number))
    except (OSError, TypeError, ValueError, struct.error):
        return ""


def _direct_from_buffer(
    buffer: np.ndarray,
    width: int,
    height: int,
    pixel_type: int,
    padding: int = 0,
) -> np.ndarray | None:
    """Mono8 / BGR8 直接拷贝，不做像素转换；其它格式返回 None。

    对应可跑通的 C++ 实现里的 directMono8 / directBgr8 快路径：源格式
    已经是目标格式时没必要再走一次 SDK 转换（BGR8 连通道顺序都不用动）。
    注意 Mono8 也统一升成 3 通道 BGR，保证下游拿到的都是同一种布局。
    """
    if width <= 0 or height <= 0:
        return None
    if pixel_type not in _MONO8_TYPES and pixel_type not in _BGR8_TYPES:
        return None

    channels = 1 if pixel_type in _MONO8_TYPES else 3
    row_bytes = width * channels
    pad_bytes = max(0, int(padding))
    span_bytes = row_bytes + pad_bytes
    need_bytes = span_bytes * height
    if buffer.size < need_bytes:
        return None
    rows = buffer[:need_bytes].reshape(height, span_bytes)
    pixels = rows[:, :row_bytes].reshape(height, width, channels)
    if channels == 3:
        return pixels.copy()
    return cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR)


def _decode_frame(
    buffer: np.ndarray,
    width: int,
    height: int,
    pixel_type: int,
    padding: int = 0,
) -> np.ndarray:
    """把 MVS 原始帧缓冲解码成 BGR 图。

    padding 是 SDK 的 nPaddingSize（每行尾部的填充字节）。忽略它会导致
    图像整体斜切/错位——这正是"整屏纯绿"这类花屏的常见成因。
    """
    if width <= 0 or height <= 0:
        raise RuntimeError("相机帧尺寸无效")

    # 统一按「每像素字节数」算行跨度，再 reshape。
    # 注意 padding 是**字节**数，而 numpy 视图的元素单位随 dtype 变化，
    # 所以这里先把 padding 换算成元素个数，避免 16 位格式错位。
    def _strided(channels: int, dtype: Any = np.uint8) -> np.ndarray:
        itemsize = np.dtype(dtype).itemsize
        row_bytes = width * channels * itemsize
        pad_bytes = max(0, int(padding))
        if pad_bytes % itemsize:
            pad_bytes += itemsize - (pad_bytes % itemsize)
        span_bytes = row_bytes + pad_bytes
        need_bytes = span_bytes * height
        if buffer.size < need_bytes:
            raise RuntimeError(
                f"相机帧数据不足：需要 {need_bytes} 字节，实际 {buffer.size}"
            )
        span = span_bytes // itemsize
        rows = buffer[:need_bytes].view(dtype).reshape(height, span)
        return rows[:, : width * channels].reshape(height, width, channels)

    if pixel_type in _MONO8_TYPES:
        return cv2.cvtColor(_strided(1), cv2.COLOR_GRAY2BGR)
    if pixel_type in _RGB8_TYPES:
        return cv2.cvtColor(_strided(3), cv2.COLOR_RGB2BGR)
    if pixel_type in _BGR8_TYPES:
        return _strided(3).copy()
    if pixel_type in _YUV422_YUYV_TYPES:
        return cv2.cvtColor(_strided(2), cv2.COLOR_YUV2BGR_YUY2)
    if pixel_type in _YUV422_PACKED_TYPES:
        return cv2.cvtColor(_strided(2), cv2.COLOR_YUV2BGR_UYVY)

    if pixel_type in _BAYER_RG8_TYPES | _BAYER_GB8_TYPES:
        raw = _strided(1)
        code = cv2.COLOR_BayerRG2BGR if pixel_type in _BAYER_RG8_TYPES else cv2.COLOR_BayerGB2BGR
        return cv2.cvtColor(raw, code)

    # Bayer10/12 在海康相机上是**非打包**的：每像素占 2 字节（低位对齐）。
    if pixel_type in _BAYER_16BIT_BITS:
        raw16 = _strided(1, np.uint16)
        bits = _BAYER_16BIT_BITS[pixel_type]
        raw8 = (raw16 >> (bits - 8)).clip(0, 255).astype(np.uint8)
        is_rg = pixel_type in _BAYER_RG10_TYPES or pixel_type in _BAYER_RG12_TYPES
        return cv2.cvtColor(raw8, cv2.COLOR_BayerRG2BGR if is_rg else cv2.COLOR_BayerGB2BGR)

    raise RuntimeError(
        f"不支持的相机像素格式：0x{pixel_type:08X}。"
        "请在 MVS 客户端把 PixelFormat 设为 Mono8/BayerRG8/RGB8/YUV422_YUYV 后重试。"
    )
