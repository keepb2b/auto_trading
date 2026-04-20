# -*- coding: utf-8 -*-
"""Resolve path to the API backend `.env` regardless of folder name (e.g. `backend` vs `auto-sending-backend`)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_root() -> Path:
    # automation/backend_env.py -> python_app (this project folder)
    return Path(__file__).resolve().parent.parent


def _repo_root() -> Path:
    # Parent of python_app (monorepo layout: sibling backend/ next to python_app/)
    return Path(__file__).resolve().parent.parent.parent


def _frozen_macos_bundle_outer_dir() -> Path | None:
    """Directory containing the .app bundle (same as CSV folder for frozen macOS builds)."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    cur = Path(sys.executable).resolve().parent
    while cur != cur.parent:
        if cur.name.endswith(".app"):
            return cur.parent
        cur = cur.parent
    return None


def backend_env_path() -> str:
    """
    Path to backend `.env`.

    - ``BACKEND_ENV_PATH`` or ``BACKEND_DOTENV``: use this path (absolute or relative to cwd).
    - ``BACKEND_DIR``: e.g. ``auto-sending-backend`` → ``{repo}/{BACKEND_DIR}/.env``.
    - Otherwise: first existing ``backend/.env`` or ``auto-sending-backend/.env`` under
      the monorepo root, then under ``python_app/``; then ``python_app/.env`` (standalone app).
      If running as a PyInstaller ``.exe``, ``{folder of exe}/.env``.
      Default fallback: ``{monorepo}/backend/.env`` (non-frozen).
    """
    explicit = (
        os.environ.get("BACKEND_ENV_PATH") or os.environ.get("BACKEND_DOTENV") or ""
    ).strip()
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return str(p.resolve())

    # PyInstaller: .env next to exe / beside .app on macOS, then walk up, then cwd.
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        mac_outer = _frozen_macos_bundle_outer_dir()
        search_roots = [mac_outer] if mac_outer is not None else []
        for cand in (
            *(r / ".env" for r in search_roots if r is not None),
            exe_dir / ".env",
            Path.cwd() / ".env",
        ):
            if cand.is_file():
                return str(cand.resolve())
        p = exe_dir
        for _ in range(10):
            cand = p / ".env"
            if cand.is_file():
                return str(cand.resolve())
            if p == p.parent:
                break
            p = p.parent
        if mac_outer is not None:
            return str(mac_outer / ".env")
        return str(exe_dir / ".env")

    root = _repo_root()
    app_root = _app_root()
    back_dir = (os.environ.get("BACKEND_DIR") or "").strip()
    if back_dir:
        for base in (root, app_root):
            cand = base / back_dir / ".env"
            if cand.is_file():
                return str(cand)
        return str(root / back_dir / ".env")

    for name in ("backend", "auto-sending-backend"):
        for base in (root, app_root):
            cand = base / name / ".env"
            if cand.is_file():
                return str(cand)

    app_dotenv = app_root / ".env"
    if app_dotenv.is_file():
        return str(app_dotenv)

    return str(root / "backend" / ".env")
