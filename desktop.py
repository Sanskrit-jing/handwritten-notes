"""手写记录桌面版入口：pywebview 壳 + 内嵌 FastAPI 服务。

后台线程启动 uvicorn（server.APP，127.0.0.1:8300），端口就绪后打开
pywebview 窗口加载页面；关闭窗口即退出进程（uvicorn 为 daemon 线程）。
"""
import os
import socket
import sys
import threading
import time

import uvicorn
import webview

import server

HOST = "127.0.0.1"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0


def _alert(msg: str):
    """桌面模式无控制台，用系统弹窗报告启动失败。"""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "手写记录", 0x10)
    else:
        print(msg, file=sys.stderr)


def main():
    port = int(os.environ.get("HN_PORT", "8300"))
    if _port_open(port):
        _alert(f"端口 {port} 已被占用（可能已有一个实例在运行），请先退出它。")
        sys.exit(1)
    threading.Thread(
        target=lambda: uvicorn.run(server.APP, host=HOST, port=port,
                                   log_level="warning"),
        daemon=True).start()
    deadline = time.time() + 15
    while time.time() < deadline and not _port_open(port):
        time.sleep(0.1)
    if not _port_open(port):
        _alert("内置服务启动失败，请查看控制台日志。")
        sys.exit(1)
    webview.create_window("手写记录", f"http://{HOST}:{port}",
                          width=1500, height=950)
    webview.start()


if __name__ == "__main__":
    main()
