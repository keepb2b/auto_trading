"""
Scraper using public APIs and business directories
More reliable than search engines
"""
import sys
import io
# Force UTF-8 output so emoji/unicode prints work on Windows.
# When imported under redirect_stdout (desktop app) or in some GUI contexts, stdout may be None — skip safely.
def _ensure_utf8_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "buffer"):
            continue
        enc = (getattr(stream, "encoding", None) or "").lower()
        if enc != "utf-8":
            wrapped = io.TextIOWrapper(
                stream.buffer, encoding="utf-8", errors="replace"
            )
            setattr(sys, name, wrapped)


_ensure_utf8_stdio()

from bs4 import BeautifulSoup
import requests
import time
import re
import json
import html
import pandas as pd
from typing import List, Dict, Optional, Tuple, Any
import random
import os
import urllib.parse
from urllib.parse import urlparse, parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# スクレイピング対象外ドメイン（SNS・検索・広告など）
skip_domains = (
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "youtu.be", "tiktok.com", "pinterest.com",
    "wikipedia.org", "amazon.", "google.", "gstatic.com", "goo.gl",
    "bing.com", "yahoo.", "duckduckgo.com", "brave.com",
    "reddit.com", "medium.com", "github.com", "stackoverflow.com",
    "apple.com", "microsoft.com", "adobe.com",
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
)


def _http_session_get(session: requests.Session, url: str, **kwargs) -> requests.Response | None:
    """Thread-safe GET with retries (use one Session per worker thread)."""
    kwargs.setdefault("timeout", 28)
    last_err: BaseException | None = None
    retry_limit = 4

    def _retry_after_delay(resp: requests.Response, attempt: int) -> float:
        ra = (resp.headers.get("Retry-After") or "").strip()
        if ra:
            try:
                return max(0.0, min(30.0, float(ra)))
            except ValueError:
                pass
        # Exponential backoff with jitter for transient throttling/server errors.
        return min(8.0, 0.6 * (2**attempt) + random.uniform(0.0, 0.35))

    for attempt in range(retry_limit):
        try:
            r = session.get(url, **kwargs)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < (retry_limit - 1):
                time.sleep(_retry_after_delay(r, attempt))
                continue
            return r
        except requests.RequestException as e:
            last_err = e
            time.sleep(min(8.0, 0.5 * (2**attempt) + random.uniform(0.0, 0.35)))
    if last_err:
        print(f"    GET failed: {str(last_err)[:80]}")
    return None


def _response_text(r: requests.Response) -> str:
    """
    Decode HTML body with a plausible encoding. When the server omits charset,
    requests assumes ISO-8859-1; Japanese sites often send UTF-8 → mojibake
    (e.g. æ±äº¬ for 東京). Use chardet's apparent_encoding or UTF-8 fallback.
    """
    enc = (r.encoding or "").lower()
    if not r.encoding or enc in ("iso-8859-1", "latin-1"):
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _keyword_looks_japanese(keyword: str) -> bool:
    if not keyword:
        return False
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", keyword))


def _cjk_overlap_match(keyword: str, blob: str) -> bool:
    """
    スペース無しの日本語・中国語などの複合語: スニペットに全文が無くても
    連続部分文字列（長い順）が含まれていれば関連とみなす。
    """
    k = (keyword or "").strip()
    if not k or not blob:
        return False
    if k in blob:
        return True
    if len(k) < 4:
        return False
    for n in range(min(len(k), 6), 1, -1):
        for i in range(0, len(k) - n + 1):
            if k[i : i + n] in blob:
                return True
    return False


def _snippet_matches_keyword(keyword: str, title: str, body: str) -> bool:
    """検索スニペットにキーワードの実質トークンが含まれるか（Bing 経由のノイズ結果を落とす）。"""
    blob = f"{title or ''} {body or ''}"
    if not keyword.strip():
        return True
    parts = [p.strip() for p in re.split(r"[\s　]+", keyword.strip()) if len(p.strip()) >= 2]
    if not parts:
        return keyword.strip() in blob
    longest = max(parts, key=len)
    if longest in blob:
        return True
    if any(p in blob for p in parts if len(p) >= 3):
        return True
    for p in parts:
        if len(p) >= 4 and _keyword_looks_japanese(p) and _cjk_overlap_match(p, blob):
            return True
    return False


def _serp_keyword_match_score(keyword: str, title: str, body: str) -> int:
    """How many meaningful keyword tokens appear in SERP title+snippet (for picking best URL per domain)."""
    blob = f"{title or ''} {body or ''}"
    if not keyword.strip():
        return 0
    parts = [p.strip() for p in re.split(r"[\s　]+", keyword.strip()) if len(p.strip()) >= 2]
    if not parts:
        return 1 if keyword.strip() in blob else 0
    return sum(1 for p in parts if p in blob)


def _serp_weighted_relevance_score(keyword: str, url: str, title: str, snippet: str) -> int:
    """
    Weighted relevance score for prioritization:
    title token hits > snippet hits > url token hits.
    """
    if not keyword.strip():
        return 0
    parts = [p.strip().lower() for p in re.split(r"[\s　]+", keyword.strip()) if len(p.strip()) >= 2]
    if not parts:
        return 0
    t_blob = (title or "").lower()
    s_blob = (snippet or "").lower()
    u_blob = (url or "").lower()
    score = 0
    for p in parts:
        if p in t_blob:
            score += 8
        if p in s_blob:
            score += 3
        if p in u_blob:
            score += 1
    return score


def _weak_url_matches_keyword(url: str, keyword: str) -> bool:
    """When SERP has no title/snippet (e.g. redirect wrappers), require tokens in path or query."""
    if not (keyword or "").strip():
        return True
    try:
        p = urlparse(url)
        blob = f"{(p.path or '').lower()}?{(p.query or '').lower()}"
    except Exception:
        return False
    parts = [x.strip().lower() for x in re.split(r"[\s　]+", keyword.strip()) if len(x.strip()) >= 2]
    if not parts:
        return keyword.strip().lower() in blob
    longest = max(parts, key=len)
    if longest in blob:
        return True
    return any(len(x) >= 4 and x in blob for x in parts)


