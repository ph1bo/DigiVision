"""在当前操作系统构建独立后端；构建工具只在开发机使用。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "src-tauri"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def bundle_targets(platform: str | None = None) -> str:
    target_platform = platform or sys.platform
    if target_platform == "darwin":
        return "app,dmg"
    if target_platform.startswith("win"):
        return "nsis"
    return "app"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-only", action="store_true")
    args = parser.parse_args()
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("构建需要安装 uv；发布后的应用不需要 uv 或 Python。")
    env = TAURI / "build-python"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        run(uv, "venv", "--python", "3.11", str(env))
    run(uv, "pip", "install", "--python", str(python), "-r", "pyproject.toml", "--extra", "desktop")
    run(str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "digivision-backend", "--paths", str(ROOT),
        "--distpath", str(TAURI / "resources"),
        "--workpath", str(TAURI / "pyinstaller-build"),
        "--specpath", str(TAURI),
        "--collect-submodules", "uvicorn", "--collect-submodules", "websockets",
        "--collect-all", "onnxruntime", "--collect-all", "cv2",
        "--collect-all", "numpy", "--collect-all", "PIL",
        "--hidden-import", "sqlite3", str(ROOT / "scripts" / "backend_entry.py"))
    if not args.backend_only:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise SystemExit("构建需要 npm。")
        # 后端已经构建，不重复执行 beforeBuildCommand 的后端步骤。
        targets = bundle_targets(sys.platform)
        build_targets = "app" if sys.platform == "darwin" else targets
        run(npm, "exec", "tauri", "build", "--", "--config",
            '{"build":{"beforeBuildCommand":"npm run build"}}',
            "--bundles", build_targets)
        if sys.platform == "darwin":
            bundle = TAURI / "target" / "release" / "bundle" / "macos" / "DigiVision.app"
            # 本机测试包采用 ad-hoc 签名；不等同于 Developer ID 签名或公证。
            run("codesign", "--force", "--deep", "--sign", "-", str(bundle))
            run("codesign", "--verify", "--deep", "--strict", str(bundle))
            dmg_dir = TAURI / "target" / "release" / "bundle" / "dmg"
            dmg_dir.mkdir(parents=True, exist_ok=True)
            dmg = dmg_dir / "DigiVision_0.1.0_aarch64.dmg"
            staging = Path(tempfile.mkdtemp(prefix="digivision-dmg-"))
            try:
                run("ditto", "--rsrc", "--extattr", str(bundle), str(staging / "DigiVision.app"))
                os.symlink("/Applications", staging / "Applications")
                run("hdiutil", "create", "-volname", "DigiVision", "-srcfolder", str(staging),
                    "-ov", "-format", "UDZO", str(dmg))
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            if not list(dmg_dir.glob("*.dmg")):
                raise SystemExit("macOS DMG 构建失败：bundle/dmg 下没有 DMG 文件。")
        elif sys.platform.startswith("win"):
            nsis_dir = TAURI / "target" / "release" / "bundle" / "nsis"
            if not list(nsis_dir.glob("*.exe")):
                raise SystemExit("Windows NSIS 构建失败：bundle/nsis 下没有 EXE 文件。")


if __name__ == "__main__":
    main()
