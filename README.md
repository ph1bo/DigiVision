# DigiVision Demo

基于 DigiVision 技术设计实现的本地演示应用。目标是先跑通真实链路：流程编排、SQLite 持久化、海康 MVS 相机接入、`.dvmodel` 模型包导入与推理。

## 启动

```bash
npm install
uv sync
npm run sample:model
npm run dev:all
```

打开 `http://127.0.0.1:5173`。

桌面壳：

```bash
npm run tauri:dev
```

Tauri 负责拉起应用内 PyInstaller Python 服务并打开界面。启动时会显示 Splash，检查 SQLite、推理 Provider 和 MVS 环境。

正式桌面构建：

```bash
npm run app:build
```

macOS Apple Silicon 生成 `.app` 和 DMG。Windows x64 必须在 Windows 构建机生成 NSIS Setup EXE。应用内置 Python、SQLite、FastAPI、OpenCV、NumPy、Pillow 和 ONNX Runtime，不要求最终用户安装 Python 或 Node.js。

Windows 构建机可执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

也可以直接双击或在命令提示符执行：

```bat
.\scripts\build_windows.cmd
```

构建机需要安装 Python 3.11、`uv`、Node.js 和 Rust/Tauri 构建依赖。真实海康相机验证还需要在 Windows 上单独安装官方 MVS SDK。

macOS 本地包使用 ad-hoc 签名，不代表已完成 Apple 公证。对外发布需要 Developer ID 签名和公证。

## 目录

| 路径 | 说明 |
| --- | --- |
| `src/` | React + TypeScript 前端，首页、相机管理、模型管理 |
| `backend/` | FastAPI 本地核心，SQLite、相机、模型、流程执行、WebSocket |
| `samples/` | 演示模型包和演示图片 |
| `src-tauri/` | Tauri2 桌面壳 |
| `data/` | 运行时 SQLite、模型解包、图像和结果 |

## 已实现能力

| 模块 | 状态 |
| --- | --- |
| SQLite | 相机、模型、流程、运行记录写 SQLite |
| 相机 | MVS Python SDK 枚举、打开、取帧；无 SDK/真机时可用演示模拟相机 |
| 模型包 | `.dvmodel` zip 校验、解包、manifest 入库、脚本 load/run/unload、启停、删除 |
| 节点 | `start`、`capture`、`model`、`branch`、`display`、`storage`、`alarm`、`end` |
| 执行 | HTTP 启动、单步、停止；WebSocket 推送节点状态、日志、窗口帧、叠加层 |
| 页面 | 工作台、相机管理、模型管理按 MasterGo 暗色工业风实现 |

## MVS 接入

默认查找以下路径下的 `MvImport`：

1. 环境变量 `DIGIVISION_MVS_IMPORT_DIR`
2. `C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport`
3. `C:\Program Files\MVS\Development\Samples\Python\MvImport`
4. `/opt/MVS/Samples/64/Python/MvImport`

没有 SDK 时，`GET /api/v1/cameras/vendors` 会返回真实错误；文件采集和模型推理仍可使用。真机验证需要在安装 MVS 的机器上运行。MVS SDK 不进入 DigiVision 安装包，需要用户单独安装官方版本。

相机管理中的“刷新设备”直接调用 MVS 的 `MV_CC_EnumDevices`，同时枚举 GigE 和 USB3 设备。GigE 相机与电脑处于同一局域网和网段时，不需要手动填写 IP；发现结果会返回相机序列号和当前 IP。跨网段、网卡隔离或交换机禁用设备发现时，先使用 MVS 工具配置相机网络，当前 Demo 不提供手动 IP 创建设备入口。

## 模型包契约

```text
xxx.dvmodel
├── manifest.json
├── infer.py
├── weights/
└── resources/
```

`infer.py` 实现：

```python
def load(model_dir, manifest):
    return session

def run(image, params, session=None):
    return {
        "detection:defect:detected": True,
        "detection:defect:count": 1,
        "detection:defect:regions": [
            {"bbox": [100, 120, 40, 50], "score": 0.9}
        ]
    }

def unload(session=None):
    pass
```

`run` 必须返回 `tasks[].labels[]` 展开出的完整变量集合，缺少任一变量会判定推理失败。

## 注意

1. Demo 没有实现认证、PLC、复杂权限和多用户。
2. 配置文件已收敛到 SQLite 与少量固定路径，不再拆多个 JSON 文件。
3. 模拟相机只用于没有 MVS/真机时的 Demo；真实相机路径独立保留。
4. 模型推理脚本按同进程加载。生产环境应补进程隔离、资源限额和崩溃恢复。
