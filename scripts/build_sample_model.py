from __future__ import annotations

import zipfile
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "samples" / "demo_model"
OUTPUT = ROOT / "samples" / "digivision-demo-defect.dvmodel"
DEMO_IMAGE = ROOT / "samples" / "demo.jpg"


def main() -> None:
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in MODEL_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(MODEL_DIR).as_posix())
    print(f"created {OUTPUT}")

    image = np.full((720, 1280, 3), 215, dtype=np.uint8)
    cv2.rectangle(image, (250, 180), (440, 350), (35, 35, 35), -1)
    cv2.circle(image, (820, 310), 82, (55, 55, 55), -1)
    cv2.putText(image, "DIGIVISION DEMO PART", (390, 610), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (60, 60, 60), 2, cv2.LINE_AA)
    cv2.imwrite(str(DEMO_IMAGE), image)
    print(f"created {DEMO_IMAGE}")


if __name__ == "__main__":
    main()
