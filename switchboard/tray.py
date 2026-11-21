"""Tray / menu-bar icon for Windows and macOS (also Linux desktops).

Shows hub status and alerts at a glance, pops a desktop notification for new alerts, and opens the
dashboard or the output folder in one click. On an agent machine it also runs the agent itself, so one
background process does everything.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass, field

import httpx

from .paths import os_family

log = logging.getLogger("switchboard.tray")
COLORS = {"ok": (31, 138, 76), "warn": (214, 137, 16), "crit": (205, 52, 52), "off": (128, 134, 145)}
ACCENT = (58, 91, 217)
_MAC_REOPEN_CLASS = None


def macos_reopen_delegate(app, callback):
    """Finder reopens the existing tray process; forward that event to the app window."""
    global _MAC_REOPEN_CLASS
    if _MAC_REOPEN_CLASS is None:
        from Foundation import NSObject
        import objc

        class SwitchboardReopenDelegate(NSObject, protocols=[objc.protocolNamed('NSApplicationDelegate')]):
            def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, visible):
                try:self.on_reopen()
                except Exception:log.exception('could not reopen the app window')
                return False  # We handle the event; the tray must not create its own blank window.

        _MAC_REOPEN_CLASS = SwitchboardReopenDelegate
    delegate = _MAC_REOPEN_CLASS.alloc().init()
    delegate.on_reopen = callback
    app.setDelegate_(delegate)
    return delegate


def tray_available() -> bool:
    """True when the tray libraries are installed (checked without importing: pystray needs a display)."""
    return bool(importlib.util.find_spec("pystray") and importlib.util.find_spec("PIL"))


def make_icon(size: int = 64, status: str | None = None, maskable: bool = False):
    """Converge app mark with the existing hub-status badge."""
    from PIL import ImageDraw
    from .brand import draw_icon
    image = draw_icon(size, maskable=maskable)
    if status:
        draw = ImageDraw.Draw(image)
        radius = max(2, round(size*.14)); center = size-radius-1
        draw.ellipse((center-radius-1,center-radius-1,center+radius+1,center+radius+1),fill='white')
        draw.ellipse((center-radius,center-radius,center+radius,center+radius),fill=COLORS.get(status,COLORS['off'])+(255,))
    return image


def dashboard_url(config) -> str:
    if config.role == "hub":
        return f"http://localhost:{config.server.get('port', 8765)}"
    port = config.node.get("local_dashboard_port")
    return f"http://localhost:{port}" if port else config.hub_url


@dataclass
class TrayState:
    summary: dict | None = None
    error: str | None = None
    seen: set = field(default_factory=set)
    first: bool = True

    @property
    def status(self) -> str:
        if self.error or not self.summary:
            return "off"
        c = self.summary.get("counts") or {}
        return "crit" if c.get("critical") else "warn" if c.get("warning") else "ok"

    @property
    def tooltip(self) -> str:
        if self.error or not self.summary:
            return "Tazrivo: hub not reachable"
        c = self.summary.get("counts") or {}
        n = (c.get("critical") or 0) + (c.get("warning") or 0)
        return f"Tazrivo: {n} alert{'s' if n != 1 else ''}" if n else "Tazrivo: all good"

    @property
    def status_line(self) -> str:
        if self.error or not self.summary:
            return "Hub not reachable"
        s = self.summary
        busy = f" · {s['running_tasks']} running" if s.get("running_tasks") else ""
        return f"Hub {s['hub']} · {s['nodes_online']}/{s['nodes_total']} machines online{busy}"

    def new_alerts(self) -> list[dict]:
        """Alerts not seen before (nothing is announced on the first poll after start-up)."""
        if not self.summary:
            return []
        fresh = [a for a in self.summary.get("alerts", []) if a["key"] not in self.seen and a["severity"] != "info"]
        self.seen.update(a["key"] for a in self.summary.get("alerts", []))
        if self.first:
            self.first = False
            return []
        return fresh

    def plan_lines(self) -> list[str]:
        out = []
        for p in (self.summary or {}).get("plans", []):
            if p["status"] == "limit reached" and p.get("resets"):
                out.append(f"{p['label']}: limit, resets {time.strftime('%a %H:%M', time.localtime(p['resets']))}")
            elif p["status"] == "ready":
                out.append(f"{p['label']}: ready")
        return out or ["No plan apps set up yet"]


def desktop_notify(icon, title: str, message: str) -> None:
    fam = os_family()
    try:
        if fam == "macos":
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(message)} with title {json.dumps(title)}"],
                           timeout=10, check=False)
        elif fam == "linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=10, check=False)
        else:
            icon.notify(message, title)
    except Exception as exc:  # notifications are best-effort
        log.debug("notification failed: %s", exc)


def open_path(path: str) -> None:
    fam = os_family()
    if fam == "windows":
        os.startfile(path)  # type: ignore[attr-defined]
    elif fam == "macos":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def open_app(url: str) -> None:
    """The app window when available (packaged app), otherwise the browser."""
    from .window import open_app_detached, window_available

    if window_available():
        open_app_detached()
    else:
        webbrowser.open(url)


def open_setup() -> None:
    """Open the setup wizard in its own process (it may re-register this tray app when the role changes)."""
    from .paths import self_command

    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os_family() == "windows":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(self_command("setup", gui=True), **kw)


async def _poll(config, state: TrayState, on_change, stop: asyncio.Event) -> None:
    headers = {"Authorization": f"Bearer {config.token}"} if config.token else {}
    async with httpx.AsyncClient(base_url=config.hub_url, headers=headers, timeout=15) as http:
        while not stop.is_set():
            try:
                r = await http.get("/api/summary")
                r.raise_for_status()
                state.summary, state.error = r.json(), None
            except Exception as exc:
                state.error = str(exc)
            on_change()
            try:
                await asyncio.wait_for(stop.wait(), 20)
            except asyncio.TimeoutError:
                pass


def run_tray(config) -> None:
    import pystray  # imported here: it needs a desktop session

    state = TrayState()
    url = dashboard_url(config)
    stop_event: asyncio.Event | None = None
    loop_holder: dict = {}

    def alert_items():
        alerts = (state.summary or {}).get("alerts", [])
        if not alerts:
            return [pystray.MenuItem("No alerts", None, enabled=False)]
        return [pystray.MenuItem(a["title"][:90], lambda *_: webbrowser.open(url + "/#plans")) for a in alerts[:8]]

    def plan_items():
        return [pystray.MenuItem(line[:90], None, enabled=False) for line in state.plan_lines()]

    def pet_action(action):
        from .desktop_pet import control
        try:control(config,action)
        except Exception:log.exception('desktop Laith action failed')

    menu = pystray.Menu(
        pystray.MenuItem("Open Tazrivo", lambda *_: open_app(url), default=True),
        pystray.MenuItem('Show Laith',lambda *_:pet_action('show')),
        pystray.MenuItem('Talk to Laith',lambda *_:pet_action('panel')),
        pystray.MenuItem('Hide Laith',lambda *_:pet_action('hide')),
        pystray.MenuItem(lambda _: state.status_line, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Alerts", pystray.Menu(alert_items)),
        pystray.MenuItem("Plans", pystray.Menu(plan_items)),
        pystray.MenuItem("Open output folder", lambda *_: open_path(str(config.output_dir))),
        pystray.MenuItem("Change setup…", lambda *_: open_setup()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Tazrivo", lambda icon, _: (icon.stop(), _stop_loop())),
    )
    if os_family() == "macos":
        # Menu-bar only: no Dock icon for this background process (the app window has its own).
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

            NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception:  # noqa: BLE001
            log.debug("could not hide the Dock icon", exc_info=True)
    icon = pystray.Icon("switchboard", make_icon(64, "off"), "Tazrivo", menu)
    if os_family() == 'macos':
        try:
            from AppKit import NSApplication
            # NSApplication's delegate is weak: retain it for the tray's full lifetime.
            icon._switchboard_reopen_delegate = macos_reopen_delegate(NSApplication.sharedApplication(), lambda: open_app(url))
        except Exception:log.exception('could not register Finder reopen handling')
    last = {"status": None}

    def on_change() -> None:
        # Never let a drawing problem (e.g. no tray area on this desktop) stop the status checks.
        try:
            if state.status != last["status"]:
                icon.icon = make_icon(64, state.status)
                last["status"] = state.status
            icon.title = state.tooltip
            icon.update_menu()
        except Exception as exc:
            log.debug("tray update failed: %s", exc)
        for a in state.new_alerts():
            desktop_notify(icon, "Tazrivo", a["title"])

    def _stop_loop() -> None:
        loop = loop_holder.get("loop")
        if loop and stop_event:
            loop.call_soon_threadsafe(stop_event.set)

    def background() -> None:
        nonlocal stop_event
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        stop_event = asyncio.Event()

        async def main() -> None:
            from .desktop_pet import available,control,preferences
            if available() and preferences(config)['enabled']:
                with contextlib.suppress(Exception):await asyncio.to_thread(control,config,'show',False)
            tasks = [asyncio.create_task(_poll(config, state, on_change, stop_event))]
            if config.role == "agent":
                from .agent import run_agent
                tasks.append(asyncio.create_task(run_agent(config)))
            await stop_event.wait()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):await asyncio.to_thread(control,config,'stop',False)

        loop.run_until_complete(main())

    threading.Thread(target=background, name="switchboard-bg", daemon=True).start()
    icon.run()
