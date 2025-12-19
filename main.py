from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
from .analyzer import WebAnalyzerCore
from .renderer import RenderClient
from .cache import DiskCache
from .utils import extract_urls
from pathlib import Path

@register("web_analysis_pro", "YEZI", "深度网页解析Pro", "0.4.0")
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
                # 注意：这里需要解析 JSON 字符串，因为 schema 中定义为 text/json 编辑器
                "site_rules": self._parse_json_config("site_rules_json"),
                "allow": [], # 需要从配置解析
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
            # 发送 "分析中..." 提示 (可选)
            
            # 1. 抓取与提取
            # 注意：screenshot_mode 逻辑需要在 main 处理，这里简化
            need_screenshot = self.config.get("screenshot_enabled", False)
            result = await self.analyzer.fetch_and_extract(url, need_screenshot=need_screenshot)
            
            if result.error:
                logger.error(f"Analysis failed: {result.error}")
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
        # ... 实现 LLM 调用逻辑，使用 result.text ...
        # 参考 ImageGuard 的 provider 调用方式，但要换成 summary prompt
        provider = self.context.get_using_provider()
        if not provider: return f"网页标题: {result.title}\n(LLM未配置，无法总结)"
        
        prompt = self.config.get("analysis_prompt_template", "") + f"\n\n标题: {result.title}\n内容:\n{result.text[:2000]}" # 截断防止超长
        
        try:
            resp = await provider.text_chat(prompt=prompt, session_id=None)
            return resp.completion_text
        except Exception as e:
            return f"LLM 总结失败: {e}"

    async def terminate(self):
        # 插件卸载时关闭资源
        if self.analyzer:
            await self.analyzer.shutdown()
        if self.render_client:
            await self.render_client.shutdown()
