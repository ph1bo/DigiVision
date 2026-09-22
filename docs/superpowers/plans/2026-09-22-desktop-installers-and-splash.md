# DigiVision 桌面安装包与启动流程实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Apple Silicon Mac 生成并验证包含独立 Python 后端和真实启动状态页的 DigiVision DMG，同时把 Windows x64 NSIS 与 CUDA/CPU 依赖选择准备到可在 Windows 构建机直接执行的状态。

**Architecture:** Python 后端新增单一运行时能力接口，统一报告 SQLite、ONNX Runtime Provider 和 MVS SDK 状态，并把同样的能力信息传给模型脚本。Tauri 同时创建 Splash 和隐藏的主窗口，Splash 完成后端握手与能力检查后切换到主窗口；构建脚本按平台安装 ONNX Runtime 发行包并生成原生安装器。

**Tech Stack:** React 19、TypeScript 6、Vite 8、Tauri 2、Rust、FastAPI、SQLite、PyInstaller、ONNX Runtime、Python 3.11

**Spec:** `docs/superpowers/specs/2026-09-22-desktop-installers-and-splash-design.md`

## Global Constraints

- 首个 macOS 安装包只支持 Apple Silicon，产物为 DMG，使用 ad-hoc 签名，仅用于本机验收。
- Windows 安装包必须在 Windows x64 构建机生成，产物为 NSIS Setup EXE。
- macOS 安装 `onnxruntime`，Provider 优先级为 CoreML、CPU。
- Windows 安装 `onnxruntime-gpu`，Provider 优先级为 CUDA、CPU。
- 加速 Provider 不可用时使用 CPU，不阻断应用启动；CPU Provider 不可用时阻断启动。
- MVS SDK 不进入安装包。SDK 缺失时允许进入主界面，但真实相机功能不可用。
- Python、SQLite、FastAPI、OpenCV、NumPy、Pillow 和 ONNX Runtime 必须包含在 App 中。
- 模型包只维护一份 `infer.py`，Provider 选择以 `onnxruntime.get_available_providers()` 的实际结果为准。
- 不改动现有流程结果语义：系统只记录 `success` 或 `failed`，不内置 OK/NG 业务结果。
- 不修改现有 Region 原图坐标语义和 Text 图像窗口 CSS 像素坐标语义。
- 当前目录不是 Git 仓库，不执行初始化、提交、分支或 worktree 操作。

## Review Focus

- `onnxruntime-gpu` 安装后，声明 `onnxruntime>=1.22.0` 的模型包仍应通过依赖检查。
- CoreML 或 CUDA 已列出但 Session 创建失败时，模型必须只用 CPU 重试一次。
- MVS 未安装或 Python 模块加载失败时，Splash 应显示非阻断提醒，主窗口仍可打开。
- 后端握手超时、进程退出或能力接口失败时，Splash 必须保留错误和日志路径，不能显示空白主窗口。
- 已安装的 DMG App 必须在脱离源码、系统 Python 和 Node.js 的条件下启动后端，并在退出时回收后端进程。

---

### Task 1: 统一运行时能力和模型依赖契约

**Files:**
- Modify: `backend/services/models.py`
- Modify: `backend/main.py`
- Modify: `tests/test_runtime_logic.py`
- Modify: `tests/test_api_lifecycle.py`

**Interfaces:**
- Produces: `ModelService.runtime_capabilities() -> dict[str, Any]`
- Produces: `GET /api/v1/runtime/capabilities`
- Produces: `load(ctx)` 中的 `ctx["runtime"]`
- Consumes: `CameraService.vendors() -> list[dict[str, Any]]`

- [ ] **Step 1: 写能力检测和 ONNX Runtime 别名的失败测试**

在 `tests/test_runtime_logic.py` 增加用例，固定能力结构、Provider 优先级和依赖别名行为：

