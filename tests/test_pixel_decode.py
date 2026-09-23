"""相机像素格式解码回归测试。

覆盖两类曾经导致"整屏纯绿 / 画面错位"的问题：
1. 像素格式常量与 MVS SDK 真值不一致（Bayer 相位错位 → 单色主导）。
2. 解码时忽略 nPaddingSize（行跨度错位 → 斜切花屏）。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import camera as C  # noqa: E402

_MVS_PIXEL_HEADER = Path(
    r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport\PixelType_header.py"
)

W, H = 16, 8
GREEN_BGR = (0, 255, 0)


class PixelDecodeTests(unittest.TestCase):
    # --- 常量正确性 -------------------------------------------------------
    def test_constants_match_sdk_when_available(self):
        """有 MVS SDK 时逐位核对常量，防止再手写错值。"""
        if not _MVS_PIXEL_HEADER.is_file():
            self.skipTest("本机未安装 MVS SDK，跳过常量核对")
        spec = importlib.util.spec_from_file_location("_pt", _MVS_PIXEL_HEADER)
        pt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pt)

        pairs = {
            "PIXEL_TYPE_MONO8": "PixelType_Gvsp_Mono8",
            "PIXEL_TYPE_RGB8": "PixelType_Gvsp_RGB8_Packed",
            "PIXEL_TYPE_BGR8": "PixelType_Gvsp_BGR8_Packed",
            "PIXEL_TYPE_YUV422_YUYV": "PixelType_Gvsp_YUV422_YUYV_Packed",
            "PIXEL_TYPE_YUV422_PACKED": "PixelType_Gvsp_YUV422_Packed",
            "PIXEL_TYPE_BAYER_RG8": "PixelType_Gvsp_BayerRG8",
            "PIXEL_TYPE_BAYER_GB8": "PixelType_Gvsp_BayerGB8",
            "PIXEL_TYPE_BAYER_RG10": "PixelType_Gvsp_BayerRG10",
            "PIXEL_TYPE_BAYER_GB10": "PixelType_Gvsp_BayerGB10",
            "PIXEL_TYPE_BAYER_RG12": "PixelType_Gvsp_BayerRG12",
            "PIXEL_TYPE_BAYER_GB12": "PixelType_Gvsp_BayerGB12",
            "PIXEL_TYPE_HB_MONO8": "PixelType_Gvsp_HB_Mono8",
            "PIXEL_TYPE_HB_RGB8": "PixelType_Gvsp_HB_RGB8_Packed",
            "PIXEL_TYPE_HB_BAYER_RG12": "PixelType_Gvsp_HB_BayerRG12",
        }
        for ours, sdk_name in pairs.items():
            with self.subTest(constant=ours):
                self.assertEqual(getattr(C, ours), getattr(pt, sdk_name))

    def test_bayer_phases_are_distinct(self):
        """RG/GB/BG/GR 四个相位必须两两不同——旧代码曾把 BG12 当成 GB12。"""
        for group in (
            [C.PIXEL_TYPE_BAYER_RG12, C.PIXEL_TYPE_BAYER_GB12,
             C.PIXEL_TYPE_BAYER_BG12, C.PIXEL_TYPE_BAYER_GR12],
            [C.PIXEL_TYPE_BAYER_RG8, C.PIXEL_TYPE_BAYER_GB8,
             C.PIXEL_TYPE_BAYER_BG8, C.PIXEL_TYPE_BAYER_GR8],
        ):
            self.assertEqual(len(set(group)), 4)

    # --- 解码正确性 -------------------------------------------------------
    def _check(self, name, got, want_bgr=None):
        self.assertEqual(got.shape, (H, W, 3), f"{name}: 形状不对")
        self.assertEqual(got.dtype, np.uint8, f"{name}: dtype 应为 uint8")
        if want_bgr is not None:
            center = got[H // 2, W // 2].astype(int)
            self.assertLessEqual(
                int(np.abs(center - np.array(want_bgr)).max()), 2,
                f"{name}: 中心像素 {center.tolist()} 期望 {list(want_bgr)}",
            )

    def test_mono8_with_padding(self):
        for pad in (0, 4, 5):
            buf = np.full((W + pad) * H, 200, dtype=np.uint8)
            self._check(f"Mono8 pad={pad}",
                        C._decode_frame(buf, W, H, C.PIXEL_TYPE_MONO8, pad), (200, 200, 200))

    def test_rgb8_channel_order_is_not_swapped(self):
        """纯绿在字节序上是 R=0,G=255,B=0；解码后必须仍是绿色而不是蓝色。"""
        for pad in (0, 6):
            flat = np.zeros((H, W * 3 + pad), dtype=np.uint8)
            flat[:, 0::3] = 0
            flat[:, 1::3] = 255
            flat[:, 2::3] = 0
            self._check(f"RGB8 pad={pad}",
                        C._decode_frame(flat.reshape(-1), W, H, C.PIXEL_TYPE_RGB8, pad), GREEN_BGR)

    def test_bgr8(self):
        bgr = np.zeros((H, W, 3), dtype=np.uint8)
        bgr[:, :, 1] = 255
        self._check("BGR8", C._decode_frame(bgr.reshape(-1), W, H, C.PIXEL_TYPE_BGR8, 0), GREEN_BGR)

    def test_bayer16bit_reads_two_bytes_per_pixel(self):
        """Bayer12 每像素 2 字节；旧代码按 3 字节/像素算会直接错位。"""
        for pad in (0, 8):
            raw12 = np.full((H, W), 0x0FFF, dtype=np.uint16)
            flat = np.zeros((H, W + pad // 2), dtype=np.uint16)
            flat[:, :W] = raw12
            out = C._decode_frame(flat.reshape(-1).view(np.uint8), W, H, C.PIXEL_TYPE_BAYER_RG12, pad)
            self._check(f"BayerRG12 pad={pad}", out)

    def test_yuv422_yuyv(self):
        raw = np.zeros((H, W, 2), dtype=np.uint8)
        raw[:, :, 0] = 128
        raw[:, :, 1] = 128
        self._check("YUV422_YUYV",
                    C._decode_frame(raw.reshape(-1), W, H, C.PIXEL_TYPE_YUV422_YUYV, 0))

    def test_hb_private_formats_decode(self):
        self._check("HB_Mono8",
                    C._decode_frame(np.full(W * H, 128, dtype=np.uint8), W, H, C.PIXEL_TYPE_HB_MONO8, 0))

    def test_padding_shifts_rows_without_insertion(self):
        """带 padding 时必须仍能还原行内容，而不是把填充当图像数据。"""
        pad = 6
        flat = np.zeros((H, W + pad), dtype=np.uint8)
        flat[:, :W] = 100
        flat[:, W:] = 255  # 填充区用亮值，若被误读会污染结果
        out = C._decode_frame(flat.reshape(-1), W, H, C.PIXEL_TYPE_MONO8, pad)
        self._check("Mono8 填充隔离", out, (100, 100, 100))

    def test_insufficient_buffer_raises(self):
        with self.assertRaises(RuntimeError):
            C._decode_frame(np.zeros(4, dtype=np.uint8), W, H, C.PIXEL_TYPE_RGB8, 0)

    def test_unknown_pixel_type_raises_with_hint(self):
        with self.assertRaisesRegex(RuntimeError, "不支持的相机像素格式"):
            C._decode_frame(np.zeros(W * H, dtype=np.uint8), W, H, 0xDEADBEEF, 0)

    # --- 直通快路径（对标 C++ 的 directMono8 / directBgr8）----------------
    def test_direct_bgr8_copies_without_conversion(self):
        bgr = np.zeros((H, W, 3), dtype=np.uint8)
        bgr[:, :, 1] = 255
        out = C._direct_from_buffer(bgr.reshape(-1), W, H, C.PIXEL_TYPE_BGR8, 0)
        self.assertIsNotNone(out)
        self._check("BGR8 直通", out, GREEN_BGR)

    def test_direct_mono8_expands_to_three_channels(self):
        for pad in (0, 4):
            flat = np.zeros((H, W + pad), dtype=np.uint8)
            flat[:, :W] = 200
            out = C._direct_from_buffer(flat.reshape(-1), W, H, C.PIXEL_TYPE_MONO8, pad)
            self.assertIsNotNone(out)
            self.assertEqual(out.shape, (H, W, 3))
            self._check(f"Mono8 直通 pad={pad}", out, (200, 200, 200))

    def test_direct_returns_none_for_non_native_formats(self):
        """Bayer / RGB8 / YUV 不是直通格式，必须返回 None 交给 SDK 转换。"""
        raw = np.zeros(W * H, dtype=np.uint8)
        for pt in (C.PIXEL_TYPE_BAYER_RG8, C.PIXEL_TYPE_RGB8, C.PIXEL_TYPE_YUV422_YUYV):
            self.assertIsNone(C._direct_from_buffer(raw, W, H, pt, 0), f"0x{pt:08X}")

    def test_direct_returns_none_when_buffer_too_small(self):
        self.assertIsNone(C._direct_from_buffer(np.zeros(4, dtype=np.uint8), W, H, C.PIXEL_TYPE_BGR8, 0))

    # --- 辅助函数 ---------------------------------------------------------
    def test_bytes_per_row(self):
        for pt, want in [
            (C.PIXEL_TYPE_MONO8, W),
            (C.PIXEL_TYPE_RGB8, W * 3),
            (C.PIXEL_TYPE_BGR8, W * 3),
            (C.PIXEL_TYPE_YUV422_YUYV, W * 2),
            (C.PIXEL_TYPE_BAYER_RG12, W * 2),
            (C.PIXEL_TYPE_HB_MONO8, W),
            (0xDEADBEEF, 0),
        ]:
            self.assertEqual(C._bytes_per_row(W, pt), want, f"0x{pt:08X}")

    def test_error_codes_are_translated(self):
        self.assertEqual(C.MV_E_NODATA, 2147483655)
        message = C._describe_grab_error(2147483655, 3)
        self.assertIn("无数据", message)
        self.assertIn("2147483655", message)
        denied = C._describe_grab_error(2147484163, 1)
        self.assertIn("2147484163", denied)
        self.assertIn("占用", denied)


if __name__ == "__main__":
    unittest.main(verbosity=2)
