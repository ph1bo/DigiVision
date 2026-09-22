from __future__ import annotations

import importlib
import sys
import threading
import math
import socket
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import FRAMES_DIR, MVS_IMPORT_DIR


PIXEL_TYPE_MONO8 = 0x01080001
PIXEL_TYPE_RGB8 = 0x02180014
PIXEL_TYPE_BGR8 = 0x02180015
PIXEL_TYPE_BAYER_RG8 = 0x01080009
PIXEL_TYPE_BAYER_RG10 = 0x01100010
PIXEL_TYPE_BAYER_RG12 = 0x01100012
PIXEL_TYPE_BAYER_GB8 = 0x0108000A
PIXEL_TYPE_BAYER_GB10 = 0x01100011
PIXEL_TYPE_BAYER_GB12 = 0x01100013


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

    def _load_module(self) -> None:
        if self._import_checked:
            return
        self._import_checked = True
        for path in self._candidate_paths():
            if path and Path(path).exists() and path not in sys.path:
                sys.path.insert(0, path)
        try:
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
                _check_sdk(cam.MV_CC_SetEnumValue("TriggerMode", 0), "关闭触发")
                _check_sdk(cam.MV_CC_SetEnumValue("AcquisitionMode", 2), "设置连续采集")
                _check_sdk(cam.MV_CC_StartGrabbing(), "开始采集")
            except Exception:
                try: cam.MV_CC_CloseDevice()
                finally: cam.MV_CC_DestroyHandle()
                raise
            self._handles[serial] = cam

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
            frame_type = getattr(self._module, "MV_FRAME_OUT", None) or getattr(self._module, "MV_CC_IMAGE", None)
            if frame_type is None:
                raise RuntimeError("MVS SDK 缺少 MV_FRAME_OUT 类型")
            st_frame = frame_type()
            ret = cam.MV_CC_GetImageBuffer(st_frame, timeout_ms)
            if ret != 0:
                raise RuntimeError(f"MVS 取帧失败，错误码 {ret}")
            try:
                info = st_frame.stFrameInfo
                width, height = int(info.nWidth), int(info.nHeight)
                size = int(info.nFrameLen)
                address = st_frame.pBufAddr
                ptr = self._module.cast(address, self._module.POINTER(self._module.c_ubyte)) if hasattr(self._module, "cast") else None
                if ptr is None:
                    import ctypes
                    ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_ubyte))
                buffer = np.ctypeslib.as_array(ptr, shape=(size,))
                frame = _decode_frame(buffer, width, height, int(info.enPixelType))
                return frame
            finally:
                try:
                    cam.MV_CC_FreeImageBuffer(st_frame)
                except Exception:
                    pass


    def _configure(self, cam: Any, params: dict[str, Any]) -> None:
        trigger = params.get("triggerMode", "soft")
        if trigger not in {"soft", "hardware", "continuous"}:
            raise RuntimeError("相机触发模式必须为 soft、hardware 或 continuous")
        # 修改采集节点前停流，设置失败也不继续取旧参数的帧。
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
        ok = cv2.imwrite(str(path), frame)
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


def _decode_frame(buffer: np.ndarray, width: int, height: int, pixel_type: int) -> np.ndarray:
    if width <= 0 or height <= 0:
        raise RuntimeError("相机帧尺寸无效")
    if pixel_type == PIXEL_TYPE_MONO8:
        raw = buffer[: width * height].reshape(height, width)
        return cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
    if pixel_type == PIXEL_TYPE_RGB8:
        raw = buffer[: width * height * 3].reshape(height, width, 3)
        return cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
    if pixel_type == PIXEL_TYPE_BGR8:
        return buffer[: width * height * 3].reshape(height, width, 3).copy()
    if pixel_type in {PIXEL_TYPE_BAYER_RG8, PIXEL_TYPE_BAYER_GB8}:
        raw = buffer[: width * height].reshape(height, width)
        code = cv2.COLOR_BayerRG2BGR if pixel_type == PIXEL_TYPE_BAYER_RG8 else cv2.COLOR_BayerGB2BGR
        return cv2.cvtColor(raw, code)
    if pixel_type in {PIXEL_TYPE_BAYER_RG10, PIXEL_TYPE_BAYER_RG12, PIXEL_TYPE_BAYER_GB10, PIXEL_TYPE_BAYER_GB12}:
        raw16 = buffer[: width * height * 2].view(np.uint16).reshape(height, width)
        bits = 10 if pixel_type in {PIXEL_TYPE_BAYER_RG10, PIXEL_TYPE_BAYER_GB10} else 12
        raw8 = (raw16 >> (bits - 8)).clip(0, 255).astype(np.uint8)
        code = cv2.COLOR_BayerRG2BGR if pixel_type in {PIXEL_TYPE_BAYER_RG10, PIXEL_TYPE_BAYER_RG12} else cv2.COLOR_BayerGB2BGR
        return cv2.cvtColor(raw8, code)
    raise RuntimeError(f"不支持的相机像素格式：0x{pixel_type:08X}")
