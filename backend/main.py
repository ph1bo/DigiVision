from __future__ import annotations

import asyncio
import json
import secrets
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import AUTH_TOKEN, DATA_DIR, FRAMES_DIR, RESULTS_DIR, ROOT_DIR, SAMPLES_DIR, UPLOADS_DIR, ensure_data_dirs, runtime_handshake
from .db import connect, init_db, new_id, now_iso
from .engine.executor import WorkflowManager, variables_for_node
from .events import event_hub
from .services.camera import CameraService
from .services.models import ModelPackageError, ModelService


app = FastAPI(title="DigiVision Demo", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def authenticate(request: Request, call_next):
    if AUTH_TOKEN and request.method != "OPTIONS":
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ") or request.query_params.get("token", "")
        if not secrets.compare_digest(supplied, AUTH_TOKEN):
            return JSONResponse(status_code=401, content={"code": 401, "message": "本地服务认证失败", "data": None})
    return await call_next(request)


camera_service = CameraService()
model_service = ModelService()
workflow_manager = WorkflowManager(camera_service, model_service)


@app.on_event("startup")
async def startup() -> None:
    ensure_data_dirs()
    init_db()
    _restore_runtime_state()


@app.on_event("shutdown")
async def shutdown() -> None:
    try:
        await workflow_manager.stop(workflow_manager.active.run_id) if workflow_manager.active else None
    except Exception:
        pass
    model_service.unload_all()
    camera_service.close_all()


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    return {"code": 0, "message": message, "data": data}


def fail(message: str, code: int = 1, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message, "data": None})


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
    return fail(str(exc), 500, 500)


@app.get("/health")
async def health() -> dict[str, Any]:
    return ok({"status": "ok", "dataDir": str(DATA_DIR)})


@app.get("/api/v1/runtime")
async def runtime() -> dict[str, Any]:
    return ok({"dataDir": str(DATA_DIR), "activeRun": workflow_manager.active_info()})


@app.get("/api/v1/runtime/capabilities")
async def runtime_capabilities() -> dict[str, Any]:
    return ok({
        "database": {"ready": True, "path": str(DATA_DIR / "digivision.db")},
        "inference": model_service.runtime_capabilities(),
        "camera": {"vendors": camera_service.vendors()},
    })


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if AUTH_TOKEN and not secrets.compare_digest(websocket.query_params.get("token", ""), AUTH_TOKEN):
        await websocket.close(code=1008)
        return
    await event_hub.connect(websocket, AUTH_TOKEN or "dev")
    try:
        await websocket.send_json({"type": "connected"})
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        event_hub.disconnect(websocket, AUTH_TOKEN or "dev")
    except Exception:
        event_hub.disconnect(websocket, AUTH_TOKEN or "dev")


@app.get("/api/v1/cameras/vendors")
async def camera_vendors() -> dict[str, Any]:
    return ok(camera_service.vendors())


@app.post("/api/v1/cameras/discover")
async def camera_discover(request: Request) -> dict[str, Any]:
    payload = await request.json()
    vendor = str(payload.get("vendor") or "hikvision")
    result = await asyncio.to_thread(camera_service.discover, vendor)
    with connect() as conn:
        rows = conn.execute("SELECT serial, interface FROM cameras").fetchall()
    added = {f"{row['serial']}:{row['interface']}" for row in rows}
    for device in result.get("devices", []):
        device["added"] = f"{device['serial']}:{device['interface']}" in added
    return ok(result)


@app.get("/api/v1/cameras")
async def list_cameras(connected: bool | None = None) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM cameras ORDER BY created_at ASC").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["auto_connect"] = bool(item["auto_connect"])
        item["connected"] = camera_service.is_connected(str(item["id"]))
        if connected is None or item["connected"] == connected:
            items.append(item)
    return ok(items)


