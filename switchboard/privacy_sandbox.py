"""Fail-closed, credential-free process runner for the fixed local worker."""
import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from .privacy import PrivacyFailure


def sandbox_command(scratch, *, isolation_check=False):
    worker = Path(__file__).with_name("privacy_worker.py").resolve()
    python = Path(sys.executable).resolve()
    runtime = Path(sys.base_prefix).resolve()
    if not worker.is_file() or getattr(sys, "frozen", False):
        raise PrivacyFailure("sandbox_unavailable")
    argv = [str(python), "-I", "-S", "-B", "-X", "utf8", str(worker)]
    if isolation_check:
        argv.append("--isolation-check")
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        quote = json.dumps
        profile = '(version 1)(deny default)(allow file-read-metadata)'
        profile += '(allow process-exec (literal ' + quote(str(python)) + '))'
        profile += '(allow file-read* (subpath ' + quote(str(runtime)) + ') (subpath "/System/Library") (subpath "/usr/lib")'
        for path in (str(worker), "/", "/dev/null", "/dev/random", "/dev/urandom"):
            profile += ' (literal ' + quote(path) + ')'
        profile += ')'
        return ["/usr/bin/sandbox-exec", "-p", profile, *argv]
    if sys.platform.startswith("linux") and Path("/usr/bin/bwrap").is_file():
        command = ["/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--unshare-net", "--die-with-parent",
                   "--new-session", "--clearenv", "--cap-drop", "ALL", "--ro-bind", str(runtime), str(runtime)]
        for path in ("/usr/lib", "/lib", "/lib64"):
            if Path(path).exists() and not str(runtime).startswith(path + "/") and str(runtime) != path:
                command += ["--ro-bind", path, path]
        command += ["--ro-bind", str(worker), str(worker), "--proc", "/proc", "--dev", "/dev",
                    "--tmpfs", "/tmp", "--remount-ro", "/tmp", "--chdir", "/tmp", "--", *argv]
        return command
    raise PrivacyFailure("sandbox_unavailable")


async def _read(stream):
    data = bytearray()
    while True:
        part = await stream.read(4096)
        if not part:
            return bytes(data)
        if len(data) + len(part) > 64000:
            raise PrivacyFailure("local_tool_failed")
        data.extend(part)


class RestrictedWorker:
    async def run(self, value, *, isolation_check=False):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > 32000:
            raise PrivacyFailure("invalid_text_request")
        process = None
        readers = []
        try:
            with tempfile.TemporaryDirectory(prefix="switchboard-private-worker-") as scratch:
                command = sandbox_command(scratch, isolation_check=isolation_check)
                process = await asyncio.create_subprocess_exec(*command, cwd=scratch,
                    env={"LANG": "C", "PATH": "/usr/bin:/bin"}, start_new_session=True, close_fds=True,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                process.stdin.write(raw)
                await asyncio.wait_for(process.stdin.drain(), 1)
                process.stdin.close()
                readers = [asyncio.create_task(_read(process.stdout)), asyncio.create_task(_read(process.stderr)),
                           asyncio.create_task(process.wait())]
                out, _, code = await asyncio.wait_for(asyncio.gather(*readers), 4)
                if code != 0:
                    raise PrivacyFailure("local_tool_failed")
                result = json.loads(out)
                if set(result) != {"text"} or not isinstance(result["text"], str) or not 0 < len(result["text"]) <= 8000:
                    raise PrivacyFailure("local_tool_failed")
                return result["text"]
        except PrivacyFailure:
            raise
        except Exception:
            raise PrivacyFailure("local_tool_failed") from None
        finally:
            if process and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
