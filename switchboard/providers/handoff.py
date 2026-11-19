"""Hand a task to a plan's own app (ChatGPT, Grok) when no command-line route exists, e.g. image generation.

The task waits: you open the app (Switchboard can open it on any of your computers with the prompt on the
clipboard), make the image on your plan, and save it. The agent on that computer notices the new file in
its Downloads folder (or a folder you choose) and attaches it to the task. You can also add it by hand.
"""
from __future__ import annotations

import re

from ..toolbox import ToolBox
from .base import Provider, RunRequest, RunResult

_LEAD = re.compile(r"^\s*(please\s+)?(can you\s+)?(generate|create|make|draw|design)\s+(me\s+)?(an?\s+)?(image|picture|illustration)\s+(of\s+)?",
                   re.I)


def image_prompt(text: str) -> str:
    """The user's request, tidied for an image app (their words are kept; nothing is invented)."""
    body = _LEAD.sub("", text.strip()).strip() or text.strip()
    return body[0].upper() + body[1:] if body else body


class HandoffProvider(Provider):
    kind = "handoff"

    def available(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    async def run(self, req: RunRequest, toolbox: ToolBox | None = None) -> RunResult:
        label = self.cfg.get("label", self.name)
        writing = self.cfg.get("task_type") == "writing"
        prompt = (req.prompt.partition("Text to edit:\n")[2] or req.prompt) if writing else image_prompt(req.prompt)
        if writing:
            text = (f"Edit this text with **{label}** using your own account. This is a manual handoff, "
                    "not an API connection. Your subscription's features and limits apply.\n\n"
                    f"Open {label} below, paste the copied text, and make your edits. "
                    "Then use **Paste edited text** here to save the result and finish the task.")
            return RunResult(text=text, provider=self.name, model=req.model.id, stop_reason="handoff",
                             handoff={"app": self.name, "label": label, "prompt": prompt, "task_type": "writing",
                                      "url": self.cfg.get("url", ""), "windows_app": label, "mac_app": label})
        text = (f"This will be made with your **{label}** plan, so it uses no API credit.\n\n"
                f"Prompt (copied for you when you open {label}):\n\n> {prompt}\n\n"
                f"Open {label} on one of your computers below, create the image, and save it to that computer's "
                f"Downloads folder. Switchboard attaches it here automatically. You can also use **Add a file**.")
        return RunResult(text=text, provider=self.name, model=req.model.id, stop_reason="handoff",
                         handoff={"app": self.name, "label": label, "prompt": prompt, "url": self.cfg.get("url", ""),
                                  "windows_app": self.cfg.get("windows_app", label), "mac_app": self.cfg.get("mac_app", label)})
