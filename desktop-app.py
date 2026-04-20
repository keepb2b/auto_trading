import tkinter as tk
from tkinter import scrolledtext
import threading
import subprocess
import sys
import os
import stat
import time
import csv
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime


def _darwin_frozen_bundle_parent_dir() -> str | None:
    """
    macOS PyInstaller .app: sys.executable is .../Name.app/Contents/MacOS/name.
    Return the folder that *contains* Name.app (CSV / .env beside the bundle, not inside it).
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    cur = os.path.dirname(os.path.realpath(sys.executable))
    while True:
        if os.path.basename(cur).endswith(".app"):
            return os.path.dirname(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def app_deploy_dir() -> str:
    """Folder with desktop-app.py (dev), .exe dir (Windows onefile), or beside .app (macOS bundle)."""
    if getattr(sys, "frozen", False):
        outer = _darwin_frozen_bundle_parent_dir()
        if outer is not None:
            return outer
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


if getattr(sys, "frozen", False):
    _automation_dir = os.path.join(sys._MEIPASS, "automation")
else:
    _automation_dir = os.path.join(app_deploy_dir(), "automation")
if _automation_dir not in sys.path:
    sys.path.insert(0, _automation_dir)
from backend_env import backend_env_path
from supabase_upload import upload_companies_csv, normalize_status_jp


def _bootstrap_process_env() -> None:
    """
    Load local .env into os.environ (no Git involved).
    Order: .env next to the app / deploy dir first, then backend_env_path() default.
    override=False so real OS env wins over file for the same key.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    paths: list[str] = []
    try:
        paths.append(
            os.path.normpath(os.path.join(os.path.abspath(app_deploy_dir()), ".env"))
        )
    except Exception:
        pass
    try:
        be = os.path.normpath(os.path.abspath(backend_env_path()))
        if be not in paths:
            paths.append(be)
    except Exception:
        pass
    seen: set[str] = set()
    for p in paths:
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        load_dotenv(p, override=False)


_bootstrap_process_env()


def _init_platform_fonts() -> None:
    """Fonts that exist on each OS so layout and Japanese text stay consistent."""
    global UI, UI_SM, UI_BOLD, UI_TITLE, LOG_FONT, EMOJI_LARGE, INDICATOR_FONT
    if sys.platform == "darwin":
        _sans = "Hiragino Sans"
        UI = (_sans, 11)
        UI_SM = (_sans, 10)
        UI_BOLD = (_sans, 12, "bold")
        UI_TITLE = (_sans, 20, "bold")
        LOG_FONT = ("Menlo", 10)
        EMOJI_LARGE = ("Apple Color Emoji", 26)
        INDICATOR_FONT = (_sans, 15)
    elif sys.platform == "win32":
        UI = ("Yu Gothic UI", 10)
        UI_SM = ("Yu Gothic UI", 9)
        UI_BOLD = ("Yu Gothic UI", 12, "bold")
        UI_TITLE = ("Yu Gothic UI", 22, "bold")
        LOG_FONT = ("Consolas", 9)
        EMOJI_LARGE = ("Segoe UI Emoji", 28)
        INDICATOR_FONT = ("Yu Gothic UI", 16)
    else:
        UI = ("DejaVu Sans", 10)
        UI_SM = ("DejaVu Sans", 9)
        UI_BOLD = ("DejaVu Sans", 12, "bold")
        UI_TITLE = ("DejaVu Sans", 20, "bold")
        LOG_FONT = ("DejaVu Sans Mono", 9)
        EMOJI_LARGE = ("Noto Color Emoji", 26)
        INDICATOR_FONT = ("DejaVu Sans", 15)


_init_platform_fonts()


def _mac_ttk_font(root: tk.Misc, *, smaller: bool = False, bold: bool = False):
    """
    Font tuple Tcl actually resolves for TkTextFont (via Font.actual()).
    This is more reliable for Japanese/CJK metrics on macOS than cget('family').
    """
    import tkinter.font as tkfont

    f = tkfont.nametofont("TkTextFont", root).copy()
    if smaller:
        f.configure(size=max(9, int(f.cget("size")) - 1))
    if bold:
        f.configure(weight="bold")
    a = f.actual()
    fam = a.get("family", f.cget("family"))
    sz = int(a.get("size", f.cget("size")))
    if a.get("weight", "normal") == "bold":
        return (fam, sz, "bold")
    return (fam, sz)