@app.post("/api/v1/cameras")
async def create_camera(request: Request) -> dict[str, Any]:
    payload = await request.json()
    name = str(payload.get("name") or "Camera")
    vendor = str(payload.get("vendor") or "hikvision")
    model = str(payload.get("model") or "")
    serial = str(payload.get("serial") or "")
    interface = str(payload.get("interface") or "USB3")
    ip_address = str(payload.get("ip_address") or "")
    remark = str(payload.get("remark") or f"{serial}_{interface.upper()}")
    if not serial:
        raise HTTPException(status_code=400, detail="序列号不能为空")
    camera_id = str(payload.get("id") or f"{vendor}-{serial}")
    now = now_iso()
    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO cameras(id, name, vendor, model, serial, interface, ip_address, remark, auto_connect, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (camera_id, name, vendor, model, serial, interface, ip_address, remark, now, now),
            )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"相机已存在或写入失败：{exc}") from exc
    return ok(_get_camera(camera_id))


@app.put("/api/v1/cameras/{camera_id}")
async def update_camera(camera_id: str, request: Request) -> dict[str, Any]:
    current = _get_camera(camera_id)
    if not current:
        raise HTTPException(status_code=404, detail="相机不存在")
    payload = await request.json()
    allowed = {"name", "remark", "auto_connect"}
    values = {key: payload[key] for key in allowed if key in payload}
    if "auto_connect" in values:
        values["auto_connect"] = 1 if values["auto_connect"] else 0
    if not values:
        return ok(current)
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as conn:
        conn.execute(f"UPDATE cameras SET {assignments} WHERE id = ?", (*values.values(), camera_id))
    return ok(_get_camera(camera_id))


@app.delete("/api/v1/cameras/{camera_id}")
async def delete_camera(camera_id: str) -> dict[str, Any]:
    _ensure_flow_idle()
    camera = _get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="相机不存在")
    if camera_service.is_connected(camera_id):
        raise HTTPException(status_code=400, detail="请先断开相机")
    with connect() as conn:
        conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    return ok(None)


@app.post("/api/v1/cameras/{camera_id}/connect")
async def connect_camera(camera_id: str) -> dict[str, Any]:
    camera = _get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="相机不存在")
    try:
        await asyncio.to_thread(camera_service.connect, camera)
        with connect() as conn:
            conn.execute("UPDATE cameras SET auto_connect = 1, updated_at = ? WHERE id = ?", (now_iso(), camera_id))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(_get_camera(camera_id))


@app.post("/api/v1/cameras/{camera_id}/disconnect")
async def disconnect_camera(camera_id: str) -> dict[str, Any]:
    _ensure_flow_idle()
    camera = _get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="相机不存在")
    try:
        await asyncio.to_thread(camera_service.disconnect, camera)
        with connect() as conn:
            conn.execute("UPDATE cameras SET auto_connect = 0, updated_at = ? WHERE id = ?", (now_iso(), camera_id))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(_get_camera(camera_id))


@app.post("/api/v1/cameras/{camera_id}/grab")
async def grab_camera(camera_id: str) -> dict[str, Any]:
    camera = _get_camera(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="相机不存在")
    try:
        frame = await asyncio.to_thread(camera_service.grab, camera, 1000)
        path = camera_service.file_path_for_capture(camera_id, frame)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok({"imageUrl": f"/api/v1/frames/{path.name}", "width": int(frame.shape[1]), "height": int(frame.shape[0])})


@app.get("/api/v1/sample-image")
async def get_sample_image() -> FileResponse:
    path = SAMPLES_DIR / "demo.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="示例图像不存在，请先运行 npm run sample:model")
    return FileResponse(path)


@app.get("/api/v1/frames/{filename}")
async def get_frame(filename: str) -> FileResponse:
    path = (FRAMES_DIR / filename).resolve()
    if FRAMES_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="图像不存在")
    return FileResponse(path)


@app.get("/api/v1/models")
async def list_models(enabled: bool | None = None, serviceStatus: str | None = None) -> dict[str, Any]:
    return ok(model_service.list_models(enabled=enabled, status=serviceStatus))


