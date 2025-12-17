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
    screenshot_path: Optional[str] = None  # [新增] 截图路径

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
        # [关键修复] 计划1: 修复黑名单无效问题
        try:
            dom = urlparse(url).netloc.lower()
            # 修正：Schema 中的 key 是 "allow" 和 "deny"，不是 "allow_domains"
            allow_list = self.domain_rules.get("allow") or self.domain_rules.get("allow_domains") or []
            deny_list = self.domain_rules.get("deny") or self.domain_rules.get("deny_domains") or []
            
            allow = set([d.lower() for d in allow_list if d])
            deny  = set([d.lower() for d in deny_list if d])
            
            # 黑名单优先
            if deny and any(dom == d or dom.endswith("." + d) for d in deny):
                return False
            
            # 白名单模式
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

        # 缓存逻辑 (注意：如果是截图模式，暂时跳过缓存或需升级缓存结构，这里简化为截图时不读缓存文本)
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
            
            doc = Document(text)
            title = doc.short_title()
            content = doc.summary()
            clean_text = BeautifulSoup(content, "lxml").get_text("\n", strip=True)

            used_renderer = False
            screenshot_path = None

            # 决定是否使用渲染器 (SPA检测 OR 强制截图)
            should_render = False
            if self.render_client:
                # 如果开启了截图，必须走渲染器
                if need_screenshot:
                    should_render = True
                # 或者是 SPA 页面
                elif looks_like_shell_html(text):
                    should_render = True
                # 或者配置强制渲染 (always)
                elif self.render_settings.get("render_mode") == "always":
                    should_render = True

            if should_render and self.render_client:
                 try:
                     # [变更] 传递 screenshot 标记
                     r_title, r_text, s_path = await self.render_client.render_extract(final_url, screenshot=need_screenshot)
                     
                     if s_path:
                         screenshot_path = s_path
                     
                     # 只有文本更丰富时才替换文本，但截图是必须保留的
                     if len(r_text) > len(clean_text) or not clean_text:
                         clean_text = r_text
                         title = r_title if r_title else title
                         used_renderer = True
                 except Exception as e:
                     # 如果仅仅是截图失败，不要导致整个流程崩溃，但如果是渲染器崩了，就回退到静态
                     print(f"[WebAnalysis] Render/Screenshot fail: {e}")

            res = FetchResult(
                url=url, final_url=final_url, status_code=status,
                title=title, text=clean_text, used_renderer=used_renderer,
                screenshot_path=screenshot_path
            )
            
            # 仅缓存纯文本结果，带截图的通常是一次性的不缓存
            if self.cache and not screenshot_path:
                self.cache.set(cache_key, res.__dict__)
            return res

        except Exception as e:
            return FetchResult(url=url, final_url=url, status_code=0, title="", text="", used_renderer=False, error=str(e))