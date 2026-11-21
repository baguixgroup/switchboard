"""Generate Tazrivo app icons from the Converge vector geometry."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from switchboard.tray import make_icon  # noqa: E402


def main() -> None:
    out = ROOT / "build" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    big = make_icon(1024)
    big.save(out / "tazrivo.png")
    big.save(out / "switchboard.png")
    big.save(out / "tazrivo.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    big.save(out / "switchboard.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(out / "tazrivo.icns")
    big.save(out / "switchboard.icns")
    from switchboard.brand import mark_svg
    static=ROOT/'switchboard/web/static'
    for filename,options in [('tazrivo-mark.svg',{}),('tazrivo-app.svg',{'app_icon':True}),('tazrivo-mark-mono.svg',{'monochrome':True}),('tazrivo-mark-light.svg',{'monochrome':True,'light':True})]:
        (static/filename).write_text(mark_svg(**options),encoding='utf-8')
    for filename,size,maskable in [('icon-192.png',192,False),('icon-512.png',512,False),('icon-maskable-512.png',512,True),('apple-touch-icon.png',180,False),('favicon-32.png',32,False)]:
        make_icon(size,maskable=maskable).save(static/filename)
    from PIL import Image
    with Image.open(ROOT/'switchboard/web/static/laith.png') as lion:
        lion.save(out/'laith.icns')
        lion.save(out/'laith.ico',sizes=[(32,32),(64,64),(128,128),(256,256)])
    print(f"icons in {out}")


if __name__ == "__main__":
    main()
