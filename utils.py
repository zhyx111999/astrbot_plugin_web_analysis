from __future__ import annotations
import re
from urllib.parse import urlparse

URL_RE = re.compile(r"(https?://[^\s<>\]\"'()]+)", re.IGNORECASE)

def extract_urls(text: str, limit: int = 3) -> list[str]:
    if not text:
        return []
    urls = URL_RE.findall(text)
    cleaned = []
    for u in urls:
        u = u.rstrip(").,;!?，。；！？】】")
        cleaned.append(u)
    seen = set()
    out = []
    for u in cleaned:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def truncate(text: str, max_len: int) -> str:
    if text is None:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n...(truncated)..."

def looks_like_shell_html(html: str) -> bool:
    if not html:
        return True
    lower = html.lower()
    script_cnt = lower.count("<script")
    stripped = re.sub(r"<[^>]+>", " ", html)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return (len(stripped) < 400 and script_cnt >= 10) or ("__next_data__" in lower) or ("id=\"app\"" in lower)
