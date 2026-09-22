from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


_session: dict[str, Any] = {}


def load(model_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    _session["model_dir"] = model_dir
    _session["manifest"] = manifest
    return _session


def unload(session: dict[str, Any] | None = None) -> None:
    _session.clear()


def run(image: np.ndarray, params: dict[str, Any], session: dict[str, Any] | None = None) -> dict[str, Any]:
    active_session = session or _session
    label = active_session["manifest"]["tasks"][0]["labels"][0]
    threshold = int(params.get("threshold") or 80)
    min_area = int(params.get("minArea") or 120)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions: list[dict[str, Any]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        regions.append({
            "label": label["label"],
            "labelName": label["name"],
            "bbox": [int(x), int(y), int(w), int(h)],
            "area": float(area),
            "score": 1.0,
        })
    return {
        "detection:defect:detected": len(regions) > 0,
        "detection:defect:count": len(regions),
        "detection:defect:regions": regions,
    }
