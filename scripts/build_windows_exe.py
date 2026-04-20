# -*- coding: utf-8 -*-
"""Build SalesAutomation.exe: refresh icon from assets/app_brand.png, then PyInstaller.

Uses a staging dist folder so Win32 resource temp files (RCX*.tmp) do not land next to
your shipped exe in dist/. The final exe is moved to dist/SalesAutomation.exe.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "build_exe.spec")
REFRESH = os.path.join(ROOT, "scripts", "refresh_app_ico.py")
STAGING = os.path.join(ROOT, "build", "exe_staging")
WORK = os.path.join(ROOT, "build", "pyi_work")
FINAL_DIST = os.path.join(ROOT, "dist")
EXE_NAME = "SalesAutomation.exe"


def _retry_remove(path: str, attempts: int = 30) -> None:
    for i in range(attempts):
        try:
            if os.path.isfile(path):
                os.remove(path)
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(0.15)


def _retry_copy_replace(src: str, dst: str, attempts: int = 60) -> None:
    """Copy built exe into dist; retry while AV/Explorer briefly lock the file."""
    last_err: OSError | None = None
    for _ in range(attempts):
        try:
            _retry_remove(dst, attempts=5)
            shutil.copy2(src, dst)
            return
        except OSError as e:
            last_err = e
            time.sleep(0.25)
    raise SystemExit(
        "Could not write %s (file in use). Close SalesAutomation.exe, close Explorer "
        "windows on dist/, wait a few seconds, and run this script again. (%s)"
        % (dst, last_err)
    )


def _clean_rcx_tmp(folder: str) -> None:
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if name.upper().startswith("RCX") and name.lower().endswith(".tmp"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _clear_staging_exe(staging_dir: str, exe_name: str, attempts: int = 40) -> None:
    """Remove prior staging output so PyInstaller can overwrite (Windows file locks)."""
    path = os.path.join(staging_dir, exe_name)
    for _ in range(attempts):
        try:
            if os.path.isfile(path):
                os.remove(path)
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit(
        f"Could not remove locked file: {path}\n"
        "Close SalesAutomation.exe if it is running from that folder, then rebuild."
    )


def main() -> None:
    os.chdir(ROOT)

    subprocess.run([sys.executable, REFRESH], check=True, cwd=ROOT)

    ico = os.path.join(ROOT, "assets", "app.ico")
    brand = os.path.join(ROOT, "assets", "app_brand.png")
    print(f"PyInstaller shell icon (from PNG): {brand}", flush=True)
    print(f"Bundled window icon (.ico): {ico}", flush=True)

    built = os.path.join(STAGING, EXE_NAME)
    final = os.path.join(FINAL_DIST, EXE_NAME)

    if os.path.isdir(STAGING):
        _clear_staging_exe(STAGING, EXE_NAME)
    shutil.rmtree(STAGING, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(STAGING, exist_ok=True)
    os.makedirs(FINAL_DIST, exist_ok=True)
    if os.path.isfile(built):
        _clear_staging_exe(STAGING, EXE_NAME)

    _clean_rcx_tmp(FINAL_DIST)
    _retry_remove(final)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        SPEC,
        "--noconfirm",
        "--distpath",
        STAGING,
        "--workpath",
        WORK,
    ]
    print("Running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)

    if not os.path.isfile(built):
        raise SystemExit(
            f"Build failed: missing {built}\n"
            "Check PyInstaller errors above. If you only ran "
            "`pyinstaller build_exe.spec`, the exe is under dist\\ instead; use build.bat for dist\\SalesAutomation.exe."
        )

    time.sleep(0.5)
    _retry_copy_replace(built, final)

    try:
        _retry_remove(built, attempts=20)
    except OSError:
        print(
            "Note: could not delete staging copy (still locked). You can remove "
            f"build{os.sep}exe_staging{os.sep} manually later.",
            flush=True,
        )

    _clean_rcx_tmp(STAGING)
    shutil.rmtree(STAGING, ignore_errors=True)
    _clean_rcx_tmp(FINAL_DIST)

    print(f"Done: {final}", flush=True)
    print(
        "Ship this file: Explorer icon comes from assets\\app_brand.png; "
        "the running app uses assets\\app.ico for the window. "
        "Rebuild with build.bat after changing the PNG. If Explorer still shows the old icon, clear the icon cache or rename the .exe once.",
        flush=True,
    )


if __name__ == "__main__":
    main()
