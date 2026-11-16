"""Push alerts to your phone/desktop through ntfy (free app for Android, iOS, web)."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("switchboard.notify")
PRIORITY = {"critical": 5, "warning": 4, "info": 3}
TAGS = {"critical": ["rotating_light"], "warning": ["warning"], "info": ["information_source"]}


async def send(config, title: str, message: str, severity: str = "info") -> bool:
    n = config.notify or {}
    topic = n.get("ntfy_topic")
    if not topic:
        return False
    body = {"topic": topic, "title": title[:250], "message": message[:3500], "priority": PRIORITY.get(severity, 3),
            "tags": TAGS.get(severity, [])}
    headers = {}
    token = config.secret("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(n.get("ntfy_server", "https://ntfy.sh").rstrip("/") + "/", json=body, headers=headers)
            r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("ntfy send failed: %s", exc)
        return False


async def dispatch(config, store, alerts) -> int:
    """Send alerts that have not been sent before (each alert key is sent once)."""
    sent = 0
    for a in alerts:
        if store.alert_sent(a.key):
            continue
        if await send(config, a.title, a.message, a.severity) or not (config.notify or {}).get("ntfy_topic"):
            store.mark_alert_sent(a.key)
            sent += 1
    return sent
