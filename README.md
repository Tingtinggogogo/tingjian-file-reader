# 听见文件

独立验收网址：[read.tinsmartfm.com](https://read.tinsmartfm.com)。访问需管理员提供口令。源码在功能分支，见 [PR #1](https://github.com/Tingtinggogogo/tingjian-file-reader/pull/1)；运行与验收记录见 [交付状态](docs/LOCAL-VERIFICATION.md)。

手机优先的中文文件朗读工具。蓝色 `#0058A3` 与黄色 `#FFDB00`，静态资源随服务器托管，无字体/CDN/Google 前端依赖。目标为约 50 位受邀使用者；不是 50 路并发语音服务。

## 功能与边界

- 上传 PDF、DOCX、XLSX、PPTX、TXT、MD、CSV、JSON、LOG、PNG、JPG/JPEG、WEBP；提取可编辑文字。
- 图片使用服务器本地 Tesseract 中文 OCR；扫描 PDF 请先转图片。暂不读取旧版 DOC/XLS/PPT、压缩包、音视频、程序及加密文件；不宣称可读任意格式。
- 单文件 20 MB；提取最多 30,000 字；PDF 最多 100 页；图片最多 1600 万像素；Office 解压上限 80 MiB、3000 条目。
- 默认 eSpeak NG 中文离线语音（固定音色，较机械）；可选 Edge-TTS 在线晓晓/云希，需要服务端启用且用户确认发送文字。
- 音频排队生成、状态轮询、播放器、音频下载；最多 20 个待处理任务，每会话每小时 10 次。
- 本机即时草稿 + 服务器防抖同步；断网提示。客户端本机存储受设备和浏览器设置影响，原文件本身不保存草稿。
- 访问口令、签名 HttpOnly Cookie、来源校验、会话数据隔离、上传白名单与子进程超时、容器资源限制。
- 原文件提取完成后删除；服务器文字/音频约 24 小时后清理（最长约 24 小时 5 分钟）；页面可主动清除。服务器备份另按管理员保留策略处理。退出后本机草稿清除、服务器记录继续按过期策略清理。

## 本地启动

本机依赖已安装后，可双击 `本机预览.command`。终端会显示本机地址和随机访问口令；设置保存在被 Git 忽略的 `data/local-settings.json`。仅监听本机，不作为公网配置。

需要 Python 3.13；Linux 音频生成需要安装 eSpeak NG；macOS 本机预览可使用已安装的系统婷婷音色（不会自动下载语音包）。图片需要 Tesseract 的 chi_sim/eng 语言包。Docker 镜像包含这些系统依赖。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export ACCESS_CODE="$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
export COOKIE_SECURE=false
export ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

在本机终端查看 `$ACCESS_CODE` 后输入页面；不要将口令、环境文件或用户上传内容提交到 GitHub。默认本地 SQLite；正式与 staging 均使用独立 PostgreSQL。应用仅支持一个进程、一个副本；不可增加 Uvicorn worker 数，否则启动恢复可能导致任务重复执行。

## 检查

```sh
.venv/bin/python scripts/guardrails.py
.venv/bin/python -m pytest -q
.venv/bin/pip-audit -r requirements.txt
node --check app/static/app.js
```

14 项回归测试覆盖认证、限速、来源校验、草稿隔离、编码、上传、大小、Office 提取、压缩炸弹、语音授权、队列配额、音频隔离、过期与重启、语音工作线程。在有 eSpeak NG 的 CI 中最后一项生成真实中文 WAV；macOS 使用系统中文语音实测；其他本机缺失引擎时验证失败状态，不把模拟结果当作真实语音成功。CI 另外使用 PostgreSQL、审计依赖并构建正式 Docker 镜像。

## 交付与运维

见 [部署说明](docs/DEPLOYMENT.md)、[语音与大陆访问说明](docs/TTS.md)、[验收清单](docs/ACCEPTANCE.md)。代码在 `feat/file-reader` 分支开发，通过 PR 审核；`test-and-build` 成功与 staging 真机验收完成后才可考虑合并，不能从运行中的容器提取源码。CI 不含服务器密钥、生产部署或自动合并。新建公开仓库的初始 `main` 只放仓库说明；业务代码通过功能分支 PR 引入。

兼容性目标：Android/iPhone/鸿蒙、平板与桌面浏览器，微信/企业微信/支付宝内置浏览器；尚需真机验收，不能仅凭响应式布局宣称全部通过。音频播放必须由用户点击，锁屏与后台播放由设备限制决定。二维码应编码最终 HTTPS 地址，可使用微信等现有分享功能扫码。
