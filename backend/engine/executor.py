from __future__ import annotations

import asyncio
import json
import copy
import math
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .. import imaging
from ..config import RESULTS_DIR, ROOT_DIR, AUTH_TOKEN
from ..db import connect, now_iso
from ..events import event_hub
from ..services.camera import CameraService
from ..services.models import ModelService, ModelPackageError


NODE_TYPES = {
    "start",
    "capture",
    "quickCapture",
    "preprocess",
    "calibration",
    "affine",
    "model",
    "branch",
    "display",
    "storage",
    "alarm",
    "plc",
    "end",
}


@dataclass
class ExecutionSession:
    run_id: str
    flow_id: str
    mode: str
    nodes: dict[str, dict[str, Any]]
    status: str = "running"
    current_node_id: str | None = None
    executed: list[str] = field(default_factory=list)
    stop_requested: bool = False
    context: dict[str, Any] = field(default_factory=dict)
    objects: dict[str, Any] = field(default_factory=dict)
    started_perf: float = field(default_factory=time.perf_counter)
    next_node_id: str | None = None
    initialized: bool = False
    windows: dict[str, Any] = field(default_factory=dict)
    node_outputs: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=now_iso)


class WorkflowManager:
    def __init__(self, camera_service: CameraService, model_service: ModelService) -> None:
        self.camera_service = camera_service
        self.model_service = model_service
        self.active: ExecutionSession | None = None
        self._task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()
        self._debugging = False
        self._last_session: ExecutionSession | None = None
        self._directory_indices: dict[tuple[str, str], int] = {}
        if model_service is not None:
            model_service.in_use = self.model_in_use
        if camera_service is not None:
            camera_service.in_use = self.camera_in_use

    def list_flows(self) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute("SELECT * FROM flows ORDER BY sort_order ASC, updated_at DESC").fetchall()
        return [self._flow_row(row, include_nodes=False) for row in rows]

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM flows WHERE id = ?", (flow_id,)).fetchone()
        return self._flow_row(row, include_nodes=True) if row else None

    def save_flow(self, flow_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        flow_id = flow_id or str(payload.get("id") or f"flow-{uuid.uuid4().hex[:8]}")
        name = str(payload.get("name") or "未命名流程")
        description = str(payload.get("description") or "")
        self._guard_flow_edit(flow_id)
        nodes = copy.deepcopy(payload.get("nodes") or [])
        self.validate_flow(nodes, validate_references=False)
        for node in nodes:
            data = node["data"]
            data.pop("status", None)
            data.pop("durationMs", None)
            for output in data.get("outputs") or []:
                output.pop("value", None)
        with connect() as conn:
            exists = conn.execute("SELECT id FROM flows WHERE id = ?", (flow_id,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE flows SET name = ?, description = ?, nodes_json = ?, updated_at = ? WHERE id = ?",
                    (name, description, json.dumps(nodes, ensure_ascii=False), now_iso(), flow_id),
                )
            else:
                conn.execute(
                    "INSERT INTO flows(id, name, description, nodes_json, updated_at, sort_order) VALUES(?, ?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM flows))",
                    (flow_id, name, description, json.dumps(nodes, ensure_ascii=False), now_iso()),
                )
        return self.get_flow(flow_id) or {}

    def create_flow(self, name: str | None = None) -> dict[str, Any]:
        from ..db import default_flow_nodes

        flow_id = f"flow-{uuid.uuid4().hex[:8]}"
        nodes = default_flow_nodes()
        return self.save_flow(flow_id, {"name": name or "新流程", "description": "", "nodes": nodes})

    def delete_flow(self, flow_id: str) -> None:
        self._guard_flow_edit(flow_id)
        with connect() as conn:
            conn.execute("DELETE FROM flows WHERE id = ?", (flow_id,))

    def _guard_flow_edit(self, flow_id: str) -> None:
        if self.active and self.active.flow_id == flow_id and self.active.status in {"running", "paused"}:
            raise RuntimeError("流程执行中，不允许保存或删除")

    def model_in_use(self, model_id: str) -> bool:
        return self._resource_in_use("model", model_id)

    def camera_in_use(self, camera_id: str) -> bool:
        return self._resource_in_use("camera", camera_id)

    def _resource_in_use(self, kind: str, resource_id: str) -> bool:
        session = self.active
        if not session or session.status not in {"running", "paused"}:
            return False
        for node in session.nodes.values():
            data = node.get("data") or {}
            params = data.get("params") or {}
            if kind == "model" and data.get("nodeType") == "model" and params.get("modelId") == resource_id:
                return True
            if kind == "camera" and data.get("nodeType") in {"capture", "quickCapture"} and params.get("source") == "camera" and (params.get("camera") or {}).get("cameraId") == resource_id:
                return True
        return False

    def validate_flow(self, nodes: list[dict[str, Any]], *, validate_references: bool = True) -> None:
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("流程不能为空")
        ids = []
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"].strip():
                raise ValueError("节点 ID 不能为空")
            if any(c in node["id"] for c in ("/", "\\", ".", "\x00")):
                raise ValueError("节点 ID 含非法字符")
            if not isinstance(node.get("data"), dict):
                raise ValueError("节点 data 必须为对象")
            ids.append(node["id"])
        if len(ids) != len(set(ids)):
            raise ValueError("节点 ID 重复")
        by_id = dict(zip(ids, nodes))
        starts = [n for n in nodes if n["data"].get("nodeType") == "start"]
        if len(starts) != 1:
            raise ValueError("流程必须且只能有一个开始节点")
        graph = {}
        for node in nodes:
            data = node["data"]
            if data.get("nodeType") not in NODE_TYPES:
                raise ValueError(f"未知节点类型：{data.get('nodeType')}")
            if not isinstance(data.get("params", {}), dict):
                raise ValueError("节点 params 必须为对象")
            ports = data.get("ports") or {}
            if not isinstance(ports, dict) or not isinstance(ports.get("dynamic", []), list):
                raise ValueError("节点 ports 格式错误")
            dynamic = ports.get("dynamic", [])
            if any(not isinstance(item, dict) or not item.get("id") for item in dynamic):
                raise ValueError("动态出口缺少 ID")
            if len({item["id"] for item in dynamic}) != len(dynamic):
                raise ValueError("动态出口 ID 重复")
            targets = [_next_builtin(node)] + [item.get("targetId") for item in dynamic]
            for target in targets:
                if target is not None and (not isinstance(target, str) or target not in by_id):
                    raise ValueError(f"节点 {node['id']} 指向不存在的节点 {target}")
            graph[node["id"]] = [t for t in targets if t is not None]
            if data.get("nodeType") == "branch" and (data.get("branch") or {}).get("role") not in {"else", "end"}:
                conditions = data.get("params", {}).get("conditions", [])
                if not isinstance(conditions, list) or any(not isinstance(c, dict) or not c.get("id") for c in conditions):
                    raise ValueError("条件列表格式错误")
                condition_ids = [c["id"] for c in conditions]
                if len(set(condition_ids)) != len(condition_ids) or set(condition_ids) != {d["id"] for d in dynamic}:
                    raise ValueError("条件 ID 与动态出口必须一一对应")
        visiting, done = set(), set()
        def visit(nid):
            if nid in visiting:
                raise ValueError("流程存在环路")
            if nid in done:
                return
            visiting.add(nid)
            for target in graph[nid]:
                visit(target)
            visiting.remove(nid)
            done.add(nid)
        for nid in ids:
            visit(nid)
        # 保存时允许未配置的空引用；已有引用必须来自所有路径均经过的上游。
        start_id = starts[0]["id"]
        reachable = set()
        def reach(nid):
            if nid not in reachable:
                reachable.add(nid)
                for target in graph[nid]: reach(target)
        reach(start_id)
        if reachable != set(ids):
            raise ValueError("流程包含从开始节点不可达的孤立节点：" + "、".join(sorted(set(ids) - reachable)))
        # 编辑草稿允许暂时失效的引用，运行/调试入口仍严格校验。
        if not validate_references:
            return
        predecessors = {nid: set() for nid in reachable}
        for source in reachable:
            for target in graph[source]: predecessors[target].add(source)
        dominators = {nid: ({nid} if nid == start_id else set(reachable)) for nid in reachable}
        changed = True
        while changed:
            changed = False
            for nid in reachable - {start_id}:
                value = {nid} | set.intersection(*(dominators[p] for p in predecessors[nid]))
                if value != dominators[nid]: dominators[nid] = value; changed = True
        for node in nodes:
            for value in _walk_values(node["data"].get("params", {})):
                if value.get("type") != "ref" or not value.get("ref"):
                    continue
                reference = value["ref"]
                if not isinstance(reference, str) or "." not in reference:
                    raise ValueError("输入引用格式错误")
                source = reference.split(".", 1)[0]
                if source not in by_id or source == node["id"] or source not in dominators.get(node["id"], set()):
                    raise ValueError(f"引用必须来自当前节点的确定上游：{reference}")

    async def start_run(self, flow_id: str, mode: str = "run") -> str:
        if mode not in {"run", "step"}:
            raise ValueError("mode 只能是 run 或 step")
        async with self._lock:
            if self._debugging or (self.active and self.active.status in {"running", "paused"}):
                raise RuntimeError("已有流程正在执行")
            flow = self.get_flow(flow_id)
            if not flow:
                raise RuntimeError("流程不存在")
            self.validate_flow(flow["nodes"])
            session = ExecutionSession(
                run_id=f"run-{uuid.uuid4().hex[:12]}",
                flow_id=flow_id,
                mode=mode,
                nodes={node["id"]: node for node in flow["nodes"]},
            )
            self.active = session
            self._task = asyncio.create_task(self._drive_session(session))
            return session.run_id

    async def step(self, run_id: str) -> None:
        async with self._lock:
            session = self._require_session(run_id)
            if session.status != "paused":
                raise RuntimeError("当前状态不允许单步")
            session.status = "running"
            self._task = asyncio.create_task(self._drive_session(session, max_steps=1))

    async def stop(self, run_id: str) -> None:
        async with self._lock:
            session = self._require_session(run_id)
            session.stop_requested = True
            if session.status == "paused":
                session.status = "stopped"
                self._last_session = session
                await self._emit(session, {"type": "run.stopped", "durationMs": int((time.perf_counter() - session.started_perf) * 1000)})
                if self.active is session:
                    self.active = None

    def active_info(self) -> dict[str, Any] | None:
        session = self.active
        if not session or session.mode == "debug" or session.status not in {"running", "paused"}:
            return None
        return {
            "runId": session.run_id,
            "flowId": session.flow_id,
            "mode": session.mode,
            "status": session.status,
            "executed": list(session.executed),
            "currentNodeId": session.current_node_id,
            "windows": copy.deepcopy(session.windows),
            "nodeOutputs": copy.deepcopy(session.node_outputs),
            "context": copy.deepcopy(session.context),
            **_window_snapshot(session),
        }

    async def debug_node(self, flow_id: str, node_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            if self._debugging or (self.active and self.active.status in {"running", "paused"}):
                raise RuntimeError("已有流程正在执行")
            flow = self.get_flow(flow_id)
            if not flow:
                raise RuntimeError("流程不存在")
            nodes = {node["id"]: copy.deepcopy(node) for node in flow["nodes"]}
            if node_id not in nodes:
                raise RuntimeError("节点不存在")
            if params is not None:
                nodes[node_id]["data"]["params"] = {**nodes[node_id]["data"].get("params", {}), **params}
            self.validate_flow(list(nodes.values()))
            session = ExecutionSession(f"debug-{uuid.uuid4().hex[:12]}", flow_id, "debug", nodes)
            self._debugging = True
            self.active = session
        started = time.perf_counter()
        try:
            current = self._first_real_node(session)
            while current:
                session.current_node_id = current
                result = await self._execute_node(session, nodes[current], debug=True)
                session.executed.append(current)
                if current == node_id:
                    window = next(reversed(session.windows.values()), {})
                    return {"status": "success", "durationMs": int((time.perf_counter()-started)*1000),
                            "outputs": result.get("outputs", {}), "imageUrl": window.get("imageUrl"),
                            "annotations": window.get("annotations", []), "windows": session.windows,
                            "executed": session.executed, "nodeOutputs": session.node_outputs}
                if session.stop_requested:
                    raise RuntimeError("调试已停止")
                current = result.get("nextId")
            raise RuntimeError("当前分支未到达所选节点")
        finally:
            session.status = "finished"
            self._last_session = session
            if self.active is session: self.active = None
            self._debugging = False

    async def _drive_session(self, session: ExecutionSession, max_steps: int | None = None) -> None:
        limit = 1 if session.mode == "step" else max_steps
        steps = 0
        try:
            if not session.initialized:
                session.initialized = True
                session.next_node_id = self._first_real_node(session)
                await self._emit(session, {"type": "run.started", "mode": session.mode})
            while session.next_node_id and not session.stop_requested:
                if limit is not None and steps >= limit:
                    session.status = "paused"
                    await self._emit(session, {"type": "run.paused"})
                    return
                node_id = session.next_node_id
                if node_id in session.executed:
                    raise RuntimeError("流程存在环路")
                session.current_node_id = node_id
                result = await self._execute_node(session, session.nodes[node_id])
                session.executed.append(node_id)
                session.next_node_id = result.get("nextId")
                steps += 1
            duration = int((time.perf_counter() - session.started_perf) * 1000)
            if session.stop_requested:
                session.status = "stopped"
                await self._emit(session, {"type": "run.stopped", "durationMs": duration})
            else:
                session.status = "finished"
                self._write_run_record(session, "success", duration)
                await self._emit(session, {"type": "run.finished", "result": "success", "durationMs": duration})
        except Exception as exc:
            duration = int((time.perf_counter() - session.started_perf) * 1000)
            session.status = "stopped" if session.stop_requested else "failed"
            if session.stop_requested:
                await self._emit(session, {"type": "run.stopped", "durationMs": duration})
            else:
                self._write_run_record(session, "failed", duration, str(exc))
                await self._emit(session, {"type": "run.finished", "result": "failed", "durationMs": duration,
                                          "error": str(exc), "nodeId": session.current_node_id})
        finally:
            session.current_node_id = None
            if session.status not in {"running", "paused"}:
                self._last_session = session
                if self.active is session: self.active = None

    def _first_real_node(self, session: ExecutionSession) -> str | None:
        for node in session.nodes.values():
            data = node.get("data") or {}
            if data.get("nodeType") == "start":
                return _next_builtin(node)
        return None

    async def _execute_node(self, session: ExecutionSession, node: dict[str, Any], debug: bool = False) -> dict[str, Any]:
        node_id = str(node["id"])
        data = node.get("data") or {}
        node_type = str(data.get("nodeType") or "start")
        label = str(data.get("label") or node_type)
        params = data.get("params") or {}
        started = time.perf_counter()
        if not debug:
            await self._emit(session, {"type": "node.started", "nodeId": node_id, "nodeType": node_type, "nodeLabel": label, "seq": len(session.executed) + 1})
        try:
            handler = getattr(self, f"_node_{node_type}", None)
            if handler is None:
                raise RuntimeError(f"节点类型未实现：{node_type}")
            result = await handler(session, node_id, params)
            session.node_outputs[node_id] = result.get("outputs", {})
            for key, value in result.get("outputs", {}).items():
                session.context[f"{node_id}.{key}"] = value
            duration = int((time.perf_counter() - started) * 1000)
            if not debug:
                await self._emit(
                    session,
                    {
                        "type": "log",
                        "nodeId": node_id,
                        "nodeType": node_type,
                        "time": now_iso(),
                        "level": "success",
                        "nodeLabel": label,
                        "content": result.get("message") or f"{label} 执行完成，耗时 {duration}ms",
                    },
                )
                await self._emit(
                    session,
                    {
                        "type": "node.finished",
                        "nodeId": node_id,
                        "nodeType": node_type,
                        "nodeLabel": label,
                        "status": "success",
                        "durationMs": duration,
                        "outputs": result.get("outputs", {}),
                    },
                )
            return result
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            if not debug:
                await self._emit(
                    session,
                    {
                        "type": "log",
                        "nodeId": node_id,
                        "nodeType": node_type,
                        "time": now_iso(),
                        "level": "error",
                        "nodeLabel": label,
                        "content": str(exc),
                    },
                )
                await self._emit(
                    session,
                    {
                        "type": "node.finished",
                        "nodeId": node_id,
                        "nodeType": node_type,
                        "nodeLabel": label,
                        "status": "failed",
                        "durationMs": duration,
                    },
                )
            raise

    async def _node_start(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"nextId": None, "outputs": {}, "message": "流程开始"}

    async def _node_capture(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        source = str(params.get("source") or "file")
        if source == "file":
            path = Path(str((params.get("file") or {}).get("path") or ""))
            if not path.is_absolute():
                path = ROOT_DIR / path
            if not path.exists():
                raise RuntimeError(f"指定图像不存在：{path}")
            image = imaging.imread(path)
            if image is None:
                raise RuntimeError(f"图像读取失败：{path}")
        elif source == "dir":
            dir_conf = params.get("dir") or {}
            files = [str((item or {}).get("path") or "") for item in dir_conf.get("files") or [] if (item or {}).get("selected", True)]
            if not files and not dir_conf.get("files"):
                base = Path(str(dir_conf.get("path") or ""))
                if not base.is_absolute():
                    base = ROOT_DIR / base
                files = [str(p) for p in sorted(base.glob("*")) if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}]
            if not files:
                raise RuntimeError("文件目录没有可用图像")
            key = (session.flow_id, node_id)
            index = self._directory_indices.get(key, 0)
            if index >= len(files):
                if not dir_conf.get("loop", True):
                    raise RuntimeError("目录图像已读完，请启用循环或重新选择目录")
                index = 0
            self._directory_indices[key] = index + 1
            path = Path(files[index])
            if not path.is_absolute():
                path = ROOT_DIR / path
            image = imaging.imread(path)
            if image is None:
                raise RuntimeError(f"图像读取失败：{path}")
        elif source == "camera":
            camera_id = str((params.get("camera") or {}).get("cameraId") or "")
            camera = self._get_camera(camera_id)
            camera_params = _resolve_tree(session, params.get("camera") or {})
            timeout = camera_params.get("timeout") or {}
            timeout_us = timeout.get("valueUs", 1000000) if timeout.get("enabled", True) else 1000000
            timeout_us = _number(timeout_us, "相机超时", minimum=1)
            image = await asyncio.to_thread(self.camera_service.grab, camera, max(1, math.ceil(timeout_us / 1000)), camera_params)
        else:
            raise RuntimeError(f"不支持的图像源：{source}")

        path = _save_capture_image(session.run_id, node_id, image)
        session.objects[f"{node_id}.Image"] = image
        session.context[f"{node_id}.Image"] = {
            "type": "Image",
            "path": str(path),
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
        }
        await self._emit(session, {"type": "window.frame", "windowId": 1, "nodeId": node_id, "imageUrl": f"/api/v1/frames/{path.name}", "width": int(image.shape[1]), "height": int(image.shape[0])})
        return {
            "nextId": _next_builtin(session.nodes.get(node_id) or {}),
            "outputs": {"Image": {"path": str(path), "width": int(image.shape[1]), "height": int(image.shape[0])}},
            "message": f"采集成功 {image.shape[1]}×{image.shape[0]}",
        }

    async def _node_quickCapture(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._node_capture(session, node_id, params)

    async def _node_preprocess(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("预处理节点尚未实现，请移除该历史节点")

    async def _node_calibration(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("图像标定节点尚未实现，请移除该历史节点")

    async def _node_affine(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("仿射图像节点尚未实现，请移除该历史节点")

    async def _node_plc(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        values = _resolve_tree(session, params)
        outputs = await asyncio.to_thread(_write_modbus, values)
        return {"nextId": _next_builtin(session.nodes.get(node_id) or {}), "outputs": outputs,
                "message": f"PLC 写入成功：{outputs['function']} 地址 {outputs['address']}"}

    async def _node_model(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        model_id = str(params.get("modelId") or "")
        if not model_id:
            raise RuntimeError("模型推理节点未选择模型")
        image = self._resolve_image(session, params.get("image") or {"type": "ref", "ref": ""})
        model_params = {key: _resolve_tree(session, value) for key, value in params.items() if key not in {"lable", "label", "modelId", "image"}}
        outputs, duration_ms = await asyncio.to_thread(self.model_service.run, model_id, image, model_params)
        for key, value in outputs.items():
            session.context[f"{node_id}.{key}"] = value
        return {
            "nextId": _next_builtin(session.nodes.get(node_id) or {}),
            "outputs": outputs,
            "message": f"模型推理完成，耗时 {duration_ms}ms，输出变量 {len(outputs)} 个",
        }

    async def _node_branch(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        conditions = params.get("conditions") or []
        current = session.nodes.get(node_id) or {}
        structure = (current.get("data") or {}).get("branch") or {}
        if structure.get("role") in {"else", "end"}:
            message = "进入否则分支" if structure["role"] == "else" else "条件分支结束，继续后续节点"
            return {"nextId": _next_builtin(current), "outputs": {}, "message": message}
        if not conditions:
            raise RuntimeError("条件分支至少配置一个条件")
        for condition in conditions:
            if not isinstance(condition, dict):
                raise RuntimeError("条件项目不能为空")
            if _boolean(session, condition.get("value"), "分支条件"):
                condition_id = str(condition.get("id") or "")
                target = _dynamic_target(current, condition_id)
                return {"nextId": target, "outputs": {"matched": condition_id}, "message": f"命中条件 {condition_id}"}
        return {"nextId": _next_builtin(current), "outputs": {"matched": None}, "message": "未命中条件，继续判断后续分支"}

    async def _node_display(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        image_ref = params.get("image")
        image = self._resolve_image(session, image_ref)
        annotations: list[dict[str, Any]] = []
        for shape in params.get("shapes") or []:
            if not isinstance(shape, dict) or "content" not in shape:
                raise RuntimeError("显示图形项目不能为空")
            if not _boolean(session, shape.get("visible", True), "图形显示状态"):
                continue
            regions = _resolve_value_object(session, shape["content"])
            if not isinstance(regions, list):
                raise RuntimeError("图形内容必须为区域列表")
            status = _boolean(session, shape.get("status", True), "图形状态")
            style = shape.get("style") or {}
            show_label = _boolean(session, shape.get("showLabel", style.get("showLabel", False)), "显示标签")
            color = str(style.get("okColor" if status else "ngColor") or ("#22C55E" if status else "#EF4444"))
            line_width = _number(_resolve_value_object(session, style.get("lineWidth", 1)), "线宽", minimum=1)
            for region in regions:
                if not isinstance(region, dict):
                    raise RuntimeError("区域必须为对象")
                bbox = region.get("bbox", [region.get(k) for k in ("x", "y", "width", "height")])
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    raise RuntimeError("区域 bbox 必须包含 x、y、width、height")
                x, y, width, height = [_number(v, "区域坐标") for v in bbox]
                if width < 0 or height < 0: raise RuntimeError("区域宽高不能为负数")
                annotations.append({"kind": "rect", "x": x, "y": y, "width": width, "height": height,
                                    "bbox": [x, y, x+width, y+height], "color": color, "lineWidth": line_width,
                                    "label": region.get("labelName") if show_label else None})
        for item in params.get("texts") or []:
            if not isinstance(item, dict) or "content" not in item:
                raise RuntimeError("显示文字项目不能为空")
            if not _boolean(session, item.get("visible", True), "文字显示状态"):
                continue
            content = _resolve_value_object(session, item["content"])
            if content is None or isinstance(content, (list, dict)) or content == "":
                raise RuntimeError("文字内容不能为空且必须为标量")
            status = _boolean(session, item.get("status", True), "文字状态")
            annotations.append({"kind": "text", "content": str(content),
                "x": _number(_resolve_value_object(session, item.get("x", 0)), "文字 X"),
                "y": _number(_resolve_value_object(session, item.get("y", 0)), "文字 Y"),
                "color": "#27C346" if status else "#F76965",
                "fontSize": _number(_resolve_value_object(session, item.get("fontSize", 32)), "字号", minimum=1)})
        window_id = int(_number(params.get("windowId", 1), "窗口 ID", minimum=1))
        payload = session.context.get(_resolve_ref(image_ref)) or {}
        if payload.get("path"):
            image_url = f"/api/v1/frames/{Path(payload['path']).name}"
            if session.windows.get(str(window_id), {}).get("imageUrl") != image_url:
                await self._emit(session, {"type": "window.frame", "windowId": window_id, "nodeId": node_id,
                    "imageUrl": image_url, "width": int(image.shape[1]), "height": int(image.shape[0])})
        await self._emit(session, {"type": "window.overlay", "windowId": window_id, "nodeId": node_id, "annotations": annotations})
        return {"nextId": _next_builtin(session.nodes.get(node_id) or {}), "outputs": {}, "message": f"结果已输出到窗口 {window_id}"}

    async def _node_storage(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        target = RESULTS_DIR / f"{session.run_id}_{node_id}.json"
        payload = {
            "runId": session.run_id,
            "flowId": session.flow_id,
            "nodeId": node_id,
            "context": jsonable(session.context),
            "time": now_iso(),
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"nextId": _next_builtin(session.nodes.get(node_id) or {}), "outputs": {"path": str(target)}, "message": f"结果已保存：{target.name}"}

    async def _node_alarm(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        duration_ms = int(params.get("durationMs") or 800)
        await self._emit(session, {"type": "alarm", "nodeId": node_id, "mode": params.get("mode") or "screen", "durationMs": duration_ms})
        if duration_ms > 0:
            await asyncio.sleep(min(duration_ms, 3000) / 1000)
        return {"nextId": _next_builtin(session.nodes.get(node_id) or {}), "outputs": {}, "message": "警示信号已输出"}

    async def _node_end(self, session: ExecutionSession, node_id: str, params: dict[str, Any]) -> dict[str, Any]:
        current = session.nodes.get(node_id) or {}
        if ((current.get("data") or {}).get("branch") or {}).get("role") == "end":
            return {
                "nextId": _next_builtin(current),
                "outputs": {},
                "message": "条件分支结束，继续后续节点",
            }
        return {"nextId": _next_builtin(current), "outputs": {}, "message": "结束节点执行完成"}

    def _resolve_image(self, session: ExecutionSession, value_obj: Any) -> np.ndarray:
        ref = _resolve_ref(value_obj)
        if not ref:
            raise RuntimeError("图像引用为空")
        image = session.objects.get(ref)
        if isinstance(image, np.ndarray):
            return image
        payload = session.context.get(ref)
        if isinstance(payload, dict) and payload.get("path"):
            image = imaging.imread(payload["path"])
            if image is not None:
                session.objects[ref] = image
                return image
        raise RuntimeError(f"图像引用不存在：{ref}")

    def _get_camera(self, camera_id: str) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        if not row:
            raise RuntimeError("相机不存在")
        item = dict(row)
        item["auto_connect"] = bool(item["auto_connect"])
        return item

    def _write_run_record(self, session: ExecutionSession, result: str, duration_ms: int, error: str = "") -> None:
        record = {
            "runId": session.run_id,
            "flowId": session.flow_id,
            "mode": session.mode,
            "status": session.status,
            "result": result,
            "executed": session.executed,
            "context": jsonable(session.context),
            "windows": session.windows,
            "nodeOutputs": session.node_outputs,
            **_window_snapshot(session),
            "error": error,
        }
        with connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs(id, flow_id, status, result, started_at, finished_at, duration_ms, record_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.run_id,
                    session.flow_id,
                    session.status,
                    result,
                    session.started_at,
                    now_iso(),
                    duration_ms,
                    json.dumps(record, ensure_ascii=False),
                ),
            )

    async def _emit(self, session: ExecutionSession, payload: dict[str, Any]) -> None:
        event = {"runId": session.run_id, "flowId": session.flow_id, **payload}
        if payload.get("type") in {"window.frame", "window.overlay"}:
            key = str(payload.get("windowId", 1))
            window = session.windows.setdefault(key, {})
            window.update({k: v for k, v in payload.items() if k != "type"})
            if payload["type"] == "window.frame": window["annotations"] = []
        if session.mode != "debug":
            await event_hub.broadcast(AUTH_TOKEN or "dev", event)

    def _require_session(self, run_id: str) -> ExecutionSession:
        session = self.active
        if not session or session.run_id != run_id or session.status not in {"running", "paused"}:
            raise RuntimeError("运行会话不存在或已失效")
        return session

    def _flow_row(self, row: Any, include_nodes: bool) -> dict[str, Any]:
        item = dict(row)
        if include_nodes:
            item["nodes"] = json.loads(item.pop("nodes_json"))
        else:
            item.pop("nodes_json", None)
        return item


def variables_for_node(node: dict[str, Any], manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = node.get("data") or {}
    node_type = data.get("nodeType")
    if node_type == "capture":
        return [{"variable": "Image", "displayName": "图像", "type": "Image", "value": None, "remarks": None}]
    if node_type == "model" and manifest:
        variables: list[dict[str, Any]] = []
        for task in manifest.get("tasks", []):
            for label in task.get("labels", []):
                variables.extend(
                    [
                        {
                            "variable": f"{task['key']}:{label['label']}:detected",
                            "displayName": f"{task['name']}：{label['name']}-检出状态",
                            "type": "Bool",
                            "value": None,
                        },
                        {
                            "variable": f"{task['key']}:{label['label']}:count",
                            "displayName": f"{task['name']}：{label['name']}-个数",
                            "type": "Int",
                            "value": None,
                        },
                        {
                            "variable": f"{task['key']}:{label['label']}:regions",
                            "displayName": f"{task['name']}：{label['name']}-区域",
                            "type": "List<Region>",
                            "value": None,
                        },
                    ]
                )
        return variables
    return []


def _next_builtin(node: dict[str, Any]) -> str | None:
    ports = (node.get("data") or {}).get("ports") or {}
    target = ports.get("builtIn")
    return target.get("targetId") if isinstance(target, dict) else target


def _dynamic_target(node: dict[str, Any], condition_id: str) -> str | None:
    ports = (node.get("data") or {}).get("ports") or {}
    for item in ports.get("dynamic", []):
        if str(item.get("id")) == condition_id:
            return item.get("targetId")
    raise RuntimeError(f"条件缺少对应动态出口：{condition_id}")


def _resolve_ref(value_obj: Any) -> str:
    if not isinstance(value_obj, dict):
        return ""
    if value_obj.get("type") == "ref":
        return str(value_obj.get("ref") or "")
    return ""


def _resolve_value_object(session: ExecutionSession, value_obj: Any) -> Any:
    if not isinstance(value_obj, dict):
        return value_obj
    if value_obj.get("type") == "ref":
        reference = value_obj.get("ref")
        if not isinstance(reference, str) or not reference or reference not in session.context:
            raise RuntimeError(f"输入引用不存在或尚未执行：{reference}")
        return session.context[reference]
    if value_obj.get("type") == "content" and "ref" in value_obj:
        return value_obj["ref"]
    raise RuntimeError("输入值对象必须为 content 或 ref")


def _save_capture_image(run_id: str, node_id: str, image: np.ndarray) -> Path:
    from ..config import FRAMES_DIR

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    target = FRAMES_DIR / f"{run_id}_{node_id}.jpg"
    if not imaging.imwrite(target, image):
        raise RuntimeError("采集图像保存失败")
    return target


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return f"ndarray:{value.shape}"
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return value


def _walk_values(value: Any) -> list[Any]:
    result: list[Any] = []
    if isinstance(value, dict):
        result.append(value)
        for item in value.values():
            result.extend(_walk_values(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_walk_values(item))
    return result


def _resolve_tree(session: ExecutionSession, value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("type") in {"ref", "content"}: return _resolve_value_object(session, value)
        return {key: _resolve_tree(session, item) for key, item in value.items()}
    if isinstance(value, list): return [_resolve_tree(session, item) for item in value]
    return value


def _boolean(session: ExecutionSession, value: Any, name: str) -> bool:
    result = _resolve_value_object(session, value)
    if type(result) is not bool: raise RuntimeError(f"{name} 必须为 Bool")
    return result


def _number(value: Any, name: str, minimum: float | None = None) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise RuntimeError(f"{name} 必须为有限数值")
    if minimum is not None and value < minimum: raise RuntimeError(f"{name} 不能小于 {minimum}")
    return value


def _write_modbus(params: dict[str, Any]) -> dict[str, Any]:
    host = params.get("host")
    if not isinstance(host, str) or not host.strip(): raise RuntimeError("PLC 主机地址不能为空")
    def integer(name, default, low, high):
        value = params.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise RuntimeError(f"PLC {name} 必须为 {low}~{high} 的整数")
        return value
    port = integer("port", 502, 1, 65535)
    unit = integer("unitId", 1, 0, 255)
    address = integer("address", 0, 0, 65535)
    timeout = integer("timeoutMs", 3000, 1, 60000) / 1000
    function = params.get("operation", params.get("function", "coil"))
    value = params.get("value")
    if function == "coil":
        if type(value) not in {bool, int} or value not in (False, True, 0, 1):
            raise RuntimeError("PLC 线圈值必须为 Bool 或 0/1")
        code, encoded = 5, 0xFF00 if value else 0
    elif function == "register":
        value = integer("value", None, 0, 65535)
        code, encoded = 6, value
    else: raise RuntimeError("PLC function 必须为 coil 或 register")
    transaction = uuid.uuid4().int & 0xFFFF
    pdu = struct.pack(">BHH", code, address, encoded)
    request = struct.pack(">HHHB", transaction, 0, 6, unit) + pdu
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            def receive(size):
                result = b""
                while len(result) < size:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0: raise TimeoutError("PLC 响应超时")
                    sock.settimeout(remaining)
                    chunk = sock.recv(size-len(result))
                    if not chunk: raise RuntimeError("PLC 响应不完整")
                    result += chunk
                return result
            sock.sendall(request)
            tid, protocol, length, response_unit = struct.unpack(">HHHB", receive(7))
            if tid != transaction or protocol != 0 or response_unit != unit or not 2 <= length <= 254:
                raise RuntimeError("PLC MBAP 响应不匹配")
            response = receive(length-1)
            if response[0] == code | 0x80:
                raise RuntimeError(f"PLC 返回异常码 {response[1] if len(response)>1 else '缺失'}")
            if response != pdu: raise RuntimeError("PLC 写入确认与请求不匹配")
    except OSError as exc:
        raise RuntimeError(f"PLC 通信失败：{exc}") from exc
    return {"function": function, "address": address, "value": value, "unitId": unit}


def _window_snapshot(session: ExecutionSession) -> dict[str, Any]:
    window = session.windows.get("1") or next(reversed(session.windows.values()), {})
    return {"imageUrl": window.get("imageUrl"), "annotations": copy.deepcopy(window.get("annotations", [])),
            "width": window.get("width"), "height": window.get("height")}
