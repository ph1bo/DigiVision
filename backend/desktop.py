"""桌面后端入口：保留动态 socket，stdout 仅输出一次就绪握手。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import sys
import threading


def main() -> None:
    handshake_output = sys.stdout
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = int(sock.getsockname()[1])
    os.environ["DIGIVISION_PORT"] = str(port)
    os.environ["DIGIVISION_TOKEN"] = secrets.token_urlsafe(32)
    os.environ["DIGIVISION_DESKTOP"] = "1"
    os.environ["DIGIVISION_MOCK_CAMERA"] = "0"

    class TokenFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.msg = record.getMessage().replace(os.environ["DIGIVISION_TOKEN"], "[redacted]")
            record.args = ()
            return True

    for handler in logging.getLogger().handlers:
        handler.addFilter(TokenFilter())

    import uvicorn
    from backend.config import DATA_DIR, runtime_handshake
    from backend.main import app

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None,
        loop="asyncio", http="h11", ws="websockets", access_log=False,
        timeout_graceful_shutdown=5,
    ))

    # 宿主正常退出关闭 stdin；宿主被强杀时操作系统也会关闭管道。
    def watch_host() -> None:
        try:
            sys.stdin.buffer.read(1)
        finally:
            server.should_exit = True
            # SDK 或推理阻塞事件循环时仍保证子进程最终退出。
            timer = threading.Timer(8, lambda: os._exit(0))
            timer.daemon = True
            timer.start()

    threading.Thread(target=watch_host, daemon=True).start()

    async def serve() -> None:
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("后端未完成启动")
            await asyncio.sleep(0.02)
        hs = runtime_handshake()
        handshake_output.write(json.dumps({
            "host": hs.host, "port": hs.port, "token": hs.token,
            "dataDir": str(DATA_DIR),
        }) + "\n")
        handshake_output.flush()
        await task

    try:
        asyncio.run(serve())
    finally:
        sock.close()


if __name__ == "__main__":
    main()