def _configure_mac_ttk_styles(root: tk.Misc) -> None:
    """
    macOS Tk ignores bg/fg on many tk.Button widgets (Aqua). Use ttk + clam so
    custom colors match the Windows build.
    """
    if sys.platform != "darwin":
        return
    from tkinter import ttk

    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except tk.TclError:
        return

    def cfg(
        name: str,
        bg: str,
        fg: str,
        active: str | None = None,
        font_tuple=None,
        pad: int | tuple = 8,
    ) -> None:
        active = active or bg
        if font_tuple is None:
            font_tuple = _mac_ttk_font(root)
        s.configure(
            name,
            background=bg,
            foreground=fg,
            fieldbackground=bg,
            borderwidth=0,
            focuscolor="none",
            font=font_tuple,
            padding=pad,
        )
        s.map(
            name,
            background=[("active", active), ("pressed", active)],
            foreground=[("disabled", fg)],
        )

    cfg("SA.ModalMuted.TButton", "#e2e8f0", "#1e293b", "#cbd5e1")
    cfg(
        "SA.Purple.TButton",
        "#8b5cf6",
        "white",
        "#7c3aed",
        _mac_ttk_font(root, bold=True),
        (14, 10),
    )
    cfg(
        "SA.RedLight.TButton",
        "#fee2e2",
        "#dc2626",
        "#fecaca",
        _mac_ttk_font(root, smaller=True),
        (10, 6),
    )
    cfg("SA.Red.TButton", "#ef4444", "white", "#dc2626")
    cfg("SA.Gray.TButton", "#64748b", "white", "#475569")
    cfg("SA.Blue.TButton", "#2563eb", "white", "#1d4ed8")
    # Per-dialog accent; default blue until _centered_modal updates it
    cfg("SA.ModalAccent.TButton", "#2563eb", "white", "#1d4ed8")


def _modal_accent_ttk(root: tk.Misc, accent: str) -> None:
    if sys.platform != "darwin":
        return
    from tkinter import ttk

    def _darken(hex_color: str) -> str:
        try:
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            r, g, b = max(0, r - 25), max(0, g - 25), max(0, b - 25)
            return f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, IndexError):
            return accent

    s = ttk.Style(root)
    s.configure(
        "SA.ModalAccent.TButton",
        background=accent,
        foreground="white",
        fieldbackground=accent,
        borderwidth=0,
        focuscolor="none",
        font=_mac_ttk_font(root),
        padding=8,
    )
    s.map(
        "SA.ModalAccent.TButton",
        background=[("active", _darken(accent)), ("pressed", _darken(accent))],
    )


def _platform_button(
    parent,
    text: str,
    command,
    *,
    mac_style: str,
    win_bg: str,
    win_fg: str = "white",
    font=None,
    **win_extra,
):
    if sys.platform == "darwin":
        from tkinter import ttk

        return ttk.Button(parent, text=text, command=command, style=mac_style)
    kw = {
        "text": text,
        "command": command,
        "bg": win_bg,
        "fg": win_fg,
        "relief": tk.FLAT,
        "cursor": "hand2",
    }
    if font is not None:
        kw["font"] = font
    kw.update(win_extra)
    return tk.Button(parent, **kw)

# スクレイプ結果 CSV：開発時は desktop-app.py と同じフォルダ、Windows .exe 隣、macOS .app はバンドル外（.app と同じフォルダ）
SCRAPED_CSV_NAME = os.getenv("SCRAPED_COMPANIES_CSV", "スクレイプ結果_企業.csv")


def scrape_result_csv_path() -> str:
    """絶対パス。frozen 時は exe 隣（Windows）または .app を含むフォルダ（macOS）。"""
    return os.path.abspath(
        os.path.normpath(os.path.join(app_deploy_dir(), SCRAPED_CSV_NAME))
    )


def _is_csv_write_lock_error(err: BaseException) -> bool:
    if isinstance(err, PermissionError):
        return True
    if isinstance(err, OSError):
        if err.errno == 13:
            return True
        w = getattr(err, "winerror", None)
        if w in (32, 5):
            return True
    return False


