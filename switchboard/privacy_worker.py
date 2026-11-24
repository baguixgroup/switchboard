"""Standalone stdlib-only worker. Run only through the restricted launcher."""
import ast
import base64
import csv
import io
import json
import math
import os
import resource
import re
import sys


def limits():
    for kind, value in ((resource.RLIMIT_CPU, 2), (resource.RLIMIT_FSIZE, 0),
                        (resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 32), (resource.RLIMIT_NPROC, 0)):
        resource.setrlimit(kind, (value, value))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)


def calculate(expression):
    if not isinstance(expression, str) or not 0 < len(expression) <= 256:
        raise ValueError()
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 64:
        raise ValueError()
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): value = left + right
            elif isinstance(node.op, ast.Sub): value = left - right
            elif isinstance(node.op, ast.Mult): value = left * right
            elif isinstance(node.op, ast.Div): value = left / right
            elif isinstance(node.op, ast.FloorDiv): value = left // right
            else: value = left % right
        else:
            raise ValueError()
        if not math.isfinite(value) or abs(value) > 10**12:
            raise ValueError()
        return value
    return str(visit(tree.body))


def extract(arguments):
    if set(arguments) != {"format", "data_b64"} or arguments["format"] not in ("text", "markdown", "csv", "json"):
        raise ValueError()
    data = base64.b64decode(arguments["data_b64"], validate=True)
    if not 0 < len(data) <= 16384:
        raise ValueError()
    text = data.decode("utf-8-sig")
    if "\x00" in text or data.startswith((b"%PDF", b"PK\x03\x04")):
        raise ValueError()
    if arguments["format"] == "csv":
        rows = list(csv.reader(io.StringIO(text)))
        if not 2 <= len(rows) <= 100 or any(len(row) > 20 for row in rows) or any(not re.fullmatch(r"[\w -]{1,64}",h) for h in rows[0]):
            raise ValueError()
        if any(len(row) != len(rows[0]) for row in rows[1:]):
            raise ValueError()
        text = "\n".join(header + ": " + json.dumps(cell,ensure_ascii=False) for row in rows[1:] for header, cell in zip(rows[0], row))
    elif arguments["format"] == "json":
        value = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        def depth(item, level=0):
            if level > 6:
                raise ValueError()
            if isinstance(item, dict):
                for v in item.values(): depth(v, level + 1)
            if isinstance(item, list):
                for v in item: depth(v, level + 1)
        depth(value)
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    if not text.strip() or len(text) > 8000:
        raise ValueError()
    return text


def execute(value):
    if set(value) != {"tool", "arguments"} or not isinstance(value["arguments"], dict):
        raise ValueError()
    tool, args = value["tool"], value["arguments"]
    if tool == "calculate" and set(args) == {"expression"}:
        return calculate(args["expression"])
    if tool == "text.statistics" and set(args) == {"text"} and isinstance(args["text"], str) and len(args["text"]) <= 8000:
        return json.dumps({"characters": len(args["text"]), "words": len(args["text"].split())})
    if tool == "attachment.extract":
        return extract(args)
    raise ValueError()


def probe(value):
    # Only a launcher test flag reaches this, never a registered tool operation.
    import socket
    result = {"read": False, "network": False, "secret_env": bool(os.environ.get("SWITCHBOARD_WORKER_SENTINEL"))}
    try:
        with open(value["sentinel"], "rb") as stream:
            result["read"] = bool(stream.read(1))
    except OSError:
        pass
    try:
        with socket.create_connection(("127.0.0.1", value["port"]), timeout=.3):
            result["network"] = True
    except OSError:
        pass
    return json.dumps(result)


if __name__ == "__main__":
    try:
        limits()
        raw = sys.stdin.buffer.read(32001)
        if len(raw) > 32000:
            raise ValueError()
        value = json.loads(raw)
        text = probe(value) if sys.argv[1:] == ["--isolation-check"] else execute(value)
        print(json.dumps({"text": text}, ensure_ascii=False))
    except Exception:
        print('{"error":"rejected"}')
        sys.exit(1)
