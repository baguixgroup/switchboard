"""Draw the app icons from the same mark the tray uses: build/icons/switchboard.{png,ico,icns}."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from switchboard.tray import make_icon  # noqa: E402


def main() -> None:
    out = ROOT / "build" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    big = make_icon(1024)
    big.save(out / "switchboard.png")
    big.save(out / "switchboard.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(out / "switchboard.icns")
    from PIL import Image
    with Image.open(ROOT/'switchboard/web/static/laith.png') as lion:
        lion.save(out/'laith.icns')
        lion.save(out/'laith.ico',sizes=[(32,32),(64,64),(128,128),(256,256)])
    print(f"icons in {out}")


if __name__ == "__main__":
    main()
