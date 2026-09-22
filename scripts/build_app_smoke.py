"""验证独立后端：隔离目录、就绪握手、认证、持久化和宿主退出清理。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from scripts.build_app import bundle_targets


def start(executable: Path, data: Path, cwd: Path):
    env = {key: value for key, value in os.environ.items() if key not in {
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "DIGIVISION_TOKEN", "DIGIVISION_PORT"
    }}
    env.update(DIGIVISION_DATA_DIR=str(data), PATH="/usr/bin:/bin")
    process = subprocess.Popen([str(executable)], cwd=cwd, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        hs = json.loads(lines.get(timeout=60))
        assert hs["host"] == "127.0.0.1" and 0 < hs["port"] < 65536
        assert hs["dataDir"] == str(data) and len(hs["token"]) >= 32
        return process, hs
    except BaseException:
        process.kill()
        _, stderr = process.communicate()
        print(stderr)
        raise


def request(hs, path, body=None, token=True, method=None):
    headers = {"Authorization": "Bearer " + hs["token"]} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f'http://127.0.0.1:{hs["port"]}{path}',
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} 返回 HTTP {error.code}: {detail}") from error


def stop(process, hs):
    process.stdin.close()
    code = process.wait(timeout=12)
    logs = process.stderr.read()
    assert code == 0, logs
    assert hs["token"] not in logs, "运行时 token 被写入日志"
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", hs["port"])) != 0, "后端退出后端口仍可连接"


def check_websocket(hs):
    with socket.create_connection((hs["host"], hs["port"]), timeout=5) as ws:
        ws.sendall((f'GET /ws?token={hs["token"]} HTTP/1.1\r\n'
                    f'Host: 127.0.0.1:{hs["port"]}\r\n'
                    'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                    'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n'
                    'Sec-WebSocket-Version: 13\r\n\r\n').encode())
        assert b"101 Switching Protocols" in ws.recv(4096), "WebSocket 认证握手失败"


def upload(hs, path, source):
    boundary = "digivision-frozen-smoke-boundary"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{source.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + source.read_bytes() + f'\r\n--{boundary}--\r\n'.encode())
    req = urllib.request.Request(f'http://127.0.0.1:{hs["port"]}{path}', data=body,
        headers={"Authorization": "Bearer " + hs["token"], "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    assert result["code"] == 0, result
    return result["data"]


def check_model(hs, package, image):
    model = upload(hs, "/api/v1/models/import", package)
    uploaded = upload(hs, "/api/v1/files/upload", image)
    base = "/api/v1/models/" + model["id"]
    for _ in range(2):
        assert request(hs, base + "/start", {})["data"]["serviceStatus"] == "running"
        assert request(hs, base + "/stop", {})["data"]["serviceStatus"] == "stopped"
    assert request(hs, base + "/start", {})["data"]["serviceStatus"] == "running"
    flow = request(hs, "/api/v1/flows", {"name": "独立包真实推理检查"})["data"]
    start = flow["nodes"][0]
    start["data"]["ports"]["builtIn"] = "capture"
    def node(identifier, kind, params, target=None):
        return {"id": identifier, "type": "workflow", "position": {"x": 0, "y": 0},
                "data": {"label": identifier, "nodeType": kind, "params": params,
                         "ports": {"builtIn": target, "dynamic": []}}}
    params = {
        declaration["name"]: {"type": "content", "ref": declaration["default"]}
        for declaration in model["manifest"].get("params", [])
        if "default" in declaration
    }
    nodes = [start, node("capture", "capture", {"source": "file", "file": {"path": uploaded["path"]}}, "infer"),
        node("infer", "model", {"modelId": model["id"], "image": {"type": "ref", "ref": "capture.Image"}, **params})]
    request(hs, "/api/v1/flows/" + flow["id"], {**flow, "nodes": nodes}, method="PUT")
    result = request(hs, f'/api/v1/flows/{flow["id"]}/nodes/infer/debug', {})["data"]
    output = result["outputs"]
    count = 0
    for task in model["manifest"]["tasks"]:
        for label in task["labels"]:
            prefix = f"{task['key']}:{label['label']}"
            assert isinstance(output[f"{prefix}:detected"], bool), output
            label_count = output[f"{prefix}:count"]
            assert isinstance(label_count, int)
            assert len(output[f"{prefix}:regions"]) == label_count, output
            count += label_count
    run = request(hs, f'/api/v1/flows/{flow["id"]}/runs', {"mode": "run"})["data"]
    for _ in range(200):
        active = request(hs, "/api/v1/runs/active")["data"]
        if not active or active["runId"] != run["runId"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("推理流程超时")
    record = request(hs, "/api/v1/runs/" + run["runId"])["data"]
    assert record["result"] != "failed", record
    context = record["record"]["context"]
    for task in model["manifest"]["tasks"]:
        for label in task["labels"]:
            key = f"infer.{task['key']}:{label['label']}:count"
            assert key in context, record
    assert request(hs, base + "/stop", {})["data"]["serviceStatus"] == "stopped"
    print(json.dumps({"动态模型导入": "通过", "OpenCV_NumPy图片推理": "通过", "检出区域": count,
                      "模型启停": "通过", "正式流程记录": "通过"}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--sample-package", type=Path)
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    if bool(args.sample_package) != bool(args.image):
        parser.error("--sample-package 和 --image 必须一起指定")
    executable = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix="digivision-smoke-") as directory:
        cwd = Path(directory).resolve()
        data = cwd / "用户数据"
        process, first = start(executable, data, cwd)
        try:
            assert request(first, "/health")["data"]["status"] == "ok"
            runtime = request(first, "/api/v1/runtime")["data"]
            assert runtime["dataDir"] == first["dataDir"]
            for resource in ["cameras", "models", "flows"]:
                assert request(first, "/api/v1/" + resource)["data"] == [], resource + " 被注入了样例"
            try:
                request(first, "/api/v1/flows", token=False)
                raise AssertionError("缺失 token 的 API 请求未被拒绝")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            flow = request(first, "/api/v1/flows", {"name": "持久化检查"})["data"]
            with sqlite3.connect(data / "digivision.db") as db:
                assert db.execute("SELECT name FROM flows WHERE id=?", (flow["id"],)).fetchone()[0] == "持久化检查"
            check_websocket(first)
            if args.sample_package:
                check_model(first, args.sample_package.resolve(), args.image.resolve())
        finally:
            stop(process, first)
        process, second = start(executable, data, cwd)
        try:
            assert second["token"] != first["token"]
            assert request(second, "/api/v1/flows/" + flow["id"])["data"]["name"] == "持久化检查"
        finally:
            stop(process, second)
        print(json.dumps({"result": "通过", "executable": str(executable),
                          "checks": ["无源码或用户venv", "动态端口就绪握手", "health", "认证", "WebSocket及日志脱敏", "无样例数据", "SQLite重启持久化", "stdin EOF正常退出", "退出后端口释放"]}, ensure_ascii=False))


class BuildConfigurationTests(unittest.TestCase):
    def test_macos_bundles_app_and_dmg(self):
        self.assertEqual(bundle_targets("darwin"), "app,dmg")

    def test_windows_bundles_nsis(self):
        self.assertEqual(bundle_targets("win32"), "nsis")


if __name__ == "__main__":
    main()
