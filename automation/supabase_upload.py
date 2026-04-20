# -*- coding: utf-8 -*-
"""Upload scraped company CSV rows to Supabase via PostgREST (REST API)."""
from __future__ import annotations

import csv
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable, Optional

from backend_env import backend_env_path


def _env_paths_for_upload(csv_path: str, env_path: Optional[str]) -> list[str]:
    """
    Ordered candidate .env paths: explicit override, then walk parents of the CSV
    folder (macOS: same folder as SalesAutomation.app + CSV), then backend_env_path().
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        p = os.path.normpath(os.path.abspath(p))
        if p not in seen:
            seen.add(p)
            out.append(p)

    if env_path and str(env_path).strip():
        add(env_path)

    cur = os.path.dirname(os.path.abspath(os.path.normpath(csv_path)))
    for _ in range(16):
        add(os.path.join(cur, ".env"))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    add(backend_env_path())
    return out


def _merged_file_env(csv_path: str, env_path: Optional[str]) -> tuple[dict, list[str]]:
    """
    Merge variables from all existing candidate files. Earlier paths win;
    later files only fill keys still empty after strip.
    """
    merged: dict[str, str] = {}
    paths = _env_paths_for_upload(csv_path, env_path)
    for p in paths:
        if not os.path.isfile(p):
            continue
        for k, v in _read_env_file(p).items():
            if k not in merged or not str(merged.get(k) or "").strip():
                merged[k] = v
    return merged, paths


def _read_env_file(path: str) -> dict:
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _derive_supabase_rest_url(database_url: str) -> str:
    """
    Build https://<project_ref>.supabase.co from a Supabase Postgres URL when
    SUPABASE_URL is not set (pooler or db.*.supabase.co style).
    """
    u = (database_url or "").strip()
    if not u or "supabase" not in u.lower():
        return ""
    m = re.match(r"postgresql://postgres\.([^:]+):", u, re.I)
    if m:
        return f"https://{m.group(1)}.supabase.co"
    m2 = re.search(r"@db\.([^.\s]+)\.supabase\.co", u, re.I)
    if m2:
        return f"https://{m2.group(1)}.supabase.co"
    # Direct project URL in string (some templates paste it into DATABASE_URL comment lines — rare)
    m3 = re.search(r"https://([a-z0-9]+)\.supabase\.co", u, re.I)
    if m3:
        return f"https://{m3.group(1)}.supabase.co"
    return ""


def normalize_status_jp(val) -> str:
    s = str(val or "").strip()
    if s.lower() in ("new", "") or s == "New":
        return "新規"
    return s or "新規"


def upload_companies_csv(
    csv_path: str,
    *,
    env_path: Optional[str] = None,
    normalize_status: Callable = normalize_status_jp,
) -> str:
    """
    POST each CSV row to {SUPABASE_URL}/rest/v1/companies with upsert semantics.
    Rows without website are skipped; existing rows (same website) are updated.
    """
    if not os.path.isfile(csv_path):
        return "CSV が見つかりません。アップロードをスキップしました。"

    file_env, env_candidates = _merged_file_env(csv_path, env_path)
    # Path shown in errors: first existing candidate, else CSV-adjacent .env, else backend default
    env_file = next(
        (p for p in env_candidates if os.path.isfile(p)),
        env_candidates[0] if env_candidates else backend_env_path(),
    )

    def gv(key: str) -> str:
        v = file_env.get(key) or os.environ.get(key) or ""
        return str(v).strip().strip('"').strip("'")

    base = (
        gv("SUPABASE_URL")
        or gv("NEXT_PUBLIC_SUPABASE_URL")
        or gv("VITE_SUPABASE_URL")
        or gv("PUBLIC_SUPABASE_URL")
        or _derive_supabase_rest_url(gv("DATABASE_URL"))
    )
    key = (
        gv("SUPABASE_SERVICE_ROLE_KEY")
        or gv("SERVICE_ROLE_KEY")
        or gv("SUPABASE_SECRET_KEY")
        or gv("SUPABASE_KEY")
        or gv("SUPABASE_ANON_KEY")
        or gv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    )
    def _env_hint_block() -> str:
        lines = [
            "",
            "【.env の探索順】スクレイプ結果 CSV と同じフォルダ（および上位フォルダ）の .env を読み込み、"
            "不足分は従来の backend_env_path() をマージします。macOS の .app 利用時は、"
            "SalesAutomation.app と同じフォルダに .env を置いてください。",
            "",
            "検索した .env 候補:",
        ]
        for p in env_candidates[:12]:
            ok = "あり" if os.path.isfile(p) else "なし"
            lines.append(f"  - {p}  ({ok})")
        return "\n".join(lines)

    if not base:
        exists = os.path.isfile(os.path.normpath(env_file))
        return (
            "Supabase の URL が取得できません。.env に SUPABASE_URL=（例: https://xxxx.supabase.co）"
            "または Supabase の DATABASE_URL を設定してください。"
            f"\n\n主参照パス: {env_file}\n（ファイルあり: {'はい' if exists else 'いいえ'}）"
            + _env_hint_block()
            + "\n\n※ Windows .exe では exe と同じフォルダの .env。"
            "別の場所なら環境変数 BACKEND_ENV_PATH（または BACKEND_DOTENV）で .env の絶対パスを指定できます。"
            "\n自動アップロードをスキップしました（CSV のみ保存済み）。"
        )
    if not key:
        return (
            "Supabase の API キーが .env にありません（DATABASE_URL だけではアップロードできません）。"
            "ダッシュボード → Project Settings → API で「service_role」または「anon」キーをコピーし、"
            "SUPABASE_SERVICE_ROLE_KEY（推奨）または SUPABASE_ANON_KEY を .env に追加してください。"
            + _env_hint_block()
            + "\n自動アップロードをスキップしました（CSV のみ保存済み）。"
        )

    synced = skipped = failed = 0
    err_sample = None
    endpoint = f"{base.rstrip('/')}/rest/v1/companies?on_conflict=website"
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                website = (row.get("website") or "").strip()
                if not website:
                    skipped += 1
                    continue
                payload = [
                    {
                        "company_name": (row.get("company_name") or "").strip()
                        or "（名称不明）",
                        "email": (row.get("email") or "").strip() or None,
                        "phone": (row.get("phone") or "").strip() or None,
                        "website": website,
                        "form_url": (row.get("form_url") or "").strip() or None,
                        "address": (row.get("address") or "").strip() or None,
                        "description": (row.get("description") or "").strip() or None,
                        "status": normalize_status(row.get("status")),
                        "created_at": (row.get("created_at") or "").strip()
                        or datetime.now().isoformat(),
                    }
                ]
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    endpoint,
                    data=body,
                    method="POST",
                    headers={
                        "apikey": key,
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        # Upsert by website so a re-scrape updates existing rows instead of 409 skip.
                        "Prefer": "return=minimal,resolution=merge-duplicates",
                    },
                )
                try:
                    with urllib.request.urlopen(req, timeout=90) as resp:
                        if resp.status in (200, 201, 204):
                            synced += 1
                        else:
                            skipped += 1
                except urllib.error.HTTPError as e:
                    failed += 1
                    if err_sample is None:
                        err_sample = (
                            f"HTTP {e.code} "
                            f"{e.read()[:200].decode('utf-8', errors='replace')}"
                        )
    except Exception as e:
        return f"Supabase への送信中にエラー: {e}"

    msg = (
        f"Supabase に反映しました。同期 {synced} 件、スキップ {skipped} 件、失敗 {failed} 件。"
    )
    if err_sample:
        msg += f"\n※一部エラー例: {err_sample}"
    if synced == 0 and skipped == 0 and failed == 0:
        msg = "CSV に有効な行がありませんでした（website 列を確認してください）。"
    elif synced == 0 and failed > 0:
        msg += "\n※同期できた行が 0 件です。.env の SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY と companies テーブル定義を確認してください。"
    return msg
