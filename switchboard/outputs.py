"""The dedicated output folder: one sub-folder per task with prompt, answer, code files and images."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

LANG_EXT = {
    "python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts", "tsx": "tsx",
    "jsx": "jsx", "csharp": "cs", "c#": "cs", "cs": "cs", "java": "java", "go": "go", "rust": "rs", "cpp": "cpp",
    "c++": "cpp", "c": "c", "html": "html", "css": "css", "json": "json", "yaml": "yaml", "yml": "yml",
    "bash": "sh", "sh": "sh", "shell": "sh", "zsh": "sh", "powershell": "ps1", "ps1": "ps1", "bat": "bat",
    "sql": "sql", "xml": "xml", "toml": "toml", "dockerfile": "Dockerfile", "php": "php", "ruby": "rb",
    "kotlin": "kt", "swift": "swift", "lua": "lua", "gdscript": "gd", "hlsl": "hlsl", "glsl": "glsl",
    "shader": "shader", "svg": "svg", "ini": "ini", "nginx": "conf",
}
_FENCE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.S | re.M)
_FILENAME = re.compile(r"^[\w.\-/]+\.[\w]+$|^Dockerfile$|^Makefile$")


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].rstrip("-") or "task")


def safe_join(base: Path, rel: str) -> Path:
    """Join a relative path under base, refusing absolute paths and '..'."""
    p = PurePosixPath(rel.replace("\\", "/"))
    parts = [x for x in p.parts if x not in ("", ".", "/")]
    if p.is_absolute() or ".." in parts or not parts or ":" in parts[0]:
        raise ValueError(f"unsafe path: {rel}")
    out = base.joinpath(*parts)
    out.resolve().relative_to(base.resolve())
    return out


def task_folder(base: Path, prompt: str, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    folder = base / now.strftime("%Y-%m-%d") / f"{now.strftime('%H%M%S')}-{slugify(prompt)}"
    n = 2
    while folder.exists():
        folder = folder.with_name(f"{folder.name.rsplit('~', 1)[0]}~{n}")
        n += 1
    folder.mkdir(parents=True)
    return folder


def extract_code_files(text: str) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    used: set[str] = set()
    for i, m in enumerate(_FENCE.finditer(text), start=1):
        info, body = m.group(1).strip(), m.group(2)
        parts = info.replace("title=", " ").replace("filename=", " ").replace('"', " ").replace("'", " ").split()
        first = parts[0] if parts else ""
        lang, name = first.lower(), None
        if ":" in first:
            head, _, maybe = first.partition(":")
            lang = head.lower()
            name = maybe if _FILENAME.match(maybe) else None
        for p in parts[1:]:
            if _FILENAME.match(p):
                name = p
                break
        if not name and _FILENAME.match(first) and "." in first:
            name, lang = first, first.rsplit(".", 1)[-1].lower()
        if not name:
            ext = LANG_EXT.get(lang)
            if not ext:
                continue
            name = ext if ext == "Dockerfile" else f"snippet-{i:02d}.{ext}"
        base_name, k = name, 2
        while name in used:
            stem, dot, ext = base_name.rpartition(".")
            name = f"{stem}-{k}.{ext}" if dot else f"{base_name}-{k}"
            k += 1
        used.add(name)
        files.append((name, body))
    return files


def save_task_output(folder: Path, prompt: str, answer: str, meta: dict,
                     binary_files: list[tuple[str, bytes]] | None = None) -> list[str]:
    written: list[str] = []
    (folder / "prompt.md").write_text(prompt, encoding="utf-8")
    (folder / "response.md").write_text(answer, encoding="utf-8")
    written += ["prompt.md", "response.md"]
    for name, body in extract_code_files(answer):
        try:
            target = safe_join(folder / "files", name)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written.append(str(target.relative_to(folder)).replace("\\", "/"))
    for name, data in binary_files or []:
        try:
            target = safe_join(folder, name)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(name)
    meta = dict(meta, files=written)
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    written.append("meta.json")
    return written
