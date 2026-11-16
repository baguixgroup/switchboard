import pytest

from switchboard.outputs import extract_code_files, safe_join, save_task_output, task_folder

ANSWER = """Here you go.

```python main.py
print("hi")
```

```csharp:Assets/Scripts/Coin.cs
public class Coin {}
```

```js
console.log(1)
```

```text
not a file
```
"""


def test_extract_code_files_names():
    files = dict(extract_code_files(ANSWER))
    assert files["main.py"].strip() == 'print("hi")'
    assert "Assets/Scripts/Coin.cs" in files
    assert any(n.startswith("snippet-") and n.endswith(".js") for n in files)
    assert not any("text" in n for n in files)


def test_safe_join_blocks_traversal(tmp_path):
    assert safe_join(tmp_path, "a/b.txt") == tmp_path / "a" / "b.txt"
    for bad in ("../x", "/etc/passwd", "a/../../x", "C:/Windows/x"):
        with pytest.raises(ValueError):
            safe_join(tmp_path, bad)


def test_save_task_output(tmp_path):
    folder = task_folder(tmp_path, "Build a coin script!")
    written = save_task_output(folder, "prompt", ANSWER, {"model": "m"}, [("image-1.png", b"\x89PNG")])
    assert (folder / "files" / "main.py").exists() and (folder / "files" / "Assets/Scripts/Coin.cs").exists()
    assert (folder / "image-1.png").read_bytes() == b"\x89PNG"
    assert "meta.json" in written and folder.name.endswith("build-a-coin-script")
