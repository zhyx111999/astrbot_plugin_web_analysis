# 🌐 网页分析 Pro (WebAnalysis)

**让 AstrBot 能够“看懂”任何网页链接。**

无论是新闻、博客，还是需要加载半天的动态网页（如推特分享页），发送链接即可自动总结。

> **⚠️ 使用必读：**
> 插件目前仅支持识别**包含协议头**的完整网址！
> * ✅ 正确：`https://www.baidu.com`
> * ❌ 错误：`baidu.com`

---

## 🛠️ 1. 环境与依赖

### 基础环境
- **Python**: 3.9+
- **操作系统**: Linux (推荐) / Windows / macOS

### 必须依赖库 (Python Libraries)
本插件运行需要以下 Python 库，请确保已安装：
- `httpx` (>=0.27.0): 用于高速静态抓取
- `beautifulsoup4`: 解析 HTML 结构
- `playwright`: 驱动浏览器进行动态渲染
- `readability-lxml`: 提取文章正文算法
- `tldextract`: 准确识别域名
- 在Astrbot环境中使用pip install -r requirements.txt一键安装如上依赖库

### 外部环境库 (Playwright Browsers)
为了处理 SPA（单页应用）或强反爬页面，插件需要 Chromium 内核：
```bash
# 在 AstrBot 运行环境中执行
python -m playwright install chromium

📖 补充说明：如何打开 "AstrBot 环境"？
这里的“环境”指的是 AstrBot 程序运行的命令行终端。根据你的部署方式，打开方式不同：

情况 1：如果你使用 Docker 部署 (最常见) 你需要进入 Docker 容器内部才能安装这些依赖。

在服务器终端输入 docker ps，找到 AstrBot 的容器名称（通常叫 astrbot）。

输入以下命令进入容器：

Bash

docker exec -it astrbot /bin/bash
(如果你的容器不叫 astrbot，请替换成实际名字)

看到终端提示符变了（例如变成 root@xxx:/app#），说明你已经进来了！ 在这里执行上面的 install 命令。

情况 2：如果你使用源码/直接部署

打开终端（CMD/Powershell/Bash）。

cd 进入到你存放 AstrBot 的目录。

如果你使用了虚拟环境（venv），请务必先激活它（例如 source venv/bin/activate 或 .\venv\Scripts\activate）。

确保在能运行 python main.py 的那个命令行窗口里执行安装命令。
