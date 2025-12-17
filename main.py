from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
import astrbot.api.message_components as Comp # 引入组件用于发图

from .utils import extract_urls, truncate
from .cache import DiskCache
from .analyzer import WebAnalyzerCore
from .renderer import RenderClient

@register(
    "web_analysis",
    "YEZI",
    "网页分析 Pro：静态抓取+动态渲染+LLM深度分析",
    "0.4.0", # 版本升级
    "https://github.com/yezi-ai/astrbot_plugin_web_analysis",
)
class WebAnalysisPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any] = None):
        super().__init__(context)
        self.config = config or {}
        self.cfg = self.config

        data_dir = Path(StarTools.get_data_dir())
        self._cache = None
        if self.cfg.get("enable_cache", True):
            self._cache = DiskCache(
                cache_dir=data_dir / "cache",
                ttl_sec=self.cfg.get("cache_ttl_sec", 3600)
            )

        def _parse_json_cfg(key, default):
            raw = self.cfg.get(key)
            if not raw: return default
            if isinstance(raw, str):
                try: return json.loads(raw)
                except: return default
            return raw

        extra_headers = _parse_json_cfg("extra_headers_json", {})
        cookies = _parse_json_cfg("cookies_json", [])
        site_rules = _parse_json_cfg("site_rules_json", [])
        domain_rules = _parse_json_cfg("domain_rules_json", {})

        self._renderer = None
        render_mode = self.cfg.get("render_mode", "auto")
        if render_mode != "never":
            self._renderer = RenderClient({
                "max_render_concurrency": self.cfg.get("max_render_concurrency", 2),
                "render_timeout_ms": self.cfg.get("render_timeout_ms", 20000),
                "wait_until": "networkidle",
                "user_agent": self.cfg.get("http_user_agent"),
                "proxy": self.cfg.get("http_proxy"),
                "extra_headers": extra_headers,
                "cookies": cookies,
                "site_rules": site_rules,
            })

        self._core = WebAnalyzerCore(
            http_settings={
                "timeout_sec": self.cfg.get("http_timeout_sec", 15),
                "retry_times": 2,
                "proxy": self.cfg.get("http_proxy"),
                "user_agent": self.cfg.get("http_user_agent"),
            },
            render_settings={
                "render_mode": render_mode,
                "enable_render_fallback": True,
                "min_text_length_to_skip_render": self.cfg.get("min_text_length_to_skip_render", 200),
            },
            domain_rules=domain_rules,
            cache=self._cache,
            render_client=self._renderer,
        )
        logger.info(f"[WebAnalysis] Loaded v0.4.0")

    @filter.on_astrbot_loaded()
    async def on_startup(self):
        try:
            await self._core.startup()
            if self._renderer:
                await self._renderer.startup()
        except Exception as e:
            logger.error(f"[WebAnalysis] Startup failed: {e}")

    async def terminate(self):
        await self._core.shutdown()
        if self._renderer:
            await self._renderer.shutdown()

    @filter.command("web")
    async def web_cmd(self, event: AstrMessageEvent, sub: str = "", arg: str = ""):
        sub = (sub or "").lower().strip()
        arg = (arg or "").strip()

        if sub == "analyze":
            if not arg:
                yield event.plain_result("请提供 URL。")
                return
            async for res in self._process_url(event, arg):
                yield res

        elif sub == "diag":
            if not arg:
                yield event.plain_result("请提供 URL。")
                return
            fr = await self._core.fetch_and_extract(arg, need_screenshot=True) # 诊断强制截图
            info = (
                f"🛠️ 诊断报告\nURL: {fr.final_url}\nCode: {fr.status_code}\nTitle: {fr.title}\n"
                f"Len: {len(fr.text)}\nRender: {fr.used_renderer}\n"
                f"Screenshot: {'Yes' if fr.screenshot_path else 'No'}\nError: {fr.error}"
            )
            # 发送图文
            chain = [Comp.Plain(info)]
            if fr.screenshot_path:
                chain.append(Comp.Image.fromFileSystem(fr.screenshot_path))
            yield event.chain_result(chain)

        elif sub == "cache" and arg == "clear":
            if self._cache:
                count = self._cache.clear()
                yield event.plain_result(f"已清除 {count} 个缓存文件。")
            else:
                yield event.plain_result("缓存未启用。")
        else:
            yield event.plain_result("指令错误。可用: analyze, diag, cache clear")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.cfg.get("enable_auto_detect", True):
            return
        text = event.message_str or ""
        if text.startswith(("/", "#", "！")):
            return

        urls = extract_urls(text, limit=self.cfg.get("max_urls_per_message", 1))
        if not urls:
            return
        
        for url in urls:
            async for res in self._process_url(event, url):
                yield res

    async def _process_url(self, event: AstrMessageEvent, url: str):
        # 判断是否需要截图
        screenshot_enabled = self.cfg.get("screenshot_enabled", False)
        screenshot_mode = self.cfg.get("screenshot_mode", "off")
        need_screenshot = screenshot_enabled and (screenshot_mode in ["always", "on_failure"])

        fr = await self._core.fetch_and_extract(url, need_screenshot=need_screenshot)

        if fr.error and not fr.text.strip():
            if "黑名单" in fr.error:
                logger.info(f"[WebAnalysis] Ignored blacklisted: {url}")
                return
            logger.warning(f"[WebAnalysis] Fetch failed: {fr.error}")
            return

        # 基础信息头
        base_info = (
            f"📄 {fr.title or '网页快照'}\n"
            f"🔗 {fr.final_url}\n"
            f"{'-'*20}\n"
        )
        
        # 构建消息链
        chain = []

        # 1. 如果有截图，优先放图
        if fr.screenshot_path:
             chain.append(Comp.Image.fromFileSystem(fr.screenshot_path))

        # 2. 如果不使用 LLM，直接发截断原文
        if not self.cfg.get("enable_llm", True):
            chain.append(Comp.Plain(base_info + truncate(fr.text, 500)))
            yield event.chain_result(chain)
            return

        # 3. 使用 LLM 进行分析
        try:
            summary = await self._call_llm(fr.text)
            chain.append(Comp.Plain(base_info + summary))
            yield event.chain_result(chain)
        except Exception as e:
            logger.error(f"[WebAnalysis] LLM Error: {e}")
            chain.append(Comp.Plain(base_info + f"⚠️ 分析失败: {e}\n" + truncate(fr.text, 200)))
            yield event.chain_result(chain)

        # 清理截图临时文件
        if fr.screenshot_path:
            try: os.remove(fr.screenshot_path)
            except: pass

    async def _call_llm(self, text: str) -> str:
        provider = self.context.get_using_provider()
        
        # 尝试获取默认 Provider
        if not provider and hasattr(self.context, "provider_manager"):
            pm = self.context.provider_manager
            if getattr(pm, "default_provider_id", None):
                provider = self.context.get_provider_by_id(pm.default_provider_id)
        
        if not provider:
            raise RuntimeError("未配置或未启用 LLM 服务")

        max_len = 12000 
        content_truncated = truncate(text, max_len)
        
        # [计划3] 人格化改造
        # 不再使用 system_prompt 参数覆盖，而是拼接到 user prompt
        # 让 LLM 保持原有的人设 (AstrBot System Prompt)，同时执行新任务
        
        tpl = self.cfg.get("analysis_prompt_template", "")
        persona_instruction = self.cfg.get("analysis_prompt_user_persona", "")
        
        # 组装 Prompt
        # 格式：[任务说明] + [风格要求] + [网页内容]
        full_user_prompt = f"{tpl}\n\n【风格要求】\n{persona_instruction}\n\n【网页内容】\n{content_truncated}"
        
        call_kwargs = {
            "prompt": full_user_prompt,
            "session_id": None, 
            "contexts": [],
            "image_urls": []
        }

        # 注入配置
        if self.cfg.get("llm_model"):
            call_kwargs["model"] = self.cfg.get("llm_model")
        if self.cfg.get("llm_base_url"):
            call_kwargs["base_url"] = self.cfg.get("llm_base_url")
        if self.cfg.get("llm_timeout_sec"):
             call_kwargs["timeout"] = float(self.cfg.get("llm_timeout_sec"))

        # 严禁使用 invoke/inspect，必须使用 text_chat
        response = await provider.text_chat(**call_kwargs)

        if response and response.completion_text:
            return response.completion_text.strip()
        if response and hasattr(response, "raw_completion"):
             return str(response.raw_completion)
             
        raise RuntimeError("LLM 返回内容为空")