@app.post("/api/v1/files/scan")
async def scan_image_files(request: Request) -> dict[str, Any]:
    payload = await request.json()
    raw_path = str(payload.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="目录路径不能为空")
    directory = Path(raw_path)
    if not directory.is_absolute():
        directory = ROOT_DIR / directory
    directory = directory.resolve()
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="目录不存在或不可访问")
    allowed = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
    files = [
        {"path": str(path), "name": path.name, "size": path.stat().st_size}
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in allowed
    ]
    return ok({"files": files[:500]})


@app.post("/api/v1/files/upload")
async def upload_image_file(file: UploadFile = File(...)) -> dict[str, Any]:
    ensure_data_dirs()
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}:
        raise HTTPException(status_code=400, detail="仅支持常见图片格式")
    target = UPLOADS_DIR / f"{new_id('image')}{suffix}"
    content = await file.read(32 * 1024 * 1024 + 1)
    if len(content) > 32 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片不能超过32MB")
    decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(status_code=400, detail="无法解码图像，请选择有效图片")
    target.write_bytes(content)
    return ok({"path": str(target), "name": file.filename or target.name,
               "width": int(decoded.shape[1]), "height": int(decoded.shape[0])})


@app.post("/api/v1/models/import")
async def import_model(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        item = await asyncio.to_thread(model_service.import_package, file.filename or "model.dvmodel", await file.read())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(item)


@app.post("/api/v1/models/{model_id}/start")
async def start_model(model_id: str) -> dict[str, Any]:
    try:
        item = await asyncio.to_thread(model_service.start, model_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(item)


@app.post("/api/v1/models/{model_id}/stop")
async def stop_model(model_id: str) -> dict[str, Any]:
    _ensure_flow_idle()
    try:
        item = await asyncio.to_thread(model_service.stop, model_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(item)


@app.delete("/api/v1/models/{model_id}")
async def delete_model(model_id: str) -> dict[str, Any]:
    _ensure_flow_idle()
    try:
        model_service.delete(model_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(None)


@app.get("/api/v1/models/{model_id}/variables")
async def model_variables(model_id: str) -> dict[str, Any]:
    item = model_service.get(model_id)
    if not item:
        raise HTTPException(status_code=404, detail="模型不存在")
    manifest = item["manifest"]
    variables = []
    for task in manifest.get("tasks", []):
        for label in task.get("labels", []):
            for suffix, display in (("detected", "检出状态"), ("count", "个数"), ("regions", "区域")):
                variables.append(
                    {
                        "variable": f"{task['key']}:{label['label']}:{suffix}",
                        "displayName": f"{task['name']}：{label['name']}-{display}",
                        "type": {"detected": "Bool", "count": "Int", "regions": "List<Region>"}[suffix],
                        "value": None,
                    }
                )
    return ok(variables)


@app.get("/api/v1/variables")
async def get_variables(nodeId: str, nodeType: str) -> dict[str, Any]:
    flow_id = ""
    node: dict[str, Any] | None = None
    for flow in workflow_manager.list_flows():
        detail = workflow_manager.get_flow(flow["id"])
        if not detail:
            continue
        for item in detail.get("nodes", []):
            if item.get("id") == nodeId:
                flow_id = flow["id"]
                node = item
                break
        if node:
            break
    if not node:
        return ok([])
    manifest = None
    data = node.get("data") or {}
    if nodeType == "model":
        model = model_service.get(str((data.get("params") or {}).get("modelId") or ""))
        manifest = model["manifest"] if model else None
    return ok(variables_for_node(node, manifest))


@app.get("/api/v1/flows")
async def list_flows() -> dict[str, Any]:
    return ok(workflow_manager.list_flows())


@app.post("/api/v1/flows")
async def create_flow(request: Request) -> dict[str, Any]:
    payload = await request.json() if await request.body() else {}
    return ok(workflow_manager.create_flow(payload.get("name")))


@app.put("/api/v1/flows/order")
async def order_flows(request: Request) -> dict[str, Any]:
    payload = await request.json()
    ids = payload.get("ids", [])
    with connect() as conn:
        existing = {row[0] for row in conn.execute("SELECT id FROM flows")}
        if len(ids) != len(set(ids)) or set(ids) != existing:
            raise HTTPException(status_code=400, detail="流程列表已变化，请刷新后重试")
        conn.executemany("UPDATE flows SET sort_order = ? WHERE id = ?", enumerate(ids))
    return ok(None)


@app.get("/api/v1/flows/{flow_id}")
async def get_flow(flow_id: str) -> dict[str, Any]:
    flow = workflow_manager.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="流程不存在")
    return ok(flow)


@app.put("/api/v1/flows/{flow_id}")
async def save_flow(flow_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    _ensure_flow_idle(flow_id)
    payload["id"] = flow_id
    try:
        flow = workflow_manager.save_flow(flow_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(flow)


@app.delete("/api/v1/flows/{flow_id}")
async def delete_flow(flow_id: str) -> dict[str, Any]:
    _ensure_flow_idle(flow_id)
    workflow_manager.delete_flow(flow_id)
    return ok(None)


@app.post("/api/v1/flows/{flow_id}/runs")
async def start_run(flow_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json() if await request.body() else {}
    try:
        run_id = await workflow_manager.start_run(flow_id, str(payload.get("mode") or "run"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok({"runId": run_id})


@app.post("/api/v1/runs/{run_id}/step")
async def step_run(run_id: str) -> dict[str, Any]:
    try:
        await workflow_manager.step(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok({"runId": run_id})


@app.post("/api/v1/runs/{run_id}/stop")
async def stop_run(run_id: str) -> dict[str, Any]:
    try:
        await workflow_manager.stop(run_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok({"runId": run_id})


@app.get("/api/v1/runs/active")
async def active_run() -> dict[str, Any]:
    return ok(workflow_manager.active_info())


@app.get("/api/v1/runs")
async def list_runs() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 50").fetchall()
    return ok([dict(row) for row in rows])


@app.get("/api/v1/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        active = workflow_manager.active_info()
        if active and active["runId"] == run_id:
            return ok(active)
        raise HTTPException(status_code=404, detail="运行记录不存在")
    item = dict(row)
    item["record"] = json.loads(item.pop("record_json", "{}"))
    return ok(item)


@app.post("/api/v1/flows/{flow_id}/nodes/{node_id}/debug")
async def debug_node(flow_id: str, node_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json() if await request.body() else {}
    try:
        result = await workflow_manager.debug_node(flow_id, node_id, payload.get("params"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(result)


@app.get("/api/v1/results/{filename}")
async def get_result(filename: str) -> FileResponse:
    path = (RESULTS_DIR / filename).resolve()
    if RESULTS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="结果不存在")
    return FileResponse(path)


def _ensure_flow_idle(flow_id: str | None = None) -> None:
    active = workflow_manager.active_info()
    if active and (flow_id is None or active["flowId"] == flow_id):
        raise HTTPException(status_code=409, detail="流程正在执行，请先停止")


def _get_camera(camera_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["auto_connect"] = bool(item["auto_connect"])
    item["connected"] = camera_service.is_connected(str(item["id"]))
    return item


def _restore_runtime_state() -> None:
    with connect() as conn:
        cameras = conn.execute("SELECT * FROM cameras WHERE auto_connect = 1").fetchall()
    for row in cameras:
        camera = dict(row)
        try:
            camera_service.connect(camera)
        except Exception:
            pass
    for model in model_service.list_models(enabled=True):
        try:
            model_service.start(str(model["id"]))
        except Exception:
            pass


DIST_DIR = ROOT_DIR / "dist"
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="web")


if __name__ == "__main__":
    hs = runtime_handshake()
    print(json.dumps({"port": hs.port, "token": hs.token}), flush=True)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=hs.port, log_level="info")
