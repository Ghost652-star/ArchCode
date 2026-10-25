"""桌面模式:pywebview 原生窗口渲染 Web UI。

双击 exe 的默认形态——弹出一个独立桌面窗口(无地址栏、无终端框),
内部复用 FastAPI + web/dist 前端,与 --web 模式共用同一套服务端装配。
WebView2 不可用时降级为系统浏览器打开,服务不中断。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from archcode.paths import application_data_dir

log = logging.getLogger(__name__)


def _wait_ready(url: str, timeout_s: float = 15.0) -> bool:
    """轮询本地服务直到可访问(或超时),避免窗口先于服务打开。"""
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.1)
    return False


def _alert(title: str, message: str) -> None:
    """无控制台环境下的兜底提示(打包 exe 不带终端)。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


def run_desktop(work_dir: Path | None, config, port: int = 8765) -> None:
    """启动本地服务 + 打开原生窗口。

    work_dir=None = 空启动(工作区由前端按需注册,与 --web 空启动一致)。
    """
    try:
        import uvicorn
        import webview

        from archcode.webui.server import app, create_web_server
    except Exception as e:  # pragma: no cover - 打包缺件时的兜底
        _alert("ArchCode 启动失败", f"依赖缺失:{e}")
        raise

    create_web_server(work_dir=work_dir, config=config)
    url = f"http://127.0.0.1:{port}"
    log.info("desktop mode starting: port=%d work_dir=%s", port, work_dir)

    server = threading.Thread(
        target=lambda: uvicorn.run(
            app, host="127.0.0.1", port=port, log_level="warning"
        ),
        daemon=True,
    )
    server.start()
    _wait_ready(url)

    storage_path = application_data_dir() / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)

    try:
        webview.create_window(
            "ArchCode",
            url,
            width=1360,
            height=860,
            min_size=(960, 640),
            text_select=True,
            confirm_close=True,
        )
        webview.start(
            private_mode=False,
            storage_path=str(storage_path),
        )
    except Exception as e:
        # WebView2 缺失等环境问题:降级为浏览器打开,服务保持存活
        log.exception("pywebview unavailable (%s); fallback to browser", e)
        import webbrowser

        webbrowser.open(url)
        _alert(
            "ArchCode",
            "桌面窗口组件不可用,已在浏览器中打开。\n"
            f"地址:{url}\n关闭本提示前请勿关闭程序。",
        )
        while server.is_alive():
            time.sleep(1)
