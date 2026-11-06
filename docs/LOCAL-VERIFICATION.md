# 交付与验收状态 · 2026-11-06

## 已完成

- 独立公开仓库：https://github.com/Tingtinggogogo/tingjian-file-reader
- 功能分支：feat/file-reader；PR：https://github.com/Tingtinggogogo/tingjian-file-reader/pull/1 ，未合并。
- 已部署应用提交：f9ce9937e681b730cae0474b181a6ca538353862。
- GitHub Actions test-and-build 全部成功：https://github.com/Tingtinggogogo/tingjian-file-reader/actions/runs/42229279880 。包含 14 项回归、PostgreSQL、Linux 真实离线 WAV、依赖审计、Compose 校验、Docker 镜像构建。
- main 分支保护已建立：必须 PR、test-and-build 成功且与最新基线同步；管理员也不能绕过，禁止强推与删除。
- 独立验收网址：https://read.tinsmartfm.com ，HTTPS 证书验证与健康接口正常。
- 服务器部署目录 /home/ubuntu/tingjian-file-reader-staging；Compose 项目 tingjian-reader-staging，独立 PostgreSQL、音频卷与网络；只绑定本机 18081。
- 新增 read 的 DNS A 记录。复用现有 Caddy 作为 HTTPS 入口，新增独立配置文件与路由，原配置已备份并通过校验后平滑加载。
- 使用独立测试会话验证公网登录、PostgreSQL 草稿保存、TXT 提取、图片 OCR、eSpeak NG 中文音频生成与下载，测试音频为 5.2 秒 WAV。该测试会话数据已清除。
- 原有三个业务网站的 HTTPS 请求均返回 200；没有部署或修改 waste-web-production 的业务代码、数据库或容器。
- 本机浏览器曾验证播放/暂停与 390 像素布局无横向溢出。

## 尚待用户验收

- Android/iPhone/鸿蒙与微信/企业微信/支付宝真机选文件、播放和后台行为。
- 实际中文扫描图片识别准确率、复杂 PDF/Office 的阅读顺序和离线音质。
- Edge-TTS 默认关闭，未测试微软在线合成或大陆各运营商链路。
- 50 位使用者的真实负载与磁盘容量。当前只完成小规模功能验收。

访问口令、数据库密码与会话密钥只在服务器 .env.staging 中，不进入 GitHub。用户正在使用的浏览器会话和文件不属于自动测试数据，不能为了清理测试而删除。

## 自然语音更新（2026-11-06）

- 本机护栏通过，17 项回归测试通过，依赖审计无已知漏洞，JavaScript 语法通过。
- 首尔既有容器使用公共短句实测晓晓/晓伊/云希，均成功返回 MP3（分别 26496/27216/28080 字节）；未发送用户上传文件。
- 本机页面成功生成晓伊试听，390px 手机宽度无横向溢出。仍需用户在真实微信/支付宝/鸿蒙等设备试听，声音是否温柔由用户判断。
- 此节更新了上方“Edge 未测试”的旧版本状态；中国大陆各运营商实测仍待完成。