```python
def test_runtime_capabilities_use_available_providers(self):
    fake = type("Ort", (), {
        "__version__": "1.22.1",
        "get_available_providers": staticmethod(
            lambda: ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        ),
    })
    with patch.dict("sys.modules", {"onnxruntime": fake}):
        result = self.models.runtime_capabilities()
    self.assertTrue(result["cpuAvailable"])
    self.assertEqual(result["recommendedProvider"], "CoreMLExecutionProvider")
    self.assertEqual(result["availableProviders"], [
        "CoreMLExecutionProvider", "CPUExecutionProvider"
    ])

def test_onnxruntime_requirement_accepts_gpu_distribution(self):
    with patch.object(ms.importlib.metadata, "version", side_effect=lambda name: {
        "onnxruntime-gpu": "1.22.1"
    }[name]):
        ms._check_requirements(["onnxruntime>=1.22.0"])
```

在 `tests/test_api_lifecycle.py` 的空库检查中断言 `/runtime/capabilities` 包含 `database.ready`、`inference.cpuAvailable` 和 `camera.vendors`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_runtime_logic.RuntimeTests.test_runtime_capabilities_use_available_providers tests.test_runtime_logic.RuntimeTests.test_onnxruntime_requirement_accepts_gpu_distribution -v`

Expected: FAIL，原因为 `runtime_capabilities` 不存在且依赖检查只查 `onnxruntime` 发行包名。

- [ ] **Step 3: 实现能力检测、接口和模型上下文**

在 `backend/services/models.py` 中实现稳定结构：

```python
def runtime_capabilities(self) -> dict[str, Any]:
    try:
        import onnxruntime as ort
        providers = list(ort.get_available_providers())
        preferred = ["CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
        recommended = next((name for name in preferred if name in providers), None)
        return {
            "version": ort.__version__,
            "availableProviders": providers,
            "recommendedProvider": recommended,
            "cpuAvailable": "CPUExecutionProvider" in providers,
            "accelerated": recommended not in {None, "CPUExecutionProvider"},
        }
    except Exception as exc:
        return {
            "version": None,
            "availableProviders": [],
            "recommendedProvider": None,
            "cpuAvailable": False,
            "accelerated": False,
            "error": str(exc),
        }
```

`_check_requirements()` 对规范名 `onnxruntime` 依次读取 `onnxruntime`、`onnxruntime-gpu` 的已安装版本，取第一个存在项；其他依赖保持原校验逻辑。`_call_load()` 在 `ctx` 中加入：

```python
"runtime": {
    "platform": sys.platform,
    "onnxRuntimeVersion": capabilities["version"],
    "availableProviders": capabilities["availableProviders"],
    "recommendedProvider": capabilities["recommendedProvider"],
}
```

在 `backend/main.py` 增加：

```python
@app.get("/api/v1/runtime/capabilities")
async def runtime_capabilities() -> dict[str, Any]:
    return ok({
        "database": {"ready": True, "path": str(DATA_DIR / "digivision.db")},
        "inference": model_service.runtime_capabilities(),
        "camera": {"vendors": camera_service.vendors()},
    })
```

- [ ] **Step 4: 运行后端测试**

Run: `uv run python -m unittest tests.test_runtime_logic tests.test_api_lifecycle -v`

Expected: PASS；能力接口报告真实 Provider，MVS 缺失不导致接口失败。

### Task 2: 让同一模型脚本按真实 Provider 跨平台运行

**Files:**
- Modify: `/Users/leo/Downloads/models/hdw-fasteners-detect/infer.py`
- Modify: `/Users/leo/Downloads/models/hdw-fasteners-detect/manifest.json`
- Create: `tests/test_hdw_model_runtime.py`

**Interfaces:**
- Consumes: `ctx["runtime"]["availableProviders"]: list[str]`
- Produces: `OnnxYoloDetector._provider_candidates(ctx) -> list[str]`
- Produces: 同一份 `.dvmodel` 在 CoreML、CUDA 和 CPU 环境下可加载

- [ ] **Step 1: 写 Provider 选择和 CPU 回退的失败测试**

`tests/test_hdw_model_runtime.py` 使用 `importlib.util.spec_from_file_location()` 加载模型脚本，并用假的 ONNX Runtime Session 验证：

```python
def test_provider_priority_uses_actual_runtime_capability(self):
    self.assertEqual(
        self.module.OnnxYoloDetector._provider_candidates({
            "runtime": {"availableProviders": [
                "CUDAExecutionProvider", "CPUExecutionProvider"
            ]}
        }),
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

def test_session_retries_with_cpu_after_accelerator_failure(self):
    calls = []
    def create(path, providers):
        calls.append(providers)
        if providers != ["CPUExecutionProvider"]:
            raise RuntimeError("accelerator failed")
        return object()
    with patch.object(self.module.ort, "InferenceSession", side_effect=create):
        session = self.module.OnnxYoloDetector._create_session(
            "model.onnx", {"runtime": {"availableProviders": [
                "CoreMLExecutionProvider", "CPUExecutionProvider"
            ]}}
        )
    self.assertIsNotNone(session)
    self.assertEqual(calls[-1], ["CPUExecutionProvider"])
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_hdw_model_runtime -v`

Expected: FAIL，原因为脚本仍根据 `sys.platform` 选择 Provider，且 `_create_session` 不接收运行时上下文。

- [ ] **Step 3: 修改模型脚本和最低运行库声明**

`OnnxYoloDetector` 保存 `ctx`，按实际 Provider 选择：

```python
@staticmethod
def _provider_candidates(ctx):
    declared = (ctx.get("runtime") or {}).get("availableProviders")
    available = set(declared or ort.get_available_providers())
    return [name for name in (
        "CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"
    ) if name in available]
```

首次创建使用候选列表；失败且 CPU 可用时只用 `CPUExecutionProvider` 重试一次。`manifest.json` 保留 `onnxruntime>=1.22.0`，由平台能力别名处理，不写平台专属依赖名。

- [ ] **Step 4: 运行模型脚本测试并生成验收包**

Run: `uv run python -m unittest tests.test_hdw_model_runtime -v`

Expected: PASS。

Run: `cd /Users/leo/Downloads/models/hdw-fasteners-detect && zip -qr /Users/leo/Downloads/models/hdw-fasteners-detect.dvmodel manifest.json infer.py weights`

Expected: 生成可供 PyInstaller smoke test 导入的 `hdw-fasteners-detect.dvmodel`。

### Task 3: 实现真实启动状态 Splash 和窗口切换

**Files:**
- Create: `splash.html`
- Create: `src/splash.tsx`
- Create: `src/splash.css`
- Create: `src/splash-logic.ts`
- Create: `tests/splash-logic.test.mjs`
- Modify: `src/api.ts`
- Modify: `src/main.tsx`
- Modify: `vite.config.ts`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/tauri.conf.json`
- Modify: `src-tauri/capabilities/default.json`
- Modify: `package.json`

**Interfaces:**
- Consumes: `GET /api/v1/runtime/capabilities`
- Produces: `inspectRuntime() -> Promise<RuntimeCapabilities>`
- Produces: Tauri commands `finish_splash`, `restart_backend`, `backend_log_path`
- Produces: 多页面入口 `index.html` 和 `splash.html`

- [ ] **Step 1: 写 Splash 状态归并的失败测试**

在 `tests/splash-logic.test.mjs` 固定阻断和非阻断规则：

```javascript
test('MVS unavailable warns but does not block', () => {
  const result = summarizeCapabilities({
    database: { ready: true },
    inference: {
      cpuAvailable: true,
      recommendedProvider: 'CPUExecutionProvider',
      accelerated: false,
    },
    camera: { vendors: [{ id: 'hikvision', available: false, error: 'missing' }] },
  })
  assert.equal(result.blocked, false)
  assert.equal(result.steps.camera.tone, 'warning')
})

test('missing CPU provider blocks startup', () => {
  const result = summarizeCapabilities({
    database: { ready: true },
    inference: { cpuAvailable: false, availableProviders: [] },
    camera: { vendors: [] },
  })
  assert.equal(result.blocked, true)
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test tests/splash-logic.test.mjs`

Expected: FAIL，原因为 `src/splash-logic.ts` 尚不存在。

- [ ] **Step 3: 实现纯状态逻辑和前端运行时接口**

`src/splash-logic.ts` 定义 `RuntimeCapabilities`、`SplashSummary` 和 `summarizeCapabilities()`。完成度只由已完成阶段数量计算；MVS 缺失为 `warning`，加速 Provider 缺失为 CPU 模式，SQLite 或 CPU Provider 缺失为 `error`。

`src/api.ts` 导出：

```typescript
export async function inspectRuntime(): Promise<RuntimeCapabilities> {
  await initializeRuntime()
  return request<RuntimeCapabilities>('/runtime/capabilities')
}
```

- [ ] **Step 4: 实现 Splash 页面**

`splash.html` 仅挂载 `src/splash.tsx`。页面固定展示品牌、当前阶段、四项真实状态、动态完成度、CPU 回退提示和 MVS 非阻断提示。错误态显示“重试”和日志路径；不显示固定精度、固定延迟或伪造进度。

`vite.config.ts` 使用 Rollup 多入口：

```typescript
build: {
  rollupOptions: {
    input: {
      main: resolve(__dirname, 'index.html'),
      splash: resolve(__dirname, 'splash.html'),
    },
  },
},
```

- [ ] **Step 5: 实现 Tauri 双窗口、重试和切换命令**

`src-tauri/tauri.conf.json` 中：

```json
{
  "label": "main",
  "url": "index.html",
  "visible": false
}
```

并新增 `splashscreen` 窗口，指向 `splash.html`，无边框、不可缩放、居中。`finish_splash` 只在主窗口与 Splash 都存在时关闭 Splash，然后显示、居中并聚焦主窗口。`restart_backend` 停止现有子进程、清空错误和停止标志并重新启动监督线程；`backend_log_path` 返回数据目录下的日志绝对路径。

- [ ] **Step 6: 运行前端测试、Rust 检查和生产构建**

Run: `npm run test:splash && npm run test:config && npm run test:workflow`

Expected: PASS。

Run: `cargo check --manifest-path src-tauri/Cargo.toml`

Expected: PASS。

Run: `npm run build`

Expected: `dist/index.html` 和 `dist/splash.html` 均存在，TypeScript 和 Vite 构建通过。

### Task 4: 按平台锁定推理依赖并生成原生安装器

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `scripts/build_app.py`
- Modify: `scripts/build_app_smoke.py`
- Modify: `src-tauri/tauri.conf.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: 当前平台 `sys.platform`
- Produces: macOS `.app` 和 `.dmg`
- Produces: Windows NSIS Setup `.exe`
- Produces: `python scripts/build_app.py --backend-only` 独立后端目录

- [ ] **Step 1: 写构建参数的失败测试**

把构建目标和平台依赖选择提取成纯函数，并在 `scripts/build_app_smoke.py` 中增加 `unittest` 可执行模式：

```python
class BuildConfigurationTests(unittest.TestCase):
    def test_macos_bundles_app_and_dmg(self):
        self.assertEqual(bundle_targets("darwin"), "app,dmg")

    def test_windows_bundles_nsis(self):
        self.assertEqual(bundle_targets("win32"), "nsis")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest scripts.build_app_smoke.BuildConfigurationTests -v`

Expected: FAIL，原因为 `bundle_targets` 尚不存在。

- [ ] **Step 3: 配置平台依赖和构建目标**

`pyproject.toml` 使用环境标记：

```toml
"onnxruntime>=1.22.0; sys_platform != 'win32'",
"onnxruntime-gpu>=1.22.0; sys_platform == 'win32'",
```

Run: `uv lock`

Expected: `uv.lock` 同时记录按平台筛选的 CPU/CoreML 与 GPU 发行包。

`scripts/build_app.py` 新增 `bundle_targets(platform)`，macOS 返回 `app,dmg`，Windows 返回 `nsis`。构建完成后检查预期文件存在；macOS 对 `.app` 深层 ad-hoc 签名并再次校验，因为 DMG 必须包含已签名 App。构建顺序为后端、前端、App、签名、DMG，避免签名后重新改写 App。

`src-tauri/tauri.conf.json` 配置 DMG 和 NSIS 的标准安装行为，不写正式签名身份。README 记录 MVS 前置依赖、无 Python/Node.js 要求、macOS 本地包限制和 Windows 构建命令。

- [ ] **Step 4: 运行构建配置和全量源码测试**

Run: `uv run python -m unittest scripts.build_app_smoke.BuildConfigurationTests tests.test_runtime_logic tests.test_api_lifecycle tests.test_branch_execution -v`

Expected: PASS。

Run: `npm run test:splash && npm run test:config && npm run test:workflow && npm run build`

Expected: PASS。

### Task 5: 构建并验收 macOS DMG

**Files:**
- Verify: `src-tauri/target/release/bundle/macos/DigiVision.app`
- Verify: `src-tauri/target/release/bundle/dmg/DigiVision_0.1.0_aarch64.dmg`
- Verify: `/Users/leo/Downloads/models/hdw-fasteners-detect.dvmodel`

**Interfaces:**
- Consumes: Task 1 至 Task 4 的后端、Splash、模型包和构建配置
- Produces: 本机可安装验证的 macOS DMG

- [ ] **Step 1: 构建独立后端并运行隔离 smoke test**

Run: `npm run app:backend`

Expected: `src-tauri/resources/digivision-backend/digivision-backend` 存在。

Run: `uv run python scripts/build_app_smoke.py src-tauri/resources/digivision-backend/digivision-backend --sample-package /Users/leo/Downloads/models/hdw-fasteners-detect.dvmodel --image samples/demo.jpg`

Expected: 输出包含“动态模型导入”“OpenCV_NumPy图片推理”“正式流程记录”和最终 `result: 通过`。

- [ ] **Step 2: 构建 App 和 DMG**

Run: `npm run app:build`

Expected: 同时生成 `.app` 和 `DigiVision_0.1.0_aarch64.dmg`，命令退出码为 0。

- [ ] **Step 3: 校验签名、架构和安装镜像**

Run: `codesign --verify --deep --strict --verbose=2 src-tauri/target/release/bundle/macos/DigiVision.app`

Expected: 校验成功。

Run: `file src-tauri/target/release/bundle/macos/DigiVision.app/Contents/MacOS/digivision-demo`

Expected: 输出包含 `arm64`。

Run: `hdiutil verify src-tauri/target/release/bundle/dmg/DigiVision_0.1.0_aarch64.dmg`

Expected: 镜像校验成功。

- [ ] **Step 4: 从 DMG 启动并执行可视化验收**

挂载 DMG，将 App 复制到临时目录后启动。确认 Splash 先出现，SQLite、Provider 和 MVS 状态与本机实际情况一致，随后主窗口显示；MVS 状态不会阻断进入主界面。退出 App 后确认没有遗留 `digivision-backend` 进程。

- [ ] **Step 5: 记录 Windows 构建机下一步**

在验收结果中记录 Windows 构建命令 `npm run app:build`、预期 NSIS 产物路径，以及待在 Windows x64 上执行的 CUDA、CPU 回退、MVS、安装和卸载检查。本任务不声称 Windows EXE 已构建或已验证。
