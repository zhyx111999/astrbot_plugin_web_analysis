from __future__ import annotations
import asyncio
import os
import tempfile
import time
from playwright.async_api import async_playwright, Browser

class RenderClient:
    """长生命周期Playwright浏览器：支持 DOM 提取与截图。"""

    def __init__(self, render_settings: dict):
        self.settings = render_settings or {}
        self._pw = None
        self._browser: Browser | None = None
        self._sem = asyncio.Semaphore(int(self.settings.get("max_render_concurrency", 2)))

    async def startup(self):
        if self._browser is not None:
            return
        
        self._pw = await async_playwright().start()
        
        proxy_conf = None
        proxy_str = self.settings.get("proxy")
        if proxy_str and proxy_str.strip():
            proxy_conf = {"server": proxy_str.strip()}

        self._browser = await self._pw.chromium.launch(
            headless=True,
            proxy=proxy_conf
        )

    async def shutdown(self):
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    def _match_rule(self, url: str) -> dict:
        rules = self.settings.get("site_rules") or []
        for r in rules:
            dom = r.get("domain")
            if dom and dom in url:
                return r
        return {}

    async def render_extract(self, url: str, screenshot: bool = False) -> tuple[str, str, str | None]:
        """
        返回: (title, html_content, screenshot_path)
        注意：这里返回的是 HTML 源码，交给 Analyzer 进行清洗
        """
        await self.startup()
        assert self._browser is not None
        
        async with self._sem:
            rule = self._match_rule(url)
            timeout = int(rule.get("timeout_ms") or self.settings.get("render_timeout_ms", 20000))
            wait_until = rule.get("wait_until") or self.settings.get("wait_until") or "networkidle"
            ua = self.settings.get("user_agent") or None

            context = await self._browser.new_context(
                extra_http_headers=self.settings.get("extra_headers") or {},
                user_agent=ua,
                viewport={"width": 1280, "height": 720} if screenshot else None
            )
            
            cookies = self.settings.get("cookies") or []
            if cookies:
                try: await context.add_cookies(cookies)
                except Exception: pass

            page = await context.new_page()
            screenshot_path = None
            
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout)

                sel = rule.get("wait_selector")
                if sel:
                    try: await page.wait_for_selector(sel, timeout=timeout)
                    except Exception: pass
                else:
                    try: await page.wait_for_timeout(1000)
                    except Exception: pass

                title = ""
                try: title = await page.title()
                except Exception: pass

                # [优化] 获取完整 HTML 而不是 innerText，以便后续清洗
                html_content = await page.content()
                
                if screenshot:
                    try:
                        tmp_dir = tempfile.gettempdir()
                        fname = f"astrbot_web_{int(time.time()*1000)}.png"
                        path = os.path.join(tmp_dir, fname)
                        await page.screenshot(path=path, full_page=False)
                        screenshot_path = path
                    except Exception as e:
                        print(f"[WebAnalysis] Screenshot error: {e}")

                return (title or ""), (html_content or ""), screenshot_path
            
            except Exception as e:
                raise e
            finally:
                await context.close()
