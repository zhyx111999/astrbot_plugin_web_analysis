from __future__ import annotations
import asyncio
import httpx
from bs4 import BeautifulSoup
from readability import Document
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse
from .utils import looks_like_shell_html

@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    title: str
    text: str
    used_renderer: bool
    error: str = ""
    screenshot_path: Optional[str] = None

class WebAnalyzerCore:
    def __init__(self, http_settings, render_settings, domain_rules, cache, render_client):
        self.http_settings = http_settings or {}
        self.render_settings = render_settings or {}
        self.domain_rules = domain_rules or {}
        self.cache = cache
        self.render_client = render_client
        self._client: Optional[httpx.AsyncClient] = None

    async def startup(self):
        if self._client is None:
            # 适配 httpx 0.27+ proxy 参数
            proxy_url = self.http_settings.get("proxy")
            if proxy_url and not proxy_url.strip():
                proxy_url = None
            
            client_kwargs = {
                "timeout": float(self.http_settings.get("timeout_sec", 15)),
                "headers": {"User-Agent": self.http_settings.get("user_agent") or "Mozilla/5.0"},
                "follow_redirects": True,
            }
            if proxy_url:
                client_kwargs["proxy"] = proxy_url

            self._client = httpx.AsyncClient(**client_kwargs)

    async def shutdown(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _domain_allowed(self, url: str) -> bool:
        try:
            dom = urlparse(url).netloc.lower()
            allow_list = self.domain_rules.get("allow") or []
            deny_list = self.domain_rules.get("deny") or []
            
            allow = set([d.lower() for d in allow_list if d])
            deny  = set([d.lower() for d in deny_list if d])
            
            if deny and any(dom == d or dom.endswith("." + d) for d in deny):
                return False
            
            if allow:
                return any(dom == d or dom.endswith("." + d) for d in allow)
            
            return True
        except Exception:
            return True

    async def fetch_and_extract(self, url: str, need_screenshot: bool = False) -> FetchResult:
        # 1. 检查黑名单
        if not self._domain_allowed(url):
            return FetchResult(
                url=url, final_url=url, status_code=0, title="", 
                text="", used_renderer=False, 
                error="域名被规则拦截 (黑名单)"
            )

        # 缓存逻辑 (截图模式跳过缓存)
        cache_key = f"fetch::{url}"
        if self.cache and not need_screenshot:
            cached = self.cache.get(cache_key)
            if cached: return FetchResult(**cached)

        await self.startup()
        try:
            # 重试机制
            retry_times = int(self.http_settings.get("retry_times", 1))
            resp = None
            last_err = ""
            
            for i in range(retry_times + 1):
                try:
                    resp = await self._client.get(url)
                    break
                except Exception as e:
                    last_err = str(e)
                    await asyncio.sleep(0.5)

            if resp is None:
                raise Exception(f"HTTP请求失败: {last_err}")

            text = resp.text
            status = resp.status_code
            final_url = str(resp.url)
            
            # 静态抓取的基础清洗
            doc = Document(text)
            title = doc.short_title()
            content = doc.summary()
            clean_text = BeautifulSoup(content, "lxml").get_text("\n", strip=True)

            used_renderer = False
            screenshot_path = None

            # 决定是否使用渲染器
            should_render = False
            if self.render_client:
                if need_screenshot:
                    should_render = True
                elif looks_like_shell_html(text):
                    should_render = True
                elif self.render_settings.get("render_mode") == "always":
                    should_render = True

            if should_render and self.render_client:
                 try:
                     # [优化] 获取 HTML 源码和截图
                     r_title, r_html, s_path = await self.render_client.render_extract(final_url, screenshot=need_screenshot)
                     
                     if s_path:
                         screenshot_path = s_path
                     
                     # [优化] 对渲染出来的 HTML 进行 Readability 清洗，而不是直接用 innerText
                     if r_html:
                         doc_r = Document(r_html)
                         summary_r = doc_r.summary()
                         r_clean_text = BeautifulSoup(summary_r, "lxml").get_text("\n", strip=True)
                         
                         # 如果渲染并清洗后的文本有效，则采纳
                         if len(r_clean_text) > 100 or (not clean_text and r_clean_text):
                             clean_text = r_clean_text
                             title = r_title if r_title else title
                             used_renderer = True
                 except Exception as e:
                     print(f"[WebAnalysis] Render/Screenshot fail: {e}")

            res = FetchResult(
                url=url, final_url=final_url, status_code=status,
                title=title, text=clean_text, used_renderer=used_renderer,
                screenshot_path=screenshot_path
            )
            
            # 仅缓存纯文本结果
            if self.cache and not screenshot_path:
                self.cache.set(cache_key, res.__dict__)
            return res

        except Exception as e:
            return FetchResult(url=url, final_url=url, status_code=0, title="", text="", used_renderer=False, error=str(e))
