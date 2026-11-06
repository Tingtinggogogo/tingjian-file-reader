# 听见文件

独立验收网址：[read.tinsmartfm.com](https://read.tinsmartfm.com)。扫码或直接打开，无需邀请码或口令。源码在功能分支，见 [PR #1](https://github.com/Tingtinggogogo/tingjian-file-reader/pull/1)；运行与验收记录见 [交付状态](docs/LOCAL-VERIFICATION.md)。

手机优先的中文文件朗读工具。蓝色 `#0058A3` 与黄色 `#FFDB00`，静态资源随服务器托管，无字体/CDN/Google 前端依赖。目标为约 50 位使用者；不是 50 路并发语音服务。

## 功能与边界

- 上传 EPUB、MOBI、PDF、DOCX、XLSX、PPTX、TXT、MD、CSV、JSON、LOG、PNG、JPG/JPEG、WEBP；自动提取文字。
- 图片使用服务器本地 Tesseract 中文 OCR；扫描 PDF 请先转图片。暂不读取旧版 DOC/XLS/PPT、压缩包、音视频、程序及加密文件；不宣称可读任意格式。
- 单文件 20 MB；提取最多 30,000 字；PDF 最多 100 页；图片最多 1600 万像素；Office/EPUB 解压上限 80 MiB、3000 条目；电子书正文最多 30,000 字，长书请先拆分。
- 页面只有选文件、生成朗读及下方历史。固定 Edge-TTS 晓晓自然女声、舒缓语速；点击生成前有明确微软文字发送说明。API 不接受其他音色/引擎/语速参数。
- 音频排队生成、状态轮询、播放器、音频下载；最多 20 个待处理任务，每会话每小时 10 次，全站 100 次/小时、500 次/日；上传全站 120 次/小时，草稿写入全站 600 次/小时。
- 提取内容在本机保存 + 服务器同步；断网提示。客户端本机存储受设备和浏览器设置影响，原文件本身不保存草稿。
- 匿名签名 HttpOnly Cookie、来源校验、会话数据隔离、上传白名单与子进程超时、容器资源限制。
- 原文件提取完成后删除；历史文字/音频保留 30 天（最长多约 5 分钟），服务器草稿 24 小时，本机草稿至主动清除。历史仅当前浏览器可见，Cookie 有效 180 天并随访问续期；换设备、无痕模式或清除浏览器数据无法找回。支持下载与主动清除。
- 音频达到 5 GiB 或磁盘可用空间低于 1 GiB 时拒绝新上传/合成；全站用量计数独立于用户历史，清除记录或更换浏览器不能绕过。

## 本地启动

本机依赖已安装后，可双击 `本机预览.command`。终端会显示本机地址，无需口令；设置保存在被 Git 忽略的 `data/local-settings.json`。仅监听本机，不作为公网配置。

需要 Python 3.13；当前用户流程使用微软在线语音，需要联网。eSpeak NG/系统婷婷仅保留用于旧版任务兼容与离线升级测试，不出现在用户页面。MOBI 需要 libmobi-tools 提供的 mobitool（Linux 可使用发行版软件包，macOS 可通过 Homebrew 安装 libmobi）；EPUB 使用安全 XML 与 HTML 文本解析。图片需要 Tesseract 的 chi_sim/eng 语言包。Docker 镜像包含这些系统依赖。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export COOKIE_SECURE=false
export ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

直接打开页面即可；不要将密钥、环境文件或用户上传内容提交到 GitHub。默认本地 SQLite；正式与 staging 均使用独立 PostgreSQL。应用仅支持一个进程、一个副本；不可增加 Uvicorn worker 数，否则启动恢复可能导致任务重复执行。

## 检查

```sh
.venv/bin/python scripts/guardrails.py
.venv/bin/python -m pytest -q
.venv/bin/pip-audit -r requirements.txt
node --check app/static/app.js
```

34 项回归覆盖匿名身份续期/防伪、来源校验、草稿/音频隔离、安全提取、大小限制、晓晓固定参数与发送确认、排队、全站额度、存储限制、30 天历史与分页、兼容迁移和旧版离线任务；新增 EPUB 章节顺序/路径/实体/DRM/体积验证及 MOBI 真实解码、压缩、上传和超时清理。CI 必须安装真实 mobitool，不能跳过 MOBI 测试。CI 使用 PostgreSQL，检查本地资源护栏、依赖漏洞和 JavaScript 语法，并构建 Docker 镜像。

## 交付与运维

电子书具体支持范围见 [电子书说明](docs/EBOOKS.md)。

见 [部署说明](docs/DEPLOYMENT.md)、[语音与大陆访问说明](docs/TTS.md)、[验收清单](docs/ACCEPTANCE.md)。代码在 `feat/file-reader` 分支开发，通过 PR 审核；`test-and-build` 成功与 staging 真机验收完成后才可考虑合并，不能从运行中的容器提取源码。CI 不含服务器密钥、生产部署或自动合并。新建公开仓库的初始 `main` 只放仓库说明；业务代码通过功能分支 PR 引入。

兼容性目标：Android/iPhone/鸿蒙、平板与桌面浏览器，微信/企业微信/支付宝内置浏览器；尚需真机验收，不能仅凭响应式布局宣称全部通过。音频播放必须由用户点击，锁屏与后台播放由设备限制决定。二维码应编码最终 HTTPS 地址，可使用微信等现有分享功能扫码。
