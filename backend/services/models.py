from __future__ import annotations

import importlib.metadata
import importlib.util
import inspect
import io
import json
import math
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np

from ..config import MODELS_DIR, ensure_data_dirs
from ..db import connect, new_id, now_iso


class ModelPackageError(RuntimeError):
    pass


@dataclass(slots=True)
class LoadedModel:
    db_id: str
    model_id: str
    dir: Path
    manifest: dict[str, Any]
    module: Any = None
    session: Any = None
    status: str = "stopped"
    error: str = ""


class ModelService:
    def __init__(self) -> None:
        self._loaded: dict[str, LoadedModel] = {}
        self._lock = threading.RLock()
        self.in_use = lambda model_id: False

    def _item(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["manifest"] = json.loads(item.pop("manifest_json"))
        runtime = self._loaded.get(item["id"])
        item["serviceStatus"] = runtime.status if runtime else ("error" if item["service_status"] == "error" else "stopped")
        item["lastError"] = runtime.error if runtime else item["last_error"]
        item["service_status"] = item["serviceStatus"]
        return item

    def list_models(self, enabled: bool | None = None, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock, connect() as conn:
            items = [self._item(row) for row in conn.execute("SELECT * FROM models ORDER BY imported_at DESC").fetchall()]
        return [item for item in items if (enabled is None or item["enabled"] == enabled) and (not status or item["serviceStatus"] == status)]

    def get(self, model_pk: str) -> dict[str, Any] | None:
        with self._lock, connect() as conn:
            row = conn.execute("SELECT * FROM models WHERE id = ? OR model_id = ?", (model_pk, model_pk)).fetchone()
            return self._item(row) if row else None

    def devices(self) -> list[dict[str, Any]]:
        """只报告已安装运行库真实暴露的执行能力，不猜 GPU 型号或显存。"""
        devices = [{"id": "cpu", "name": "CPU", "type": "cpu", "available": True, "providers": []}]
        try:
            import onnxruntime as ort
            providers = ort.get_available_providers()
            devices[0]["providers"] = [p for p in providers if p == "CPUExecutionProvider"]
            for provider in providers:
                if provider != "CPUExecutionProvider":
                    devices.append({"id": provider, "name": provider, "type": "accelerator", "available": True,
                                    "providers": [provider], "runtime": "onnxruntime"})
        except ImportError:
            pass
        return devices

    def runtime_capabilities(self) -> dict[str, Any]:
        return _runtime_capabilities()

    def _guard_mutation(self, item: dict[str, Any], replacement: bool = False) -> None:
        if self.in_use(item["id"]) or self.in_use(item["model_id"]):
            raise ModelPackageError("流程正在使用该模型，不能停止、删除或替换")
        if replacement and item["serviceStatus"] in {"running", "starting"}:
            raise ModelPackageError("模型正在运行，请先停止推理再替换模型包")

    def import_package(self, filename: str, content: bytes) -> dict[str, Any]:
        if not filename.lower().endswith(".dvmodel"):
            raise ModelPackageError("模型包扩展名必须是 .dvmodel")
        ensure_data_dirs()
        with self._lock:
            staging: Path | None = None
            target: Path | None = None
            backup: Path | None = None
            installed = False
            try:
                manifest, staging = self._extract_package(content)
                model_id = manifest["modelId"]
                old = self.get(model_id)
                if old: self._guard_mutation(old, replacement=True)
                pk = old["id"] if old else new_id("model")
                probe = self._load_runtime({"id": pk, "model_id": model_id, "package_path": str(staging), "manifest": manifest})
                self._unload(probe)
                stale = self._loaded.get(pk)
                if stale:
                    self._unload(stale)
                    self._loaded.pop(pk, None)
                target = MODELS_DIR / model_id
                if target.exists():
                    backup = MODELS_DIR / f".backup-{uuid.uuid4().hex}"
                    target.rename(backup)
                staging.rename(target)
                installed = True
                timestamp = now_iso()
                with connect() as conn:
                    conn.execute("""INSERT INTO models(id, model_id, name, version, description, manifest_json, package_path,
                                enabled, service_status, last_error, imported_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?, ?, 0, 'stopped', '', ?, ?)
                        ON CONFLICT(model_id) DO UPDATE SET name=excluded.name, version=excluded.version,
                        description=excluded.description, manifest_json=excluded.manifest_json, package_path=excluded.package_path,
                        enabled=0, service_status='stopped', last_error='', imported_at=excluded.imported_at, updated_at=excluded.updated_at""",
                        (pk, model_id, manifest["name"], manifest["version"], manifest.get("description", ""),
                         json.dumps(manifest, ensure_ascii=False), str(target), timestamp, timestamp))
                if backup: shutil.rmtree(backup, ignore_errors=True)
                return self.get(pk) or {}
            except Exception as exc:
                if installed and target and target.exists(): shutil.rmtree(target)
                if backup and backup.exists() and target: backup.rename(target)
                if isinstance(exc, ModelPackageError): raise
                raise ModelPackageError(f"模型导入失败：{exc}") from exc
            finally:
                if staging and staging.exists(): shutil.rmtree(staging)

    def _extract_package(self, content: bytes | Path) -> tuple[dict[str, Any], Path]:
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=MODELS_DIR))
        try:
            with zipfile.ZipFile(io.BytesIO(content) if isinstance(content, bytes) else content) as zf:
                members = zf.infolist()
                names = [m.filename for m in members]
                if len(names) != len(set(names)): raise ModelPackageError("模型包包含重复文件")
                if len(names) > 10000 or sum(m.file_size for m in members) > 4 * 1024**3:
                    raise ModelPackageError("模型包解压大小或文件数量超限")
                for member in members:
                    _package_path(member.filename.rstrip("/"))
                    if stat.S_ISLNK(member.external_attr >> 16): raise ModelPackageError("模型包不能包含符号链接")
                manifests = [n for n in names if PurePosixPath(n).name == "manifest.json" and len(PurePosixPath(n).parts) <= 2]
                if len(manifests) != 1: raise ModelPackageError("模型包必须包含唯一的 manifest.json")
                manifest_name = manifests[0]
                parent = str(PurePosixPath(manifest_name).parent)
                prefix = "" if parent == "." else parent + "/"
                relative_names = [n[len(prefix):] for n in names if n.startswith(prefix)]
                manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
                self._validate_manifest(manifest, relative_names)
                for member in members:
                    if member.is_dir() or not member.filename.startswith(prefix): continue
                    name = member.filename[len(prefix):]
                    if not name: continue
                    destination = staging / _package_path(name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
                return manifest, staging
        except Exception:
            shutil.rmtree(staging)
            raise

    def _validate_manifest(self, manifest: dict[str, Any], zip_names: list[str]) -> None:
        if not isinstance(manifest, dict): raise ModelPackageError("manifest 必须为对象")
        for key in ("manifestVersion", "modelId", "name", "version", "tasks", "script", "weights"):
            if key not in manifest: raise ModelPackageError(f"manifest.json 缺少字段：{key}")
        if type(manifest["manifestVersion"]) is not int or manifest["manifestVersion"] != 2:
            raise ModelPackageError("当前只支持 manifestVersion=2")
        if not isinstance(manifest["modelId"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", manifest["modelId"]):
            raise ModelPackageError("modelId 只能包含字母、数字、下划线和连字符")
        for field in ("name", "version"):
            if not isinstance(manifest[field], str) or not manifest[field].strip(): raise ModelPackageError(f"{field} 不能为空")
        tasks = manifest["tasks"]
        if not isinstance(tasks, list) or not tasks: raise ModelPackageError("manifest.tasks 不能为空")
        task_keys = set()
        for task in tasks:
            if not isinstance(task, dict): raise ModelPackageError("task 必须为对象")
            for key in ("key", "type", "name", "labels"):
                if key not in task: raise ModelPackageError(f"task 缺少字段：{key}")
            if not isinstance(task["key"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", task["key"]) or task["key"] in task_keys:
                raise ModelPackageError("task.key 无效或重复")
            task_keys.add(task["key"])
            if not isinstance(task["labels"], list) or not task["labels"]: raise ModelPackageError("task.labels 不能为空")
            labels = set()
            for label in task["labels"]:
                if not isinstance(label, dict) or not isinstance(label.get("label"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", label["label"]) or not isinstance(label.get("name"), str) or not label["name"]:
                    raise ModelPackageError("类别 label/name 无效")
                if label["label"] in labels: raise ModelPackageError("类别标识重复")
                labels.add(label["label"])
        if not isinstance(manifest["weights"], list) or not manifest["weights"]:
            raise ModelPackageError("weights 必须包含真实权重文件")
        for name in [manifest["script"], *manifest["weights"]]:
            _package_path(name)
            if name not in zip_names or name.endswith("/"):
                raise ModelPackageError(f"模型包缺少文件：{name}")
        if not manifest["script"].endswith(".py"): raise ModelPackageError("推理脚本必须为 Python 文件")
        params = manifest.get("params", [])
        if not isinstance(params, list): raise ModelPackageError("params 必须为数组")
        seen = set()
        for param in params:
            if not isinstance(param, dict) or not isinstance(param.get("name"), str) or not param["name"] or param["name"] in seen:
                raise ModelPackageError("模型参数名称无效或重复")
            seen.add(param["name"])
            if param.get("type") not in {"float", "int", "bool", "str", "string"}:
                raise ModelPackageError("模型参数类型不支持")
            if "default" in param: _parameter(param, param["default"])
        _check_requirements(manifest.get("requires", []))

    def start(self, model_pk: str) -> dict[str, Any]:
        with self._lock:
            item = self.get(model_pk)
            if not item: raise ModelPackageError("模型不存在")
            db_id = item["id"]
            loaded = self._loaded.get(db_id)
            if loaded and loaded.status == "running": return item
            try:
                if loaded: self._unload(loaded)
                loaded = self._load_runtime(item)
                self._loaded[db_id] = loaded
                self._update_status(db_id, True, "running", "")
            except Exception as exc:
                self._loaded[db_id] = LoadedModel(db_id, item["model_id"], Path(item["package_path"]), item["manifest"], status="error", error=str(exc))
                self._update_status(db_id, True, "error", str(exc))
                raise ModelPackageError(f"模型启动失败：{exc}") from exc
            return self.get(db_id) or item

    def stop(self, model_pk: str) -> dict[str, Any]:
        with self._lock:
            item = self.get(model_pk)
            if not item: raise ModelPackageError("模型不存在")
            self._guard_mutation(item)
            loaded = self._loaded.get(item["id"])
            if loaded: self._unload(loaded)
            self._loaded.pop(item["id"], None)
            self._update_status(item["id"], False, "stopped", "")
            return self.get(item["id"]) or item

    def delete(self, model_pk: str) -> None:
        with self._lock:
            item = self.get(model_pk)
            if not item: raise ModelPackageError("模型不存在")
            self._guard_mutation(item)
            self.stop(item["id"])
            with connect() as conn: conn.execute("DELETE FROM models WHERE id = ?", (item["id"],))
            path = Path(item["package_path"]).resolve()
            if MODELS_DIR.resolve() in path.parents and path.exists(): shutil.rmtree(path)

    def run(self, model_pk: str, image: np.ndarray, params: dict[str, Any]) -> tuple[dict[str, Any], int]:
        with self._lock:
            item = self.get(model_pk)
            if not item: raise ModelPackageError("模型不存在")
            loaded = self._loaded.get(item["id"])
            if not item["enabled"] or not loaded or loaded.status != "running":
                raise ModelPackageError("模型推理服务未启动，请先到模型管理开启推理")
            if not isinstance(image, np.ndarray) or image.size == 0 or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
                raise ModelPackageError("模型输入必须为非空 uint8 三通道图像")
            args = {}
            for declaration in loaded.manifest.get("params", []):
                name = declaration["name"]
                if name not in params and "default" not in declaration: raise ModelPackageError(f"模型参数缺值：{name}")
                args[name] = _parameter(declaration, params.get(name, declaration.get("default")))
            started = time.perf_counter()
            try:
                # OpenCV 采集和保存采用 BGR；模型脚本契约采用 RGB。
                outputs = _call_run(loaded.module.run, cv2.cvtColor(image, cv2.COLOR_BGR2RGB), args, loaded.session, loaded.dir)
                keys = [f"{t['key']}:{l['label']}:{suffix}" for t in loaded.manifest["tasks"] for l in t["labels"] for suffix in ("detected", "count", "regions")]
                if not isinstance(outputs, dict): raise ModelPackageError("模型脚本返回值必须为对象")
                outputs = {key: outputs[key] for key in keys if key in outputs}
                _validate_outputs(loaded.manifest, outputs)
                loaded.error = ""
                self._update_status(item["id"], True, "running", "")
                return outputs, int((time.perf_counter()-started)*1000)
            except Exception as exc:
                # 单轮失败不销毁已加载会话，保留下一次诊断/执行机会。
                loaded.error = str(exc)
                self._update_status(item["id"], True, "running", str(exc))
                raise ModelPackageError(f"模型推理失败：{exc}") from exc

    def _load_runtime(self, item: dict[str, Any]) -> LoadedModel:
        model_dir = Path(item["package_path"]).resolve()
        manifest = item["manifest"]
        self._validate_manifest(manifest, [str(p.relative_to(model_dir).as_posix()) for p in model_dir.rglob("*") if p.is_file()])
        script_path = model_dir / _package_path(manifest["script"])
        for relative in [manifest["script"], *manifest["weights"]]:
            path = model_dir / relative
            if not path.is_file() or path.stat().st_size == 0 or model_dir not in path.resolve().parents:
                raise ModelPackageError(f"模型文件不存在、为空或越界：{relative}")
        module_name = f"digivision_model_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if not spec or not spec.loader: raise ModelPackageError("无法加载推理脚本")
        module = importlib.util.module_from_spec(spec)
        loaded = LoadedModel(item["id"], item["model_id"], model_dir, manifest, module=module)
        try:
            spec.loader.exec_module(module)
            for name in ("load", "run"):
                function = getattr(module, name, None)
                if not callable(function) or inspect.iscoroutinefunction(function):
                    raise ModelPackageError(f"推理脚本必须定义同步 {name} 函数")
            loaded.session = _call_load(module.load, model_dir, manifest, _runtime_capabilities())
            loaded.status = "running"
            return loaded
        except Exception:
            try: self._unload(loaded)
            except Exception: pass
            raise

    @staticmethod
    def _unload(loaded: LoadedModel) -> None:
        function = getattr(loaded.module, "unload", None)
        if callable(function): _call_unload(function, loaded.session)
        loaded.session = None
        loaded.module = None
        loaded.status = "stopped"

    def _update_status(self, db_id: str, enabled: bool, status: str, error: str) -> None:
        with connect() as conn:
            conn.execute("UPDATE models SET enabled = ?, service_status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                         (int(enabled), status, error, now_iso(), db_id))

    def stop_all(self) -> None:
        for model_pk in list(self._loaded): self.stop(model_pk)

    def unload_all(self) -> None:
        with self._lock:
            errors = []
            for db_id, loaded in list(self._loaded.items()):
                try:
                    self._unload(loaded)
                    self._loaded.pop(db_id, None)
                    with connect() as conn:
                        conn.execute("UPDATE models SET service_status='stopped', last_error='', updated_at=? WHERE id=?", (now_iso(), db_id))
                except Exception as exc:
                    loaded.status, loaded.error = "error", str(exc)
                    errors.append(str(exc))
            if errors: raise ModelPackageError("模型资源释放失败：" + "; ".join(errors))


def _package_path(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value or ":" in value:
        raise ModelPackageError("模型包含非法路径")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) == ".": raise ModelPackageError("模型包含非法路径")
    return Path(*path.parts)


def _check_requirements(requirements: Any) -> None:
    if isinstance(requirements, dict): requirements = [f"{key}{value or ''}" for key, value in requirements.items()]
    if not isinstance(requirements, list): raise ModelPackageError("requires 必须为依赖数组或版本映射")
    if not requirements: return
    try:
        from packaging.requirements import Requirement
        from packaging.version import Version
        for text in requirements:
            requirement = Requirement(text)
            if requirement.url: raise ModelPackageError("不支持联网依赖")
            if requirement.marker and not requirement.marker.evaluate(): continue
            package_names = (["onnxruntime", "onnxruntime-gpu"]
                             if requirement.name.lower() == "onnxruntime"
                             else [requirement.name])
            installed = None
            last_error = None
            for package_name in package_names:
                try:
                    installed = importlib.metadata.version(package_name)
                    break
                except importlib.metadata.PackageNotFoundError as exc:
                    last_error = exc
            if installed is None:
                raise last_error or importlib.metadata.PackageNotFoundError(requirement.name)
            if requirement.specifier and Version(installed) not in requirement.specifier:
                raise ModelPackageError(f"依赖版本不满足：{text}，当前 {installed}")
    except ModelPackageError: raise
    except Exception as exc: raise ModelPackageError(f"模型依赖不可用：{exc}") from exc


def _parameter(declaration: dict[str, Any], value: Any) -> Any:
    name, kind = declaration["name"], declaration["type"]
    valid = ((kind == "int" and type(value) is int) or (kind == "float" and type(value) in {int, float})
             or (kind == "bool" and type(value) is bool) or (kind in {"str", "string"} and isinstance(value, str)))
    if not valid: raise ModelPackageError(f"模型参数类型错误：{name}")
    if type(value) in {int, float}:
        if not math.isfinite(value): raise ModelPackageError(f"模型参数不是有限数值：{name}")
        if "min" in declaration and value < declaration["min"]: raise ModelPackageError(f"模型参数小于最小值：{name}")
        if "max" in declaration and value > declaration["max"]: raise ModelPackageError(f"模型参数超过最大值：{name}")
    return value


def _call_load(func: Any, model_dir: Path, manifest: dict[str, Any], runtime: dict[str, Any] | None = None) -> Any:
    variables = []
    for task in manifest["tasks"]:
        for label in task["labels"]:
            for suffix, kind, name in (("detected", "Bool", "检出状态"), ("count", "Int", "个数"), ("regions", "List<Region>", "区域")):
                variables.append({"variable": f"{task['key']}:{label['label']}:{suffix}", "displayName": f"{task['name']}：{label['name']}-{name}", "type": kind})
    ctx = {"weights": [str((model_dir / w).resolve()) for w in manifest["weights"]], "tasks": manifest["tasks"],
           "inputSize": manifest.get("inputSize"), "resourcesDir": str(model_dir / "resources") if (model_dir / "resources").is_dir() else None,
           "modelId": manifest["modelId"], "variables": variables,
           "runtime": runtime or _runtime_capabilities()}
    parameters = list(inspect.signature(func).parameters.values())
    if not parameters: return _sync_result(func())
    # 兼容已有 load(model_dir[, manifest]) 包；文档契约 load(ctx) 为默认。
    if len(parameters) > 1: return _sync_result(func(model_dir, manifest))
    return _sync_result(func(model_dir if parameters[0].name in {"model_dir", "model_path"} else ctx))


def _runtime_capabilities() -> dict[str, Any]:
    try:
        import onnxruntime as ort
        providers = list(ort.get_available_providers())
        preferred = ["CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
        recommended = next((name for name in preferred if name in providers), None)
        return {
            "platform": sys.platform,
            "version": getattr(ort, "__version__", None),
            "availableProviders": providers,
            "recommendedProvider": recommended,
            "cpuAvailable": "CPUExecutionProvider" in providers,
            "accelerated": recommended not in {None, "CPUExecutionProvider"},
        }
    except Exception as exc:
        return {
            "platform": sys.platform,
            "version": None,
            "availableProviders": [],
            "recommendedProvider": None,
            "cpuAvailable": False,
            "accelerated": False,
            "error": str(exc),
        }


def _sync_result(value: Any) -> Any:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value): value.close()
        raise ModelPackageError("模型脚本不支持异步函数")
    return value


def _call_run(func: Any, image: np.ndarray, params: dict[str, Any], session: Any, model_dir: Path) -> dict[str, Any]:
    count = len(inspect.signature(func).parameters)
    if count <= 1: return _sync_result(func(image))
    if count == 2: return _sync_result(func(image, params))
    return _sync_result(func(image, params, session))


def _call_unload(func: Any, session: Any) -> None:
    _sync_result(func() if not inspect.signature(func).parameters else func(session))


def _validate_outputs(manifest: dict[str, Any], outputs: dict[str, Any]) -> None:
    if not isinstance(outputs, dict): raise ModelPackageError("模型脚本返回值必须是 JSON 对象")
    for task in manifest["tasks"]:
        for label in task["labels"]:
            base = f"{task['key']}:{label['label']}:"
            for suffix, expected in (("detected", bool), ("count", int), ("regions", list)):
                key = base + suffix
                if key not in outputs: raise ModelPackageError(f"模型脚本缺少输出变量：{key}")
                if type(outputs[key]) is not expected: raise ModelPackageError(f"模型输出变量类型错误：{key}")
            if outputs[base + "count"] < 0: raise ModelPackageError("模型输出个数不能为负数")
            for region in outputs[base + "regions"]:
                if not isinstance(region, dict) or region.get("label") != label["label"]:
                    raise ModelPackageError("模型区域类别与声明不匹配")
                if region.get("labelName") != label["name"]:
                    raise ModelPackageError("模型区域类别名称与声明不匹配")
                score, bbox = region.get("score"), region.get("bbox")
                if type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ModelPackageError("模型区域 score 必须为 0~1 的有限数值")
                if not isinstance(bbox, list) or len(bbox) != 4 or any(type(v) not in {int, float} or not math.isfinite(v) for v in bbox) or bbox[2] < 0 or bbox[3] < 0:
                    raise ModelPackageError("模型区域 bbox 必须为有效的 [x,y,w,h]")
    try: json.dumps(outputs, allow_nan=False)
    except (TypeError, ValueError) as exc: raise ModelPackageError("模型输出必须可序列化为 JSON") from exc