def _email_is_plausible(addr: str) -> bool:
    if not addr or "@" not in addr:
        return False
    local, _, domain = addr.partition("@")
    if len(local) < 1 or len(local) > 64 or len(domain) < 3:
        return False
    if ".." in addr or addr.startswith("@") or addr.endswith("@"):
        return False
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", addr):
        return False
    low = addr.lower()
    junk_tlds = (".png", ".jpg", ".gif", ".webp", ".svg", ".css", ".js")
    if any(low.endswith(x) for x in junk_tlds):
        return False
    return True


def _decode_cf_email(protected: str) -> Optional[str]:
    """Cloudflare email-protection hex string → plain email."""
    if not protected or len(protected) < 4:
        return None
    protected = protected.strip()
    try:
        r = int(protected[:2], 16)
        chars: List[str] = []
        i = 2
        while i + 1 <= len(protected):
            c = int(protected[i : i + 2], 16) ^ r
            if c < 32 or c > 0x10FFFF:
                return None
            chars.append(chr(c))
            i += 2
        s = "".join(chars)
        return s if _email_is_plausible(s) else None
    except (ValueError, TypeError):
        return None


def _walk_json_for_emails(obj: Any, out: List[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk == "email" and isinstance(v, str) and "@" in v:
                out.append(v.strip())
            elif lk == "contactpoint" and isinstance(v, dict):
                e = v.get("email")
                if isinstance(e, str) and "@" in e:
                    out.append(e.strip())
            _walk_json_for_emails(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _walk_json_for_emails(x, out)


def _emails_from_json_ld(soup: BeautifulSoup) -> List[str]:
    out: List[str] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = (script.string or script.get_text() or "").strip()
        if not raw or "@" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _walk_json_for_emails(data, out)
    return out


_PATH_MAIL_HINT = re.compile(
    r"/(contact|inquiry|mail|form|toiawase|support|inquiries)(/|$)",
    re.I,
)


_CONTACT_HINT = re.compile(
    r"contact|inquiry|form|about|company|access|privacy|"
    r"会社|お問|問い合わせ|概要|アクセス|採用|recruit",
    re.I,
)

_SKIP_CRAWL_PATH_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav",
    ".css", ".js", ".xml", ".json",
)


def _url_join(base: str, href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    try:
        return urllib.parse.urljoin(base, href)
    except Exception:
        return None


def _site_domain(url: str) -> str:
    """Normalize host for dedupe (www. stripped, lowercased)."""
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return url


def _safe_int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(str(os.getenv(name, default)).strip())
        return max(lo, min(hi, v))
    except Exception:
        return default


def _normalize_website_for_dedupe(url: str) -> str:
    try:
        p = urlparse((url or "").strip())
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (p.path or "/").rstrip("/").lower() or "/"
        return f"{host}{path}"
    except Exception:
        return (url or "").strip().lower()


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    e = html.unescape(email).strip().lower()
    return e or None


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("81") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    if len(digits) == 10:
        return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return phone.strip()


def _expand_search_queries(keyword: str) -> List[str]:
    """
    Multiple search phrasings per user keyword to improve recall (distinct sites).
    UI unchanged; only internal discovery is broader.
    """
    k = (keyword or "").strip()
    if not k:
        return []
    out: List[str] = [k]
    if _keyword_looks_japanese(k):
        tails = [" 会社", " 法人", " 公式", " お問い合わせ", " ホームページ"]
    else:
        tails = [" company", " official", " contact", " website"]
    for t in tails:
        q = (k + t).strip()
        if q not in out:
            out.append(q)
    return out


def _is_likely_search_engine_url(url: str) -> bool:
    """URL 全体に含まれる 'search' ではなく、検索結果ページらしいパスのみ除外する"""
    try:
        p = urlparse(url.lower())
        path_q = f"{p.path}?{p.query}"
        if "google." in p.netloc and "/search" in p.path:
            return True
        if "bing.com" in p.netloc and "/search" in p.path:
            return True
        if "yahoo." in p.netloc and "/search" in p.path:
            return True
        if "duckduckgo.com" in p.netloc:
            return True
        if "brave.com" in p.netloc and "/search" in p.path:
            return True
        return False
    except Exception:
        return False


class CompanyScraper:
    def __init__(self):
        self.session = requests.Session()
        self._ddgs_disabled = False
        self._ddgs_error_logged = False
        self._last_search_stats: Dict[str, Any] = {}
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        print("✅ Scraper initialized")

    def _session_get(self, url: str, **kwargs) -> requests.Response | None:
        return _http_session_get(self.session, url, **kwargs)

    def _worker_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(dict(self.session.headers))
        return s

    def _session_post(self, url: str, **kwargs) -> requests.Response | None:
        kwargs.setdefault("timeout", 28)
        last_err: BaseException | None = None
        retry_limit = 4

        def _retry_after_delay(resp: requests.Response, attempt: int) -> float:
            ra = (resp.headers.get("Retry-After") or "").strip()
            if ra:
                try:
                    return max(0.0, min(30.0, float(ra)))
                except ValueError:
                    pass
            return min(8.0, 0.6 * (2**attempt) + random.uniform(0.0, 0.35))

        for attempt in range(retry_limit):
            try:
                r = self.session.post(url, **kwargs)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < (retry_limit - 1):
                    time.sleep(_retry_after_delay(r, attempt))
                    continue
                return r
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(8.0, 0.5 * (2**attempt) + random.uniform(0.0, 0.35)))
        if last_err:
            print(f"    POST failed: {str(last_err)[:80]}")
        return None

    def _search_duckduckgo_lite(
        self, keyword: str, max_results: int, max_pages: int = 12
    ) -> List[Tuple[str, str, str]]:
        """DuckDuckGo Lite（HTML）。duckduckgo-search パッケージが Bing 固定のため、こちらを主経路にする。"""
        found: List[Tuple[str, str, str]] = []
        headers = {
            'User-Agent': self.session.headers.get('User-Agent', ''),
            'Referer': 'https://lite.duckduckgo.com/',
        }
        payload: Dict[str, str] = {'q': keyword, 'b': ''}
        if _keyword_looks_japanese(keyword):
            payload['kl'] = 'jp-jp'

        for page in range(max_pages):
            try:
                r = self._session_post(
                    "https://lite.duckduckgo.com/lite/",
                    data=payload,
                    headers=headers,
                )
                if r is None or r.status_code != 200:
                    break
                if b'No results.' in r.content or b'No more results.' in r.content:
                    break
                soup = BeautifulSoup(_response_text(r), 'html.parser')
                page_added = 0
                for a in soup.select('a.result-link'):
                    href = (a.get('href') or '').strip()
                    if href.startswith('http') and 'duckduckgo.com' not in href.lower():
                        title = ''
                        snippet = ''
                        tr = a.find_parent('tr')
                        if tr:
                            cells = [td.get_text(' ', strip=True) for td in tr.find_all('td')]
                            non_url = [t for t in cells if t and href not in t and t not in href]
                            if non_url:
                                title = non_url[0][:200]
                                snippet = ' '.join(non_url)[:400]
                        found.append((href, title, snippet))
                        page_added += 1
                if page_added == 0:
                    break
                if max_results and len(found) >= max_results:
                    break
                forms = soup.select('form.next_form')
                next_form = forms[-1] if forms else None
                if not next_form:
                    break
                payload = {}
                for inp in next_form.find_all('input'):
                    name = inp.get('name')
                    if name:
                        payload[name] = inp.get('value') or ''
                if not payload.get('q'):
                    break
                time.sleep(random.uniform(0.4, 0.9))
            except Exception as e:
                print(f'  DuckDuckGo Lite failed (page {page + 1}): {str(e)[:100]}')
                break

        if found:
            print(f'  DuckDuckGo Lite found {len(found)} URLs')
        return found[:max_results] if max_results else found

    def _search_duckduckgo_api(self, keyword: str, max_results: int) -> List[Tuple[str, str, str]]:
        """duckduckgo-search（内部は Bing）。スニペットがキーワードと一致する URL のみ採用。"""
        if self._ddgs_disabled:
            return []
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            print("  ⚠️ duckduckgo-search が未インストールです。`py -m pip install duckduckgo-search` を実行してください。")
            self._ddgs_disabled = True
            return []
        out: List[Tuple[str, str, str]] = []
        try:
            with DDGS() as ddgs:
                for item in ddgs.text(keyword, max_results=max(max_results, 30)):
                    u = item.get('href') or item.get('url')
                    title = item.get('title') or ''
                    body = item.get('body') or ''
                    if not u or not isinstance(u, str) or not u.startswith('http'):
                        continue
                    if not _snippet_matches_keyword(keyword, title, body):
                        continue
                    out.append((u, title, body))
                    if max_results and len(out) >= max_results:
                        break
            print(f'  DuckDuckGo package (Bing, filtered) found {len(out)} URLs')
        except Exception as e:
            self._ddgs_disabled = True
            if not self._ddgs_error_logged:
                print(f"  DuckDuckGo (API) disabled for this run after failure: {str(e)[:120]}")
                self._ddgs_error_logged = True
        return out

    def _search_duckduckgo_html(self, keyword: str) -> List[Tuple[str, str, str]]:
        """HTML 版 DDG（POST + GET の両方）。API 失敗時のフォールバック。"""
        found: List[Tuple[str, str, str]] = []
        headers = {
            "User-Agent": self.session.headers.get("User-Agent", ""),
            "Referer": "https://html.duckduckgo.com/",
        }
        # POST が現在も有効なことが多い
        for method_name, req in (
            ("POST", lambda: self._session_post(
                "https://html.duckduckgo.com/html/",
                data={"q": keyword},
                headers=headers,
                timeout=28,
            )),
            ("GET", lambda: self._session_get(
                f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(keyword)}",
                headers=headers,
                timeout=28,
            )),
        ):
            try:
                r = req()
                if r is None or r.status_code != 200:
                    continue
                soup = BeautifulSoup(_response_text(r), "html.parser")
                for a in soup.select("a.result__a"):
                    href = (a.get("href") or "").strip()
                    if href.startswith("http") and "duckduckgo.com" not in href:
                        title = a.get_text(" ", strip=True) or ""
                        snippet = ""
                        block = a.find_parent(class_="result") or a.parent
                        if block:
                            sn = block.select_one(".result__snippet") or block.find(
                                class_="result__snippet"
                            )
                            if sn:
                                snippet = sn.get_text(" ", strip=True) or ""
                        found.append((href, title[:200], snippet[:400]))
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "") or ""
                    if "uddg=" not in href and "//duckduckgo.com/l/?" not in href:
                        continue
                    try:
                        if "uddg=" in href:
                            part = href.split("uddg=", 1)[1].split("&")[0]
                            actual = unquote(part)
                        else:
                            q = parse_qs(urlparse(href).query)
                            uddg = q.get("uddg", [""])[0]
                            actual = unquote(uddg) if uddg else ""
                        if actual.startswith("http") and "duckduckgo.com" not in actual:
                            found.append((actual, "", ""))
                    except Exception:
                        pass
                if found:
                    print(f"  DuckDuckGo (HTML {method_name}) found {len(found)} URLs")
                    break
            except Exception as e:
                print(f"  DuckDuckGo (HTML {method_name}) failed: {str(e)[:60]}")
        return found

    def _search_brave_html(self, keyword: str) -> List[Tuple[str, str, str]]:
        """Brave は多くの環境で JS 表示のため空振りしやすいが、取れた場合は利用。"""
        urls: List[Tuple[str, str, str]] = []
        try:
            encoded = urllib.parse.quote_plus(keyword)
            print("  Trying Brave Search (HTML)...")
            brave_url = f"https://search.brave.com/search?q={encoded}"
            response = self._session_get(brave_url, timeout=28)
            if response is None or response.status_code != 200:
                return urls
            soup = BeautifulSoup(_response_text(response), "html.parser")
            blocked_hosts = {
                "status.brave.app",
                "hackerone.com",
                "community.brave.com",
                "support.brave.com",
            }
            for link in soup.find_all("a", href=True):
                href = link.get("href", "") or ""
                anchor_text = (link.get_text(" ", strip=True) or "")[:180]
                if "/url/" in href and "url=" in href:
                    try:
                        actual_url = unquote(href.split("url=")[1].split("&")[0])
                        if (
                            actual_url.startswith("http")
                            and not any(d in actual_url.lower() for d in skip_domains)
                            and _site_domain(actual_url) not in blocked_hosts
                            and _snippet_matches_keyword(keyword, anchor_text, "")
                        ):
                            urls.append((actual_url, anchor_text[:200], ""))
                    except Exception:
                        pass
                elif (
                    href.startswith("http")
                    and "brave.com" not in href
                    and not _is_likely_search_engine_url(href)
                    and not any(d in href.lower() for d in skip_domains)
                    and _site_domain(href) not in blocked_hosts
                    and _snippet_matches_keyword(keyword, anchor_text, "")
                ):
                    urls.append((href, anchor_text[:200], ""))
            print(f"  Brave found {len(urls)} URLs")
        except Exception as e:
            print(f"  Brave search failed: {str(e)[:60]}")
        return urls

    def _search_bing_html(self, keyword: str, max_results: int) -> List[Tuple[str, str, str]]:
        """Bing HTML（直接）。パッケージ経由の Bing と同様、接続できない環境では失敗しうる。"""
        urls: List[Tuple[str, str, str]] = []
        try:
            encoded = urllib.parse.quote_plus(keyword)
            search_url = f"https://www.bing.com/search?q={encoded}&count=50"
            r = self._session_get(search_url, timeout=28)
            if r is None or r.status_code != 200:
                return urls
            soup = BeautifulSoup(_response_text(r), 'html.parser')
            for result in soup.find_all('li', class_='b_algo'):
                link = result.find('a', href=True)
                if not link:
                    continue
                href = (link.get('href') or '').strip()
                if href.startswith('http') and 'bing.com' not in href.lower() and 'microsoft.com' not in href.lower():
                    h2 = result.find('h2')
                    title = h2.get_text(' ', strip=True) if h2 else ''
                    snippet = ''
                    cap = result.find('p') or result.find(class_='b_caption')
                    if cap:
                        snippet = cap.get_text(' ', strip=True) or ''
                    urls.append((href, title[:200], snippet[:400]))
            if urls:
                print(f'  Bing (HTML) found {len(urls)} URLs')
        except Exception as e:
            print(f'  Bing (HTML) failed: {str(e)[:80]}')
        return urls[:max_results] if max_results else urls

    def _collect_hits_for_single_query(
        self, query: str, max_fetch: int, max_pages: int
    ) -> List[Tuple[str, str, str]]:
        """Run the multi-source waterfall for one query; returns (url, title, snippet) each."""
        hits: List[Tuple[str, str, str]] = []
        hits.extend(
            self._search_duckduckgo_lite(
                query, max_results=max_fetch, max_pages=max_pages
            )
        )
        threshold = max(12, min(max_fetch // 2, 40))
        if len(hits) < threshold:
            hits.extend(self._search_duckduckgo_html(query))
        if len(hits) < threshold:
            hits.extend(self._search_bing_html(query, max_fetch))
        if len(hits) < 10:
            hits.extend(self._search_brave_html(query))
        if len(hits) < 8:
            hits.extend(self._search_duckduckgo_api(query, max_results=max_fetch))
        return hits

    @staticmethod
    def _hit_passes_relevance(query: str, url: str, title: str, snippet: str) -> bool:
        if (title or snippet or "").strip():
            return _snippet_matches_keyword(query, title, snippet)
        return _weak_url_matches_keyword(url, query)

    @staticmethod
    def _hit_passes_relaxed_relevance(
        query: str, base_keyword: str, url: str, title: str, snippet: str
    ) -> bool:
        """
        Fallback relevance for broad / arbitrary keywords.
        Prefer strict check first, then accept softer signals:
          - any token match in title/snippet for query or base keyword, or
          - keyword token in URL path/query.
        """
        if CompanyScraper._hit_passes_relevance(query, url, title, snippet):
            return True
        if base_keyword and CompanyScraper._hit_passes_relevance(
            base_keyword, url, title, snippet
        ):
            return True

        blob = f"{title or ''} {snippet or ''}".lower()
        tokens: List[str] = []
        for raw in (query, base_keyword):
            for p in re.split(r"[\s　]+", (raw or "").strip()):
                p = p.strip().lower()
                if len(p) >= 2 and p not in tokens:
                    tokens.append(p)

        if blob and any(t in blob for t in tokens):
            return True
        blob_raw = f"{title or ''} {snippet or ''}"
        for raw in (query, base_keyword):
            for p in re.split(r"[\s　]+", (raw or "").strip()):
                p = p.strip()
                if len(p) >= 4 and _keyword_looks_japanese(p) and _cjk_overlap_match(
                    p, blob_raw
                ):
                    return True
        return _weak_url_matches_keyword(url, base_keyword or query)

    def search_companies(self, keyword: str, num_results: int = 10) -> List[str]:
        """Generate URLs to scrape based on keyword (multi-query + domain dedupe)."""
        print(f"  Generating URLs for: {keyword}")
        # Maximum coverage: large SERP fetch window (cap 600, floor 400 vs num_results).
        max_fetch = min(max(num_results, 400), 600)
        max_pages = 24
        queries = _expand_search_queries(keyword)
        preview = ", ".join(queries[:3])
        if len(queries) > 3:
            preview += f", … (+{len(queries) - 3})"
        print(f"  Query variants ({len(queries)}): {preview}")

        merged: List[Tuple[str, str, str, str]] = []
        for q in queries:
            for url, title, snippet in self._collect_hits_for_single_query(
                q, max_fetch, max_pages
            ):
                merged.append((url, title, snippet, q))

        by_domain: Dict[str, Tuple[str, str, str, str, int, int]] = {}
        domain_order: List[str] = []
        seen_url: set[str] = set()
        dropped_by_relevance = 0
        for url, title, snippet, q in merged:
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            if any(d in url.lower() for d in skip_domains):
                continue
            if not self._hit_passes_relevance(q, url, title, snippet):
                dropped_by_relevance += 1
                continue
            if url in seen_url:
                continue
            seen_url.add(url)
            dom = _site_domain(url)
            score = _serp_keyword_match_score(q, title, snippet)
            weighted = _serp_weighted_relevance_score(q, url, title, snippet)
            if dom not in by_domain:
                domain_order.append(dom)
                by_domain[dom] = (url, title, snippet, q, score, weighted)
            elif (weighted, score) > (by_domain[dom][5], by_domain[dom][4]):
                by_domain[dom] = (url, title, snippet, q, score, weighted)

        prioritized = sorted(
            [by_domain[d] for d in domain_order if d in by_domain],
            key=lambda x: (x[5], x[4]),
            reverse=True,
        )
        clean_urls = [x[0] for x in prioritized]

        # Arbitrary keywords often lose good candidates in strict snippet filtering.
        # If too few URLs survive, run one relaxed pass to recover likely business sites.
        min_target = max(10, min(max_fetch // 2, 30))
        relaxed_added = 0
        if len(clean_urls) < min_target:
            for url, title, snippet, q in merged:
                if len(clean_urls) >= min_target:
                    break
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                if any(d in url.lower() for d in skip_domains):
                    continue
                if url in seen_url:
                    continue
                if not self._hit_passes_relaxed_relevance(
                    q, keyword, url, title, snippet
                ):
                    continue
                dom = _site_domain(url)
                if dom in by_domain:
                    continue
                seen_url.add(url)
                domain_order.append(dom)
                by_domain[dom] = (
                    url,
                    title,
                    snippet,
                    q,
                    _serp_keyword_match_score(q, title, snippet),
                    _serp_weighted_relevance_score(q, url, title, snippet),
                )
                clean_urls.append(url)
                relaxed_added += 1

        # If SERP titles/snippets omit the exact compound keyword (common for CJK),
        # strict + relaxed can still yield zero URLs. Keep raw search hits as last resort.
        rescue_added = 0
        if not clean_urls and merged:
            rescue_cap = min(50, max(max_fetch, 30))
            for url, title, snippet, q in merged:
                if rescue_added >= rescue_cap:
                    break
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                if any(d in url.lower() for d in skip_domains):
                    continue
                dom = _site_domain(url)
                if dom in by_domain:
                    continue
                key = url.rstrip("/")
                if key in seen_url:
                    continue
                seen_url.add(key)
                domain_order.append(dom)
                by_domain[dom] = (url, title, snippet, q, -1, -1)
                clean_urls.append(url)
                rescue_added += 1

        # Return ALL found URLs (not limited to num_results)
        print(f"  ✅ Generated {len(clean_urls)} unique domains / URLs to scrape")
        print(
            f"  ℹ️ Relevance stats: dropped={dropped_by_relevance}, "
            f"relaxed_fallback_added={relaxed_added}, rescue_added={rescue_added}"
        )
        if clean_urls:
            for i, url in enumerate(clean_urls[:5], 1):
                print(f"     [{i}] {url[:65]}...")
            if len(clean_urls) > 5:
                print(f"     ... and {len(clean_urls) - 5} more")

        self._last_search_stats = {
            "keyword": keyword,
            "serp_hits": len(merged),
            "unique_domains": len(by_domain),
            "selected_urls": len(clean_urls),
            "dropped_by_relevance": dropped_by_relevance,
            "relaxed_added": relaxed_added,
            "rescue_added": rescue_added,
        }
        
        return clean_urls

    def _discover_contact_urls(self, page_url: str, soup: BeautifulSoup, limit: int) -> List[str]:
        try:
            base_parsed = urlparse(page_url)
            origin_host = base_parsed.netloc.lower()
        except Exception:
            return []
        seen: set[str] = {page_url.rstrip("/")}
        out: List[str] = []
        for a in soup.find_all("a", href=True):
            if len(out) >= limit:
                break
            href_raw = (a.get("href") or "").strip()
            href_l = href_raw.lower()
            blob = f"{href_l} {(a.get_text() or '').lower()}"
            if not (
                _CONTACT_HINT.search(blob)
                or _PATH_MAIL_HINT.search(href_l)
                or re.search(r"メール|e-mail|mailform|toiawase", blob, re.I)
            ):
                continue
            abs_u = _url_join(page_url, href_raw)
            if not abs_u:
                continue
            try:
                if urlparse(abs_u).netloc.lower() != origin_host:
                    continue
            except Exception:
                continue
            key = abs_u.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            out.append(abs_u)
        return out

    @staticmethod
    def _is_crawlable_internal_url(url: str, origin_host: str) -> bool:
        try:
            p = urlparse(url)
            if p.scheme not in ("http", "https"):
                return False
            host = p.netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            base = origin_host.lower()
            if base.startswith("www."):
                base = base[4:]
            if host != base:
                return False
            path = (p.path or "").lower()
            if any(path.endswith(ext) for ext in _SKIP_CRAWL_PATH_EXT):
                return False
            return True
        except Exception:
            return False

    def _collect_internal_links(
        self, page_url: str, soup: BeautifulSoup, origin_host: str
    ) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            abs_u = _url_join(page_url, a.get("href") or "")
            if not abs_u:
                continue
            if not self._is_crawlable_internal_url(abs_u, origin_host):
                continue
            k = abs_u.rstrip("/")
            if k in seen:
                continue
            seen.add(k)
            out.append(abs_u)
        return out

    def _crawl_site_pages(
        self,
        *,
        start_url: str,
        start_soup: BeautifulSoup,
        seed_urls: List[str],
        max_pages: int,
        max_depth: int,
        http_session: Optional[requests.Session] = None,
    ) -> List[str]:
        """
        Same-domain BFS crawl with seed priority. Returns URLs to visit (excluding start_url).
        """
        try:
            origin_host = urlparse(start_url).netloc.lower()
        except Exception:
            return []
        if not origin_host:
            return []

        sess = http_session or self.session

        seen: set[str] = {start_url.rstrip("/")}
        queue: List[Tuple[str, int]] = []
        out: List[str] = []

        def enqueue(url: str, depth: int) -> None:
            if len(out) >= max_pages:
                return
            key = url.rstrip("/")
            if key in seen:
                return
            if not self._is_crawlable_internal_url(url, origin_host):
                return
            seen.add(key)
            queue.append((url, depth))
            out.append(url)

        # Prioritize discovered contact candidates first.
        for u in seed_urls:
            enqueue(u, 1)
            if len(out) >= max_pages:
                break

        # Then add top-level internal links from first page.
        if len(out) < max_pages and max_depth >= 1:
            for u in self._collect_internal_links(start_url, start_soup, origin_host):
                enqueue(u, 1)
                if len(out) >= max_pages:
                    break

        head = 0
        while head < len(queue) and len(out) < max_pages:
            cur, depth = queue[head]
            head += 1
            if depth >= max_depth:
                continue
            r = _http_session_get(sess, cur, timeout=28, allow_redirects=True)
            if r is None or r.status_code != 200:
                continue
            t = _response_text(r)
            sp = BeautifulSoup(t, "html.parser")
            for nxt in self._collect_internal_links(r.url or cur, sp, origin_host):
                enqueue(nxt, depth + 1)
                if len(out) >= max_pages:
                    break
            time.sleep(random.uniform(0.05, 0.2))

        return out

    @staticmethod
    def _fixed_email_page_candidates(base_url: str) -> List[str]:
        """Common paths where sites expose mailto or text emails."""
        paths = (
            "/contact",
            "/contact/",
            "/contact-us",
            "/contactus",
            "/inquiry",
            "/inquiries",
            "/form",
            "/mail",
            "/mailform",
            "/support",
            "/site/contact",
            "/pages/contact",
            "/お問い合わせ",
            "/お問合せ",
            "/toiawase",
            "/company/contact",
            "/about/contact",
        )
        out: List[str] = []
        for p in paths:
            u = urllib.parse.urljoin(base_url, p)
            if u not in out:
                out.append(u)
        return out

    @staticmethod
    def _collect_emails_from_page(page_url: str, page_text: str, soup: BeautifulSoup) -> List[str]:
        pat = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
        decoded = html.unescape(page_text)
        found: set[str] = set()
        for blob in (page_text, decoded):
            found.update(re.findall(pat, blob))
        for a in soup.select('a[href^="mailto:"]'):
            raw = (a.get("href") or "").split("mailto:", 1)[-1].split("?")[0].strip()
            raw = html.unescape(urllib.parse.unquote(raw))
            if raw:
                found.add(raw)
        for meta in soup.select('meta[itemprop="email"], meta[property="og:email"]'):
            c = (meta.get("content") or "").strip()
            if c and "@" in c:
                found.add(html.unescape(c))
        for el in soup.select("[data-cfemail]"):
            dec = _decode_cf_email(el.get("data-cfemail") or "")
            if dec:
                found.add(dec)
        for a in soup.find_all("a", href=True):
            h = a.get("href") or ""
            if "email-protection#" in h or "/cdn-cgi/l/email-protection#" in h:
                part = h.split("#", 1)[-1].strip()
                dec = _decode_cf_email(part)
                if dec:
                    found.add(dec)
        for e in _emails_from_json_ld(soup):
            found.add(e)
        for sc in soup.find_all("script"):
            st = sc.string or sc.get_text() or ""
            if "@" not in st or len(st) > 250_000:
                continue
            found.update(re.findall(pat, st))
        skip_sub = (
            "example", "test@", "domain", "placeholder", "noreply", "no-reply",
            "sample@", "yourname@", "username@", "email@", "@sentry.io",
            "wix.com", "schema.org", "gravatar.com",
        )
        good: List[str] = []
        for e in found:
            e = e.strip().strip("'\"")
            low = e.lower()
            if any(s in low for s in skip_sub):
                continue
            if not _email_is_plausible(e):
                continue
            good.append(e)
        return good

    @staticmethod
    def _email_pick_score(email: str, page_url: str) -> int:
        s = 0
        low = email.lower()
        for p in ("info@", "contact@", "inquiry@", "office@", "sales@", "support@", "hello@"):
            if low.startswith(p):
                s += 45
                break
        try:
            path = urlparse(page_url).path.lower()
            if _CONTACT_HINT.search(path):
                s += 28
            if _PATH_MAIL_HINT.search(path):
                s += 15
        except Exception:
            pass
        return s

    @staticmethod
    def _pick_best_email(candidates: List[Tuple[str, str]]) -> Optional[str]:
        """(email, page_url) pairs; highest score wins."""
        if not candidates:
            return None
        best_e, best_s = candidates[0][0], CompanyScraper._email_pick_score(candidates[0][0], candidates[0][1])
        for e, pu in candidates[1:]:
            sc = CompanyScraper._email_pick_score(e, pu)
            if sc > best_s or (sc == best_s and len(e) < len(best_e)):
                best_e, best_s = e, sc
        return best_e

    @staticmethod
    def _collect_phones_from_text(page_text: str) -> List[str]:
        patterns = [
            r"(?:\+81[\s-]?)?(?:0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{4})\b",
            r"\b\d{2,4}-\d{2,4}-\d{4}\b",
            r"\b0\d{1,2}-\d{2,4}-\d{4}\b",
        ]
        seen: set[str] = set()
        out: List[str] = []
        for pat in patterns:
            for m in re.findall(pat, page_text):
                digits = re.sub(r"\D", "", m)
                if len(digits) < 10 or len(digits) > 11:
                    continue
                if m in seen:
                    continue
                seen.add(m)
                out.append(m.strip())
        return out

    @staticmethod
    def _extract_address_from_text(page_text: str) -> Optional[str]:
        best: Optional[str] = None
        for kw in ("住所", "address", "所在地", "本社", "〒"):
            if kw not in page_text:
                continue
            idx = page_text.find(kw)
            if idx == -1:
                continue
            snippet = page_text[idx : idx + 280]
            postal_match = re.search(r"〒?\s*\d{3}-?\d{4}", snippet)
            if not postal_match:
                continue
            chunk = snippet[postal_match.start() : postal_match.end() + 80].strip()
            chunk = re.sub(r"<[^>]+>", "", chunk)[:220]
            if best is None or len(chunk) > len(best):
                best = chunk
        return best

    def _best_form_url(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        best: Optional[str] = None
        best_sc = -1
        try:
            host = urlparse(base_url).netloc.lower()
        except Exception:
            host = ""
        for link in soup.find_all("a", href=True):
            href_raw = link.get("href") or ""
            href_l = href_raw.lower()
            text_l = (link.get_text() or "").lower()
            blob = f"{href_l} {text_l}"
            if not any(
                kw in blob
                for kw in (
                    "contact", "inquiry", "form", "お問い合わせ",
                    "問い合わせ", "support",
                )
            ):
                continue
            abs_u = _url_join(base_url, href_raw)
            if not abs_u:
                continue
            try:
                if urlparse(abs_u).netloc.lower() != host:
                    continue
            except Exception:
                continue
            sc = 0
            if "お問" in blob or "問い合わせ" in blob:
                sc += 12
            if "contact" in href_l or "inquiry" in href_l:
                sc += 8
            if sc > best_sc:
                best_sc = sc
                best = abs_u
        return best

    def extract_company_info(
        self,
        url: str,
        _search_keyword: Optional[str] = None,
        *,
        http_session: Optional[requests.Session] = None,
    ) -> Optional[Dict]:
        """Extract company info; keep rows with at least one contact channel."""
        try:
            print(f"    Visiting: {url[:60]}...")

            sess = http_session or self.session
            response = _http_session_get(sess, url, timeout=28, allow_redirects=True)
            if response is None:
                return None
            if response.status_code != 200:
                print(f"    Status {response.status_code}")
                return None

            base_final = response.url or url
            page_text = _response_text(response)
            soup = BeautifulSoup(page_text, "html.parser")

            title_el = soup.find("title")
            company_name = (
                title_el.get_text(strip=True) if title_el else urlparse(url).netloc
            )
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                company_name = og_title.get("content").strip()
            company_name = company_name.split("|")[0].split("-")[0].strip()[:100]

            email_pairs: List[Tuple[str, str]] = []
            phone_pool: List[str] = []
            address_best: Optional[str] = None
            form_best: Optional[str] = None
            form_best_sc = -1
            description: Optional[str] = None

            def absorb_page(pu: str, text: str, sp: BeautifulSoup) -> None:
                nonlocal address_best, form_best, form_best_sc, description
                for e in self._collect_emails_from_page(pu, text, sp):
                    email_pairs.append((e, pu))
                phone_pool.extend(self._collect_phones_from_text(text))
                addr = self._extract_address_from_text(text)
                if addr and (address_best is None or len(addr) > len(address_best)):
                    address_best = addr
                fu = self._best_form_url(sp, pu)
                if fu:
                    sc = 1
                    try:
                        if _CONTACT_HINT.search(urlparse(fu).path):
                            sc += 5
                    except Exception:
                        pass
                    if sc > form_best_sc:
                        form_best_sc = sc
                        form_best = fu
                meta_desc = sp.find("meta", attrs={"name": "description"}) or sp.find(
                    "meta", property="og:description"
                )
                if meta_desc and meta_desc.get("content"):
                    c = meta_desc.get("content").strip()
                    if c and (description is None or len(c) > len(description)):
                        description = c[:300]

            absorb_page(base_final, page_text, soup)

            discovered = self._discover_contact_urls(base_final, soup, limit=16)
            fixed_c = self._fixed_email_page_candidates(base_final)
            seed_urls: List[str] = []
            seen_seed: set[str] = set()
            for u in sorted(
                discovered,
                key=lambda x: (0 if _PATH_MAIL_HINT.search(urlparse(x).path or "") else 1, x),
            ):
                k = u.rstrip("/")
                if k not in seen_seed:
                    seen_seed.add(k)
                    seed_urls.append(u)
            for u in fixed_c:
                k = u.rstrip("/")
                if k not in seen_seed:
                    seen_seed.add(k)
                    seed_urls.append(u)

            max_pages = _safe_int_env("SCRAPER_CRAWL_MAX_PAGES", 220, 20, 600)
            max_depth = _safe_int_env("SCRAPER_CRAWL_MAX_DEPTH", 8, 1, 12)
            stop_after_contact_pages = _safe_int_env(
                "SCRAPER_STOP_AFTER_CONTACT_PAGES", 24, 3, 120
            )
            to_fetch = self._crawl_site_pages(
                start_url=base_final,
                start_soup=soup,
                seed_urls=seed_urls,
                max_pages=max_pages,
                max_depth=max_depth,
                http_session=sess,
            )
            print(
                f"    Crawling site pages: {len(to_fetch)} "
                f"(depth<={max_depth}, budget={max_pages})"
            )

            for visited_count, eu in enumerate(to_fetch, 1):
                time.sleep(random.uniform(0.08, 0.28))
                r2 = _http_session_get(sess, eu, timeout=28, allow_redirects=True)
                if r2 is None or r2.status_code != 200:
                    continue
                t2 = _response_text(r2)
                absorb_page(r2.url or eu, t2, BeautifulSoup(t2, "html.parser"))
                # Adaptive crawl budget: once contact is found, stop after a small additional budget.
                if (
                    len(email_pairs) > 0 or len(phone_pool) > 0 or form_best is not None
                ) and visited_count >= stop_after_contact_pages:
                    break

            email = self._pick_best_email(email_pairs)
            email = _normalize_email(email)

            phone = None
            if phone_pool:
                phone_pool = list(dict.fromkeys(phone_pool))
                phone = sorted(phone_pool, key=lambda p: len(re.sub(r"\D", "", p)), reverse=True)[0]
                phone = _normalize_phone(phone)

            has_email = bool(email and str(email).strip())
            has_form = bool(form_best and str(form_best).strip())
            has_phone = bool(phone and str(phone).strip())
            if not (has_email or has_form or has_phone):
                print(f"    ⏭️  No contact channel (email/form/phone) — skipped")
                return None

            contact_flags = []
            if has_email:
                contact_flags.append("email")
            if has_form:
                contact_flags.append("form")
            if has_phone:
                contact_flags.append("phone")
            print(
                f"    ✅ {company_name[:40]} | Email: {email or 'N/A'} | "
                f"Phone: {phone or 'N/A'} | Contact: {','.join(contact_flags)}"
            )

            return {
                "company_name": company_name,
                "email": email.strip() if has_email else None,
                "phone": phone,
                "website": url,
                "website_canonical": _normalize_website_for_dedupe(url),
                "form_url": form_best,
                "address": address_best,
                "description": description,
                "status": "New",
            }

        except Exception as e:
            print(f"    ❌ Error: {str(e)[:80]}")
            return None

    def _extract_company_for_parallel(
        self, url: str, _search_keyword: Optional[str]
    ) -> Optional[Dict]:
        """Run extract with a thread-local Session (safe for ThreadPoolExecutor)."""
        ws = self._worker_session()
        try:
            return self.extract_company_info(
                url, _search_keyword=_search_keyword, http_session=ws
            )
        finally:
            ws.close()

    def _extract_with_domain_limit(
        self,
        url: str,
        keyword: str,
        *,
        domain_limit: int,
        semaphores: Dict[str, threading.Semaphore],
        sem_lock: threading.Lock,
    ) -> Optional[Dict]:
        dom = _site_domain(url) or "unknown"
        if domain_limit <= 0:
            return self._extract_company_for_parallel(url, keyword)
        with sem_lock:
            sem = semaphores.get(dom)
            if sem is None:
                sem = threading.Semaphore(domain_limit)
                semaphores[dom] = sem
        with sem:
            return self._extract_company_for_parallel(url, keyword)

    def scrape_companies(self, keywords: List[str], results_per_keyword: int = 50) -> pd.DataFrame:
        """Scrape companies; keep leads with at least one contact channel."""
        all_companies = []
        workers = _safe_int_env("SCRAPER_PARALLEL_WORKERS", 8, 1, 32)
        per_domain_limit = _safe_int_env("SCRAPER_PER_DOMAIN_CONCURRENCY", 2, 1, 4)
        keyword_funnels: List[Dict[str, Any]] = []

        for i, keyword in enumerate(keywords, 1):
            print(f"\n[{i}/{len(keywords)}] Keyword: {keyword}")
            
            # Get ALL URLs (not limited)
            urls = self.search_companies(keyword, results_per_keyword)
            
            if not urls:
                print(f"  ⚠️ No URLs generated")
                continue
            
            print(
                f"  Scraping {len(urls)} URLs in parallel (workers={workers}) "
                f"(per-domain={per_domain_limit}, keeping leads with email OR contact form OR phone)..."
            )

            slot: List[Optional[Dict]] = [None] * len(urls)
            semaphores: Dict[str, threading.Semaphore] = {}
            sem_lock = threading.Lock()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(
                        self._extract_with_domain_limit,
                        u,
                        keyword,
                        domain_limit=per_domain_limit,
                        semaphores=semaphores,
                        sem_lock=sem_lock,
                    ): idx
                    for idx, u in enumerate(urls)
                }
                for fut in as_completed(future_map):
                    idx = future_map[fut]
                    try:
                        slot[idx] = fut.result()
                    except Exception as e:
                        print(
                            f"  ⚠️ Worker error for {urls[idx][:60]}...: "
                            f"{str(e)[:120]}"
                        )
                        slot[idx] = None
            for row in slot:
                if row:
                    all_companies.append(row)

            search_stats = dict(self._last_search_stats) if self._last_search_stats else {}
            crawled_ok = sum(1 for x in slot if x is not None)
            contact_found = crawled_ok
            funnel = {
                "keyword": keyword,
                "serp_hits": int(search_stats.get("serp_hits", 0)),
                "unique_domains": int(search_stats.get("unique_domains", 0)),
                "scheduled_urls": len(urls),
                "crawled_ok": crawled_ok,
                "contact_found": contact_found,
            }
            keyword_funnels.append(funnel)
            print(
                "  Funnel: "
                f"SERP={funnel['serp_hits']} -> domains={funnel['unique_domains']} "
                f"-> scheduled={funnel['scheduled_urls']} -> crawled_ok={funnel['crawled_ok']} "
                f"-> contact={funnel['contact_found']}"
            )

            time.sleep(1)
        
        if not all_companies:
            print("\n⚠️ No contactable companies found!")
            return pd.DataFrame(columns=['id', 'company_name', 'email', 'phone', 'website', 'form_url', 'address', 'description', 'status', 'created_at', 'last_contact'])
        
        df = pd.DataFrame(all_companies)
        has_email = df["email"].notna() & (df["email"].astype(str).str.strip() != "")
        has_form = df["form_url"].notna() & (df["form_url"].astype(str).str.strip() != "")
        has_phone = df["phone"].notna() & (df["phone"].astype(str).str.strip() != "")
        df = df[has_email | has_form | has_phone]
        if "website_canonical" in df.columns:
            df = df.drop_duplicates(subset=["website_canonical"])
        else:
            df = df.drop_duplicates(subset=['website'])
        
        df['id'] = range(1, len(df) + 1)
        df['memo'] = ''
        df['created_at'] = pd.Timestamp.now().isoformat()
        df['last_contact'] = None
        
        keep_cols = ['id', 'company_name', 'email', 'phone', 'website', 'form_url', 'address', 'description', 'status', 'created_at', 'last_contact']
        df = df[keep_cols]
        count_email = int((df["email"].notna() & (df["email"].astype(str).str.strip() != "")).sum())
        count_form = int((df["form_url"].notna() & (df["form_url"].astype(str).str.strip() != "")).sum())
        count_phone = int((df["phone"].notna() & (df["phone"].astype(str).str.strip() != "")).sum())
        
        print(
            f"\n✅ Collected: {len(df)} companies "
            f"(email={count_email}, form={count_form}, phone={count_phone})"
        )
        if keyword_funnels:
            print("📊 Keyword funnel summary:")
            for f in keyword_funnels:
                print(
                    f"  - {f['keyword']}: "
                    f"{f['serp_hits']} -> {f['unique_domains']} -> {f['scheduled_urls']} -> "
                    f"{f['crawled_ok']} -> {f['contact_found']}"
                )
        return df
    
    def close(self):
        """Close session"""
        self.session.close()


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING API SCRAPER")
    print("=" * 60)
    
    keywords = ["Tokyo restaurant"]
    
    scraper = CompanyScraper()
    try:
        df = scraper.scrape_companies(keywords, results_per_keyword=5)
        
        if len(df) > 0:
            print("\nResults:")
            for idx, row in df.iterrows():
                print(f"\n[{idx+1}] {row['company_name']}")
                print(f"    Email: {row['email'] or 'N/A'}")
                print(f"    Phone: {row['phone'] or 'N/A'}")
                print(f"    Website: {row['website']}")
            
            df.to_csv("./companies.csv", index=False, encoding="utf-8-sig")
            print(f"\n✅ Saved CSV: ./companies.csv")
            try:
                from supabase_upload import upload_companies_csv

                _csv = os.path.abspath("./companies.csv")
                print(upload_companies_csv(_csv))
            except Exception as e:
                print(f"Supabase アップロード: {e}")

            # Automatically send emails to new companies
            print("\n" + "=" * 60)
            print("AUTO EMAIL SENDING")
            print("=" * 60)
            
            try:
                from auto_mailer import AutoEmailSender
                import asyncio
                
                sender = AutoEmailSender()
                asyncio.run(sender.send_to_new_companies(
                    companies_csv="./companies.csv",
                    templates_csv="./templates.csv",
                    template_id=1
                ))
            except Exception as e:
                print(f"⚠️  Auto email failed: {e}")
                print("   You can send emails manually from the frontend")
        
    finally:
        scraper.close()
