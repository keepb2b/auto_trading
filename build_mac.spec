# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: macOS .app bundle (double-click to launch).

Run only on macOS:
  python3 -m PyInstaller build_mac.spec --noconfirm --distpath dist --workpath build/pyi_work_mac

Windows cannot produce a native .app; use GitHub Actions (see .github/workflows/build-macos-app.yml)
or run this on a Mac.
"""
import os
import sys

if sys.platform != "darwin":
    raise SystemExit(
        "build_mac.spec must be run on macOS. "
        "Use GitHub Actions workflow build-macos-app.yml, or a Mac with Python + PyInstaller."
    )

block_cipher = None
spec_dir = os.path.dirname(os.path.abspath(SPEC))

_brand_png = os.path.join(spec_dir, "assets", "app_brand.png")
_ico_rel = os.path.join("assets", "app.ico")
_app_icon = os.path.join(spec_dir, _ico_rel)
_icns = os.path.join(spec_dir, "assets", "AppIcon.icns")
_bundle_icon = _icns if os.path.isfile(_icns) else None

if not os.path.isfile(_brand_png):
    raise SystemExit(f"build_mac.spec: missing {_brand_png}")
if not os.path.isfile(_app_icon):
    raise SystemExit(
        f"build_mac.spec: missing {_app_icon} (run scripts/refresh_app_ico.py before PyInstaller)"
    )

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
        "bs4",
        "bs4.builder",
        "bs4.builder._htmlparser",
        "bs4.element",
        "bs4.dammit",
        "bs4.css",
        "soupsieve",
        "lxml",
        "lxml.etree",
        "lxml.html"
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pandas.tests",
        "numpy.tests",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SalesAutomation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SalesAutomation",
)

app = BUNDLE(
    coll,
    name="SalesAutomation.app",
    icon=_bundle_icon,
    bundle_identifier="com.salesautomation.desktop",
    info_plist={
        "NSPrincipalClass": "NSApplication",
        "NSHighResolutionCapable": True,
        "CFBundleName": "SalesAutomation",
        "CFBundleDisplayName": "営業自動化システム",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
    },
)
