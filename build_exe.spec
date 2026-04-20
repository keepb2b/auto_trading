# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: one-file GUI .exe (output: dist/SalesAutomation.exe).

Explorer / shell icon: assets/app_brand.png (PyInstaller converts to .ico for the PE resource).
Window / taskbar at runtime: bundled assets/app.ico (multi-size BMP .ico from refresh_app_ico.py).
Build with build.bat or scripts/build_windows_exe.py only.
"""
import os

block_cipher = None
spec_dir = os.path.dirname(os.path.abspath(SPEC))

# UPX off: UPX can break embedded .exe icon resources on Windows.
_brand_png = os.path.join(spec_dir, "assets", "app_brand.png")
_ico_rel = os.path.join("assets", "app.ico")
_app_icon = os.path.join(spec_dir, _ico_rel)
if not os.path.isfile(_brand_png):
    raise SystemExit(f"build_exe.spec: missing {_brand_png}")
if not os.path.isfile(_app_icon):
    raise SystemExit(f"build_exe.spec: missing {_app_icon} (run scripts/refresh_app_ico.py before PyInstaller)")
# PNG here → PyInstaller + Pillow convert to .ico when embedding (reliable shell icon on Windows).
_exe_icon = os.path.normpath(_brand_png).replace("\\", "/")

a = Analysis(
    [os.path.join(spec_dir, "desktop-app.py")],
    pathex=[spec_dir, os.path.join(spec_dir, "automation")],
    binaries=[],
    datas=[
        (os.path.join(spec_dir, "automation"), "automation"),
        (_app_icon, "assets"),
    ],
    hiddenimports=[
            "scraper",
            "backend_env",
            "supabase_upload",
            "scraper",
            "backend_env",
            "supabase_upload",
            "bs4",
            "bs4.builder",
            "bs4.builder._htmlparser",
            "bs4.element",
            "bs4.dammit",
            "bs4.css",
            "soupsieve",
            "lxml",
            "lxml.etree",
            "lxml.html",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pandas.tests",
        "numpy.tests",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SalesAutomation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
)
