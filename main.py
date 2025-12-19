import httpx
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
from .analyzer import WebAnalyzerCore
from .renderer import RenderClient
from .cache import DiskCache
from .utils import extract_urls
from pathlib import Path

@register("web_analysis_pro", "YEZI", "深度网页解析Pro", "0.4.1")
class WebAnalysisPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        
        # 初始化缓存
        cache_path = Path("data/cache/web_analysis")
        self.disk_cache = None
        if config.get("enable_cache", True):
            self.disk_cache = DiskCache(cache_path, ttl_sec=config.get("cache_ttl_sec", 3600))
            
        # 初始化渲染器 (Playwright)
        self.render_client = None
        if config.get("render_mode") != "never":
            self.render_client = RenderClient(config)
            
        # 初始化核心分析器
        self.analyzer = WebAnalyzerCore(
            http_settings={
                "proxy": config.get("http_proxy"),
                "timeout_sec": config.get("http_timeout_sec", 15),
                "user_agent": config.get("http_user_agent"),
                "retry_times": 1
            },
            render_settings=config,
            domain_rules={
                "site_rules": self._parse_json_config("site_rules_json"),
                "allow": [], 
                "deny": self._parse_json_config("domain_rules_json").get("deny", [])
            },
            cache=self.disk_cache,
            render_client=self.render_client
        )

    def _parse_json_config(self, key):
        import json
        try:
            val = self.config.get(key, "[]")
            if isinstance(val, str):
                return json.loads(val)
            return val
        except:
            return []

    # 注册 URL 监听
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.config.get("enable_auto_detect", True):
            return
            
        text = event.message_str
        urls = extract_urls(text, limit=self.config.get("max_urls_per_message", 1))
        
        if not urls:
            return

        for url in urls:
            logger.info(f"[WebAnalysis] Processing: {url}")
            
            # 1. 抓取与提取
            # [Fix] 统一使用 screenshot_mode 判断
            sc_mode = self.config.get("screenshot_mode", "off")
            need_screenshot = (sc_mode == "always")
            
            result = await self.analyzer.fetch_and_extract(url, need_screenshot=need_screenshot)
            
            if result.error:
                # 如果是静默失败模式可以 log warning，这里直接 log error
                logger.warning(f"Analysis failed: {result.error}")
                continue
                
            # 2. 调用 LLM 总结
            if self.config.get("enable_llm", True):
                summary = await self._llm_summarize(result)
                
                # 3. 发送结果
                chain = [Plain(summary)]
                if result.screenshot_path:
                    chain.append(Image.fromFileSystem(result.screenshot_path))
                
                yield event.chain_result(chain)

    async def _llm_summarize(self, result):
        """
        调用 LLM 生成摘要。
        策略：优先使用 config 中定义的独立 LLM 配置；如果未配置，则回退到 context.get_using_provider()
        """
        prompt = self.config.get("analysis_prompt_template", "") + \
                 f"\n\n标题: {result.title}\nURL: {result.final_url}\n内容:\n{result.text[:3000]}" # 3000字符截断

        # 检查是否配置了独立 LLM
        custom_key = self.config.get("llm_api_key")
        custom_base = self.config.get("llm_base_url")
        custom_model = self.config.get("llm_model")

        if custom_key and custom_base:
            # [Iron Rule] 使用独立配置调用 OpenAI 兼容接口
            try:
                async with httpx.AsyncClient(timeout=self.config.get("llm_timeout_sec", 60)) as client:
                    payload = {
                        "model": custom_model or "gpt-3.5-turbo",
                        "messages": [
                            {"role": "system", "content": self.config.get("analysis_prompt_user_persona", "")},
                            {"role": "user", "content": prompt}
                        ]
                    }
                    resp = await client.post(
                        f"{custom_base.rstrip('/')}/v1/chat/completions", # 假设是兼容 OpenAI 的接口
                        json=payload,
                        headers={"Authorization": f"Bearer {custom_key}"}
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"[WebAnalysis] Custom LLM failed: {e}, falling back to system provider.")
                # 失败后继续向下执行，尝试使用系统默认 Provider
        
        # 使用 AstrBot 全局 Provider
        provider = self.context.get_using_provider()
        if not provider: 
            return f"网页标题: {result.title}\n(摘要失败：未配置 LLM)"
        
        try:
            resp = await provider.text_chat(
                prompt=prompt, 
                session_id=None,
                system_prompt=self.config.get("analysis_prompt_user_persona", "")
            )
            return resp.completion_text
        except Exception as e:
            return f"LLM 总结失败: {e}"

    async def terminate(self):
        if self.analyzer:
            await self.analyzer.shutdown()
        if self.render_client:
            await self.render_client.shutdown()
