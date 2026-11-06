# 本地交付状态 · 2026-11-06

- 源码保存在本机功能分支 feat/file-reader；按用户最新要求不推送 GitHub。
- Python 3.13 本机依赖已安装；依赖完整版本记录见 requirements-lock-dev.txt。
- 14 项回归检查通过；本机采用 macOS 婷婷音色真实生成并验证 WAV。
- 项目护栏审计、JavaScript 语法检查、Python 编译检查通过。
- 升级依赖后 pip-audit 未报告已知漏洞；此结论只对应检查当时的漏洞库。
- 浏览器实测登录、TXT 文件选择上传与提取、中文草稿保存、刷新恢复、排队生成音频、原生播放/暂停控件。
- 390 像素手机宽度下 document.scrollWidth 与 viewport 均为 390，无横向溢出；非手机真机结论。
- 示例文件生成约 19.4 秒、22050 Hz、单声道 WAV，浏览器已进入实际播放状态。

## 尚未执行

- 本机没有 Docker，未运行本机 Docker build/Compose，也未启动 PostgreSQL；本机测试数据库为 SQLite。
- Linux/eSpeak NG 和 Tesseract OCR 的真实运行、PostgreSQL 测试由预备 CI 和服务器 staging 验收完成。CI 配置已写好但没有运行，不标记为成功。
- 未创建 GitHub 仓库/PR，未推送分支、未启用远端分支保护、未运行 GitHub Actions。
- 已于 2026-11-06 通过腾讯云微信快捷登录和 OrcaTerm 连接服务器，仅执行只读检查。登录用户 ubuntu，SSH 端口 22，Docker 29.6.1，Compose v5.3.1。当前没有本机 SSH 密钥配置。
- 服务器已有生产应用与 PostgreSQL；外部 HTTPS 入口为 Caddy。未改动容器、数据库、证书、域名和入口配置。尚未部署本项目；用户要求暂不推送 GitHub。
- 没有 Android/iPhone/鸿蒙及微信/企业微信/支付宝真机验收。
- Edge-TTS 默认关闭，未发送用户文件给微软，未实测大陆链路与在线合成。
