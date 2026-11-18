"""Switchboard's own app window (like the Claude or ChatGPT desktop apps), on Windows and macOS.

It shows the same screens as the browser (setup wizard, then the dashboard) in a native window with the
system's web engine (WebKit on macOS, Edge WebView2 on Windows). One window at a time: opening the app
again brings it to the front. Without pywebview (Linux, source installs) it falls back to the browser.
"""
from __future__ import annotations

import contextlib
import importlib.util
import logging
import socket
import threading
import webbrowser

from .paths import os_family

log = logging.getLogger("switchboard.window")
FOCUS_PORT = 8769  # 127.0.0.1 only: "a window is already open, show it"
SIZE = (1240, 820)


def window_available() -> bool:
    return os_family() in ("windows", "macos") and importlib.util.find_spec("webview") is not None


def _focus_existing() -> bool:
    with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", FOCUS_PORT), timeout=1) as s:
        s.sendall(b"show\n")
        return True
    return False


def _listen_for_focus(window) -> None:
    try:
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", FOCUS_PORT))
        srv.listen(4)
    except OSError:
        return

    def loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                if conn.recv(16).startswith(b"show"):
                    with contextlib.suppress(Exception):
                        window.restore()
                        window.show()
                        window.on_top = True
                        window.on_top = False

    threading.Thread(target=loop, daemon=True).start()


def open_window(url: str, title: str = "Switchboard") -> None:
    """Show `url` in the app window and block until it is closed (or bring an open one to the front)."""
    if _focus_existing():
        return
    if not window_available():
        webbrowser.open(url)
        return
    import webview

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # links to other sites open in the browser
    webview.settings["ALLOW_DOWNLOADS"] = True  # task files, generated code
    win = webview.create_window(title, url, width=SIZE[0], height=SIZE[1], min_size=(420, 560),
                                background_color="#f4f6f9", text_select=True)
    win.events.shown += lambda: _listen_for_focus(win)
    try:
        webview.start(private_mode=False)  # keep the dashboard's saved settings between launches
    except Exception:
        log.exception("app window failed; opening the browser instead")
        webbrowser.open(url)


def open_app_detached() -> None:
    """From the tray: open (or focus) the window in its own process, so the tray stays responsive."""
    import subprocess

    from .paths import self_command

    if _focus_existing():
        return
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os_family() == "windows":
        kw["creationflags"] = 0x00000008 | 0x00000200
    else:
        kw["start_new_session"] = True
    subprocess.Popen(self_command("launch", gui=True), **kw)