def write_scrape_result_csv(df, dest_path: str, *, retries: int = 6) -> None:
    """
    exe 隣のパスへ書き込み。一時ファイル→置換で安全にし、Windows のロックに短時間リトライする。
    """
    dest_path = os.path.abspath(os.path.normpath(dest_path))
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = dest_path + ".writing.tmp"
    last_err: BaseException | None = None
    for attempt in range(retries):
        try:
            if sys.platform == "win32" and os.path.isfile(dest_path):
                try:
                    os.chmod(dest_path, stat.S_IWRITE)
                except OSError:
                    pass
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, dest_path)
            return
        except OSError as e:
            last_err = e
            if not _is_csv_write_lock_error(e):
                try:
                    if os.path.isfile(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
                raise
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        time.sleep(0.35 * (attempt + 1))
    assert last_err is not None
    raise last_err


def format_scrape_csv_error(err: BaseException) -> str:
    base = str(err)
    target = scrape_result_csv_path()
    if _is_csv_write_lock_error(err):
        return (
            f"{base}\n\n"
            f"保存先（実行ファイルと同じフォルダ）:\n{target}\n\n"
            "【よくある原因】\n"
            "・この CSV が Excel などで開かれている（閉じてから再実行）\n"
            "・ウイルス対策が一時的にファイルをロックしている（少し待って再実行）\n"
            "・フォルダへの書き込み権限がない（Program Files 直下は避け、書き込み可能な場所に exe を置く）"
        )
    return base


APP_NAME = "営業自動化システム"
APP_VERSION = "1.0.0"
APP_COPYRIGHT_YEAR = "2026"
APP_RELEASE_YEAR = APP_COPYRIGHT_YEAR  # バージョン情報・著作権表示と揃える


def window_icon_path():
    """Title-bar .ico (generated from assets/app_brand.png via scripts/refresh_app_ico.py)."""
    if getattr(sys, "frozen", False):
        p = os.path.join(sys._MEIPASS, "assets", "app.ico")
        return p if os.path.isfile(p) else None
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")
    return p if os.path.isfile(p) else None


_WINDOWS_APPUSER_MODEL_ID = "SalesAutomation.SalesAutomationApp.Desktop.1.0"
_windows_app_identity_registered = False


def register_windows_app_identity() -> None:
    """
    Must run before any Tk window exists. Stops Windows from grouping this app
    under a generic Python host and helps the taskbar pick up our icon.
    """
    global _windows_app_identity_registered
    if sys.platform != "win32" or _windows_app_identity_registered:
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _WINDOWS_APPUSER_MODEL_ID
        )
        _windows_app_identity_registered = True
    except (AttributeError, OSError, ValueError):
        pass


_win32_icon_cache_path: str | None = None
_win32_icon_cache_handles: tuple = (None, None)


def _windows_apply_icon_native(widget: tk.Misc, ico_abspath: str) -> None:
    """Set title bar / taskbar icons via Win32 WM_SETICON (Tk iconbitmap alone is unreliable)."""
    global _win32_icon_cache_path, _win32_icon_cache_handles
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return

    path = os.path.abspath(os.path.normpath(ico_abspath))
    if not os.path.isfile(path):
        return

    user32 = ctypes.windll.user32
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x10

    if path == _win32_icon_cache_path and (
        _win32_icon_cache_handles[0] or _win32_icon_cache_handles[1]
    ):
        h_small, h_big = _win32_icon_cache_handles
    else:
        _win32_icon_cache_path = path
        _win32_icon_cache_handles = (None, None)

        def load_icon(cx: int, cy: int):
            return user32.LoadImageW(
                wintypes.HINSTANCE(0),
                path,
                IMAGE_ICON,
                cx,
                cy,
                LR_LOADFROMFILE,
            )

        h_small = load_icon(16, 16) or load_icon(24, 24)
        h_big = load_icon(32, 32) or load_icon(48, 48) or load_icon(64, 64)
        if not h_big and h_small:
            h_big = h_small
        if not h_small and h_big:
            h_small = h_big
        if not h_small and not h_big:
            h_big = user32.LoadImageW(
                wintypes.HINSTANCE(0),
                path,
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | 0x40,
            )
            h_small = h_big

        if not h_small and not h_big:
            _win32_icon_cache_path = None
            _win32_icon_cache_handles = (None, None)
            return
        _win32_icon_cache_handles = (h_small, h_big)

    h_small, h_big = _win32_icon_cache_handles

    try:
        widget.update_idletasks()
        widget.update()
    except tk.TclError:
        pass

    wid = int(widget.winfo_id())
    GA_ROOT = 2
    hwnd = user32.GetAncestor(wintypes.HWND(wid), GA_ROOT)
    if not hwnd:
        hwnd = user32.GetParent(wintypes.HWND(wid))
    if not hwnd:
        p = wid
        for _ in range(10):
            q = user32.GetParent(wintypes.HWND(p))
            if not q:
                break
            p = int(q)
        hwnd = wintypes.HWND(p)
    if not hwnd:
        return

    WM_SETICON = 0x80
    ICON_SMALL = 0
    ICON_BIG = 1
    if h_small:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
    if h_big:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)


