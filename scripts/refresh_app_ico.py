# -*- coding: utf-8 -*-
"""Rebuild assets/app.ico from assets/app_brand.png (BMP frames) for Tk / taskbar at runtime.

The .exe file icon in Explorer is taken from app_brand.png at PyInstaller build time (see build_exe.spec).
"""
from __future__ import annotations

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Install Pillow: pip install pillow", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "app_brand.png")
OUT = os.path.join(ROOT, "assets", "app.ico")


def main() -> None:
    if not os.path.isfile(SRC):
        print(f"Missing source: {SRC}", file=sys.stderr)
        sys.exit(1)
    img = Image.open(SRC).convert("RGBA")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    frames = [img.resize(s, Image.Resampling.LANCZOS) for s in sizes]
    frames[0].save(
        OUT,
        format="ICO",
        sizes=[(w, h) for w, h in sizes],
        append_images=frames[1:],
        bitmap_format="bmp",
    )
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