def apply_window_icon(widget: tk.Misc) -> None:
    """Apply assets/app.ico: Tcl iconbitmap + Win32 WM_SETICON on Windows."""
    p = window_icon_path()
    if not p:
        return
    abspath = os.path.abspath(os.path.normpath(p))
    if not os.path.isfile(abspath):
        return
    try:
        widget.update_idletasks()
        widget.iconbitmap(abspath)
    except tk.TclError:
        pass
    if sys.platform == "win32":
        _windows_apply_icon_native(widget, abspath)


def center_window_on_screen(win: tk.Misc) -> None:
    """Place window geometry so it is centered on the primary monitor."""
    win.update_idletasks()
    geo = win.geometry().split("+", 1)[0]
    try:
        w, h = map(int, geo.split("x", 1))
    except (TypeError, ValueError):
        w = max(win.winfo_reqwidth(), 320)
        h = max(win.winfo_reqheight(), 160)
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")


def _user_manual_text() -> str:
    return """
【ユーザーマニュアル】

■ 検索キーワード
・1 行に 1 キーワード、または 1 行内をカンマで区切って複数指定できます
・例: 「東京都 レンタルスペース」「コワーキング 渋谷」
■ 企業スクレイプ
・「企業スクレイプを開始」を押すと、入力したキーワードで企業情報を収集します
■ 検索結果
・右側のログで進行状況を確認できます。

"""


class SalesAutomationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("営業自動化システム")
        self.root.geometry("900x700")
        self.root.configure(bg="#f0f4f8")

        # Subprocess backend / dev-only (PyInstaller .exe is not a Python interpreter)
        self.python_path = None if getattr(sys, "frozen", False) else sys.executable

        self.backend_process = None
        self.scraper_process = None

        _configure_mac_ttk_styles(self.root)
        self._create_menubar()
        self.create_widgets()
        self.check_status()

    def scraped_csv_path(self) -> str:
        return scrape_result_csv_path()

    def _try_supabase_upload_from_csv(self, csv_path: str) -> str:
        """スクレイプ済み CSV を Supabase PostgREST に 1 行ずつ POST（website 重複は 409 でスキップ）。"""
        return upload_companies_csv(
            csv_path,
            env_path=backend_env_path(),
            normalize_status=normalize_status_jp,
        )

    def _centered_modal(self, title: str, message: str, kind: str):
        """
        Screen-centered modal (replaces tkinter messagebox for predictable placement).
        kind: info | warning | error | yesno | okcancel
        Returns True/False for yesno and okcancel; None otherwise.
        """
        result: dict = {"v": None}
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg="white")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        apply_window_icon(dlg)

        accent = {
            "info": "#2563eb",
            "warning": "#ca8a04",
            "error": "#dc2626",
            "yesno": "#2563eb",
            "okcancel": "#475569",
        }.get(kind, "#2563eb")

        tk.Frame(dlg, bg=accent, height=4).pack(fill=tk.X)
        tk.Label(
            dlg,
            text=message,
            font=UI,
            bg="white",
            fg="#1e293b",
            justify=tk.LEFT,
            wraplength=520,
        ).pack(padx=24, pady=22)

        btn_frame = tk.Frame(dlg, bg="white")
        btn_frame.pack(pady=(0, 22))

        def finish(val):
            result["v"] = val
            dlg.destroy()

        btn_kw = {
            "font": UI,
            "relief": tk.FLAT,
            "cursor": "hand2",
            "padx": 20,
            "pady": 6,
        }

        if kind in ("info", "warning", "error"):
            _modal_accent_ttk(self.root, accent)
            _platform_button(
                btn_frame,
                "OK",
                lambda: finish(True),
                mac_style="SA.ModalAccent.TButton",
                win_bg=accent,
                win_fg="white",
                **btn_kw,
            ).pack()
            dlg.bind("<Return>", lambda e: finish(True))
            dlg.bind("<Escape>", lambda e: finish(True))
            dlg.protocol("WM_DELETE_WINDOW", lambda: finish(True))
        elif kind == "yesno":
            _modal_accent_ttk(self.root, accent)
            row = tk.Frame(btn_frame, bg="white")
            row.pack()
            _platform_button(
                row,
                "はい",
                lambda: finish(True),
                mac_style="SA.ModalAccent.TButton",
                win_bg=accent,
                win_fg="white",
                **btn_kw,
            ).pack(side=tk.LEFT, padx=6)
            _platform_button(
                row,
                "いいえ",
                lambda: finish(False),
                mac_style="SA.ModalMuted.TButton",
                win_bg="#e2e8f0",
                win_fg="#1e293b",
                **btn_kw,
            ).pack(side=tk.LEFT, padx=6)
            dlg.bind("<Escape>", lambda e: finish(False))
            dlg.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        elif kind == "okcancel":
            _modal_accent_ttk(self.root, accent)
            row = tk.Frame(btn_frame, bg="white")
            row.pack()
            _platform_button(
                row,
                "OK",
                lambda: finish(True),
                mac_style="SA.ModalAccent.TButton",
                win_bg=accent,
                win_fg="white",
                **btn_kw,
            ).pack(side=tk.LEFT, padx=6)
            _platform_button(
                row,
                "キャンセル",
                lambda: finish(False),
                mac_style="SA.ModalMuted.TButton",
                win_bg="#e2e8f0",
                win_fg="#1e293b",
                **btn_kw,
            ).pack(side=tk.LEFT, padx=6)
            dlg.bind("<Escape>", lambda e: finish(False))
            dlg.protocol("WM_DELETE_WINDOW", lambda: finish(False))

        center_window_on_screen(dlg)
        self.root.wait_window(dlg)
        v = result["v"]
        if kind == "yesno":
            return v is True
        if kind == "okcancel":
            return v is True
        return None

    def _create_menubar(self) -> None:
        menubar = tk.Menu(self.root, tearoff=0)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.on_closing)
        menubar.add_cascade(label="File", menu=file_menu)

        about_menu = tk.Menu(menubar, tearoff=0)
        about_menu.add_command(
            label="ユーザーマニュアル",
            command=self._show_user_manual,
        )
        about_menu.add_separator()
        about_menu.add_command(
            label="バージョン情報",
            command=self._show_about,
        )
        menubar.add_cascade(label="About", menu=about_menu)

        self.root.config(menu=menubar)

    def _open_app_folder(self) -> None:
        path = app_deploy_dir()
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
            self.log(f"フォルダを開きました: {path}")
        except Exception as e:
            self._centered_modal(
                "エラー",
                f"フォルダを開けませんでした:\n{e}\n\nパス: {path}",
                "error",
            )

    def _show_user_manual(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("ユーザーマニュアル")
        win.geometry("640x480")
        win.configure(bg="white")
        win.transient(self.root)
        apply_window_icon(win)
        txt = scrolledtext.ScrolledText(
            win,
            font=UI,
            wrap=tk.WORD,
            bg="#f8fafc",
            fg="#1e293b",
            relief=tk.FLAT,
            padx=12,
            pady=12
        )
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        txt.insert("1.0", _user_manual_text())
        txt.config(state=tk.DISABLED)
        center_window_on_screen(win)

    def _show_program_overview(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("プログラム概要")
        win.geometry("520x320")
        win.configure(bg="white")
        win.transient(self.root)
        apply_window_icon(win)
        frame = tk.Frame(win, bg="white")
        frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(
            frame,
            text=APP_NAME,
            font=UI_BOLD,
            bg="white",
            fg="#1e293b",
        ).pack(anchor=tk.W)
        tk.Label(
            frame,
            text=f"バージョン {APP_VERSION}",
            font=UI_SM,
            bg="white",
            fg="#64748b",
        ).pack(anchor=tk.W, pady=(4, 12))
        body = scrolledtext.ScrolledText(
            frame,
            font=UI,
            height=10,
            wrap=tk.WORD,
            bg="#f1f5f9",
            fg="#334155",
            relief=tk.FLAT,
        )
        body.pack(fill=tk.BOTH, expand=True)
        body.insert("1.0", _program_overview_text())
        body.config(state=tk.DISABLED)
        _platform_button(
            frame,
            "閉じる",
            win.destroy,
            mac_style="SA.Gray.TButton",
            win_bg="#64748b",
            win_fg="white",
            font=UI,
            padx=16,
            pady=4,
        ).pack(pady=(12, 0))
        center_window_on_screen(win)

    def _show_about(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("バージョン情報")
        win.geometry("420x280")
        win.configure(bg="white")
        win.transient(self.root)
        win.resizable(False, False)
        apply_window_icon(win)
        inner = tk.Frame(win, bg="white")
        inner.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)
        tk.Label(
            inner,
            text="🔍",
            font=EMOJI_LARGE,
            bg="white",
        ).pack()
        tk.Label(
            inner,
            text=APP_NAME,
            font=UI_TITLE,
            bg="white",
            fg="#1e293b",
        ).pack(pady=(8, 4))
        tk.Label(
            inner,
            text=f"Version {APP_VERSION}",
            font=UI_BOLD,
            bg="white",
            fg="#2563eb",
        ).pack()
        tk.Label(
            inner,
            text=f"Copyright © {APP_COPYRIGHT_YEAR}",
            font=UI_SM,
            bg="white",
            fg="#64748b",
        ).pack(pady=(12, 8))
        _platform_button(
            inner,
            "OK",
            win.destroy,
            mac_style="SA.Blue.TButton",
            win_bg="#2563eb",
            win_fg="white",
            font=UI,
            padx=28,
            pady=6,
        ).pack(pady=(20, 0))
        center_window_on_screen(win)

    def create_widgets(self):
        # タイトル
        title_frame = tk.Frame(self.root, bg="#2563eb", height=80)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)
        
        title_label = tk.Label(
            title_frame,
            text="🔍 営業自動化システム",
            font=UI_TITLE,
            bg="#2563eb",
            fg="white"
        )
        title_label.pack(pady=20)
        
        # メインコンテナ
        main_frame = tk.Frame(self.root, bg="#f0f4f8")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # 左側: コントロールパネル
        left_frame = tk.Frame(main_frame, bg="white", relief=tk.RAISED, bd=2)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        status_label = tk.Label(
            left_frame,
            text="システム状態",
            font=UI_BOLD,
            bg="white",
            fg="#1e293b"
        )
        status_label.pack(pady=10)

        self.status_frame = tk.Frame(left_frame, bg="white")
        self.status_frame.pack(fill=tk.X, padx=20, pady=10)

        self.backend_status = self.create_status_indicator(
            self.status_frame, "バックエンド API", 0
        )
        self.frontend_status = self.create_status_indicator(
            self.status_frame, "フロントエンド", 1
        )
        
        button_frame = tk.Frame(left_frame, bg="white")
        button_frame.pack(fill=tk.X, padx=20, pady=10)

        # Keyword input section - Beautiful design
        keyword_container = tk.Frame(button_frame, bg="white")
        keyword_container.pack(fill=tk.X, pady=15)
        
        # Title with icon
        keyword_title_frame = tk.Frame(keyword_container, bg="white")
        keyword_title_frame.pack(fill=tk.X, pady=(0, 10))
        
        keyword_icon = tk.Label(
            keyword_title_frame,
            text="🔎",
            font=INDICATOR_FONT,
            bg="white",
        )
        keyword_icon.pack(side=tk.LEFT, padx=(0, 5))

        keyword_label = tk.Label(
            keyword_title_frame,
            text="検索キーワード",
            font=UI_BOLD,
            bg="white",
            fg="#1e293b"
        )
        keyword_label.pack(side=tk.LEFT)
        
        # Input frame with border
        input_frame = tk.Frame(
            keyword_container,
            bg="#f1f5f9",
            relief=tk.FLAT,
            bd=0
        )
        input_frame.pack(fill=tk.X, pady=5)
        
        # Inner padding frame
        inner_frame = tk.Frame(input_frame, bg="#f1f5f9")
        inner_frame.pack(fill=tk.X, padx=2, pady=2)
        
        # Text widget for multi-line input
        self.keyword_text = tk.Text(
            inner_frame,
            font=UI,
            height=4,
            width=30,
            relief=tk.FLAT,
            bg="white",
            fg="#1e293b",
            insertbackground="#3b82f6",
            wrap=tk.WORD,
            padx=10,
            pady=8
        )
        self.keyword_text.pack(fill=tk.BOTH, expand=True)
        
        default_keywords = """東京都 レンタルスペース
コワーキング 渋谷
会議室 レンタル 東京"""
        self.keyword_text.insert("1.0", default_keywords)
        
        # Hint text with examples
        hint_frame = tk.Frame(keyword_container, bg="white")
        hint_frame.pack(fill=tk.X, pady=(5, 0))
        
        hint_icon = tk.Label(
            hint_frame,
            text="💡",
            font=UI,
            bg="white"
        )
        hint_icon.pack(side=tk.LEFT)

        keyword_hint = tk.Label(
            hint_frame,
            text="1行に1キーワード、またはカンマで区切って入力できます",
            font=UI_SM,
            bg="white",
            fg="#64748b",
            justify=tk.LEFT
        )
        keyword_hint.pack(side=tk.LEFT, padx=5)
        
        # Clear button (no leading emoji on macOS: ttk + emoji often hides CJK glyphs)
        _clear_kw_label = (
            "入力をクリア" if sys.platform == "darwin" else "🗑️ 入力をクリア"
        )
        _platform_button(
            keyword_container,
            _clear_kw_label,
            lambda: self.keyword_text.delete("1.0", tk.END),
            mac_style="SA.RedLight.TButton",
            win_bg="#fee2e2",
            win_fg="#dc2626",
            font=UI_SM,
            padx=10,
            pady=5,
        ).pack()

        # Company scraper button - Enhanced
        scraper_btn = _platform_button(
            button_frame,
            "🔍 企業スクレイプを開始",
            self.run_scraper,
            mac_style="SA.Purple.TButton",
            win_bg="#8b5cf6",
            win_fg="white",
            font=UI_BOLD,
            width=25,
            height=2,
            activebackground="#7c3aed",
        )
        scraper_btn.pack(pady=(15, 5))

      
        # 右側: ログ表示
        right_frame = tk.Frame(main_frame, bg="white", relief=tk.RAISED, bd=2)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        log_label = tk.Label(
            right_frame,
            text="システムログ",
            font=UI_BOLD,
            bg="white",
            fg="#1e293b"
        )
        log_label.pack(pady=10)

        self.log_text = scrolledtext.ScrolledText(
            right_frame,
            width=50,
            height=30,
            font=LOG_FONT,
            bg="#1e293b",
            fg="#10b981",
            insertbackground="white",
            relief=tk.FLAT
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Clear log button: on macOS Aqua, tk.Button may ignore bg color.
        # Use a clickable Label so red background is reliably painted.
        if sys.platform == "darwin":
            clear_btn = tk.Label(
                right_frame,
                text="ログを消去",
                font=_mac_ttk_font(self.root),
                bg="#ef4444",
                fg="white",
                relief=tk.FLAT,
                cursor="hand2",
                highlightthickness=0,
                borderwidth=0,
                padx=14,
                pady=6,
            )
            clear_btn.bind("<Button-1>", lambda _e: self.clear_log())
            clear_btn.bind("<Enter>", lambda _e: clear_btn.config(bg="#dc2626"))
            clear_btn.bind("<Leave>", lambda _e: clear_btn.config(bg="#ef4444"))
            clear_btn.pack(pady=10)
        else:
            _platform_button(
                right_frame,
                "ログを消去",
                self.clear_log,
                mac_style="SA.Red.TButton",
                win_bg="#ef4444",
                win_fg="white",
                font=UI,
            ).pack(pady=10)

        self.log("システムを起動しました")
        self.log("ボタンから操作できます")
    
    def create_status_indicator(self, parent, label, row):
        frame = tk.Frame(parent, bg="white")
        frame.grid(row=row, column=0, sticky="w", pady=5)
        
        indicator = tk.Label(
            frame,
            text="●",
            font=INDICATOR_FONT,
            fg="#ef4444",
            bg="white",
        )
        indicator.pack(side=tk.LEFT, padx=5)

        text = tk.Label(
            frame,
            text=label,
            font=UI,
            bg="white",
            fg="#64748b"
        )
        text.pack(side=tk.LEFT)
        
        return indicator
    
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
    
    def clear_log(self):
        self.log_text.delete(1.0, tk.END)
        self.log("ログを消去しました")
    
    def check_status(self):
        # Backend check
        if self.backend_process and self.backend_process.poll() is None:
            self.backend_status.config(fg="#10b981")
        else:
            self.backend_status.config(fg="#ef4444")

        self.frontend_status.config(fg="#10b981")

        # 1秒後に再チェック
        self.root.after(1000, self.check_status)
    
    def toggle_backend(self):
        if getattr(sys, "frozen", False):
            self.log("バックエンド起動は .exe 版では未対応です（python desktop-app.py で開発実行してください）。")
            return
        if not self.python_path:
            return
        if self.backend_process and self.backend_process.poll() is None:
            self.backend_process.terminate()
            self.log("バックエンドを停止しました")
        else:
            self.log("バックエンドを起動しています…")
            backend_dir = os.path.join(app_deploy_dir(), "backend")
            self.backend_process = subprocess.Popen(
                [self.python_path, "main.py"],
                cwd=backend_dir,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            self.log("バックエンドを起動しました（http://localhost:8000）")

    def load_example_keywords(self):
        examples = """不動産 東京
レンタルスペース 大阪
コワーキング 横浜
会議室 レンタル 名古屋
イベントスペース 福岡
シェアオフィス 札幌"""
        self.keyword_text.delete("1.0", tk.END)
        self.keyword_text.insert("1.0", examples)
        self.log("サンプルキーワードを読み込みました")
    
    def run_scraper(self):
        # Get keywords from text widget
        keywords_text = self.keyword_text.get("1.0", tk.END).strip()
        if not keywords_text:
            self._centered_modal("注意", "検索キーワードを入力してください", "warning")
            return

        keywords = []
        for line in keywords_text.split('\n'):
            for kw in line.split(','):
                kw = kw.strip()
                if kw:
                    keywords.append(kw)

        if not keywords:
            self._centered_modal("注意", "有効なキーワードを入力してください", "warning")
            return

        confirm_msg = f"キーワード {len(keywords)} 件でスクレイプを開始しますか？\n\n"
        confirm_msg += "\n".join([f"・{kw}" for kw in keywords[:5]])
        if len(keywords) > 5:
            confirm_msg += f"\n… ほか {len(keywords) - 5} 件"

        if not self._centered_modal("スクレイプの確認", confirm_msg, "yesno"):
            return

        def scrape():
            self.log("=" * 50)
            self.log(f"🚀 企業スクレイプを開始します（キーワード {len(keywords)} 件）")
            self.log("=" * 50)
            for i, kw in enumerate(keywords, 1):
                self.log(f"  {i}. {kw}")
            self.log("")

            def finish_ok():
                """CSV を今回保存したあと、Supabase に自動反映。"""
                p = self.scraped_csv_path()
                if os.path.isfile(p):
                    self.log(f"📁 CSV を保存しました: {p}")
                    up_msg = self._try_supabase_upload_from_csv(p)
                    self.log(up_msg)
                    self._centered_modal(
                        "完了",
                        "企業スクレイプが完了しました。\n\n"
                        + up_msg
                        + "\n\n詳細はログもご確認ください。",
                        "info",
                    )
                else:
                    self.log("CSV は作成されませんでした（取得件数 0 の可能性があります）。")
                    self._centered_modal(
                        "完了",
                        "スクレイプは終了しましたが、保存する行がありませんでした。",
                        "info",
                    )

            def finish_no_rows():
                """連絡先付き企業が 0 件: CSV を上書きせず、古い CSV を誤って DB に送らない。"""
                self.log(
                    "企業データがありませんでした。"
                    "CSV は更新していません。Supabase 自動アップロードは行いません。"
                )
                self._centered_modal(
                    "完了",
                    "スクレイプは終了しましたが、連絡先のある企業は 0 件でした。\n"
                    "（前回の CSV はそのままです。データベースへの再アップロードはしません。）",
                    "info",
                )

            try:
                from scraper import CompanyScraper

                out_buf = io.StringIO()
                err_buf = io.StringIO()
                scraper = CompanyScraper()
                try:
                    with redirect_stdout(out_buf), redirect_stderr(err_buf):
                        df = scraper.scrape_companies(keywords, results_per_keyword=20)
                finally:
                    scraper.close()

                self.log("")
                self.log("=" * 50)
                self.log("✅ 企業スクレイプが完了しました")
                self.log("=" * 50)
                for line in out_buf.getvalue().split("\n"):
                    if line.strip():
                        self.log(f"  {line}")
                for line in err_buf.getvalue().split("\n"):
                    if line.strip() and "warning" not in line.lower():
                        self.log(f"  ⚠️ {line}")

                csv_path = self.scraped_csv_path()
                if len(df) > 0:
                    if "status" in df.columns:
                        df["status"] = df["status"].replace({"New": "新規", "new": "新規"})
                    write_scrape_result_csv(df, csv_path)
                    self.log(f"SCRAPED_CSV:{csv_path}")
                    self.log(f"保存行数:{len(df)}")
                    self.root.after(0, finish_ok)
                else:
                    self.root.after(0, finish_no_rows)

            except Exception as e:
                self.log(f"❌ エラー: {str(e)}")
                msg = format_scrape_csv_error(e)
                self.root.after(
                    0,
                    lambda m=msg: self._centered_modal(
                        "エラー",
                        f"スクレイプに失敗しました:\n{m}",
                        "error",
                    ),
                )

        threading.Thread(target=scrape, daemon=True).start()

    def on_closing(self):
        if self._centered_modal(
            "終了",
            "システムを終了しますか？\n実行中のプロセスは停止します。",
            "okcancel",
        ):
            if self.backend_process:
                self.backend_process.terminate()
            self.root.destroy()

if __name__ == "__main__":
    register_windows_app_identity()
    root = tk.Tk()
    apply_window_icon(root)
    app = SalesAutomationApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    # Menus / pack can reset decorations; Win32 + Tcl both need a second pass after map.
    root.after_idle(lambda: apply_window_icon(root))
    root.after(150, lambda: apply_window_icon(root))
    root.mainloop()
