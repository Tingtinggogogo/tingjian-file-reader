# 腾讯云首尔部署

目标 IP 与域名属于运维配置，不写入程序。先核实 SSH 登录、发行版、CPU/内存/磁盘、现有容器/端口/Nginx/证书与当前站点用途。禁止覆盖既有网站或修改其他数据库。

## 已核实的服务器现状（2026-11-06）

通过腾讯云控制台终端进行只读检查：Ubuntu 24.04，2 核/4 GB/60 GB，当前根磁盘可用约 39 GB，内存可用约 2 GB；Docker 29.6.1、Compose v5.3.1 已安装。用户 ubuntu，SSH 端口 22。已有多个生产应用和 PostgreSQL 容器，外部 80/443 由 Caddy 容器负责。

本项目的 Nginx 配置仅用于内部应用入口，不替换现有 Caddy。正式接入需先核对可用子域名/DNS/路由及现有发布机制。随后用户授权完成推送与部署，已从独立仓库的 CI 成功提交构建独立验收环境；详见下方实际部署记录。未读取已有业务环境密钥。

## 源码与分支

唯一源码为 GitHub 仓库。新仓库先在 main 建立纯说明基线，再从该提交创建 feat/file-reader。所有业务改动通过 PR，不直接提交 main、不自动合并。依赖审计、58 项回归、PostgreSQL 测试和 Docker build 在 test-and-build 中执行。仓库管理员需在 GitHub 分支保护中设置 main 必须 PR 且要求 test-and-build 成功；仅有 workflow 文件不代表保护已经启用。

## 环境准备

服务器需要 Docker Engine、Compose v2、Git 与现有 HTTPS 入口。系统版本和已有 Docker 未确认前，不运行远程安装脚本。若缺依赖，按发行版官方说明安装，不重置已有 Docker 或防火墙。推荐至少 2 核/2 GB 内存及足够音频磁盘，实际容量以压测为准。

仅从 GitHub clone 独立目录，例如 `/opt/tingjian-staging`，检出 PR 的确切提交。不能从本机工作目录直接打包生产、不能从正在运行的容器反向复制源文件。

## staging：独立数据库与卷

在服务器目录复制 .env.example 为 .env.staging，权限设为 600。分别生成随机十六进制数据库密码和会话密钥。切勿提交环境文件。

```sh
python3 -c 'import secrets; print(secrets.token_hex(24))'
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

在 .env.staging 中设置 `APP_PORT=18081`、独立 `SESSION_SECRET`/`POSTGRES_PASSWORD`。`ALLOWED_ORIGINS` 为实际访问来源，不包含末尾斜杠。首选带证书的 staging 子域名；DNS/证书需另行核实。也可通过本机 SSH 隧道访问 HTTP 的 `http://127.0.0.1:18081`，此时仅 staging 设置 `COOKIE_SECURE=false`。此模式不可供公网手机验收，不要开放防火墙来替代 HTTPS。

```sh
docker compose --env-file .env.staging --project-name tingjian-staging config --quiet
docker compose --env-file .env.staging --project-name tingjian-staging up -d --build
docker compose --env-file .env.staging --project-name tingjian-staging ps
curl --fail http://127.0.0.1:18081/api/health
```

项目名使 PostgreSQL 与音频卷隔离；严禁给 staging 配置生产数据库 URL 或生产卷。数据库端口不对外发布，应用入口仅绑定服务器回环地址，HTTPS 由现有入口终结。上游已限制请求大小，上传路径配置 `client_max_body_size 81m` 以容纳表单边界，应用分别校验 80/20 MiB。Nginx 限制同时两次上传，应用同一时间最多解析一本电子书。

## HTTPS 与生产切换

目前未授权覆盖既有域名服务：检查现有域名用途后提供具体切换方案，等待用户批准。`deploy/host-location.conf.example` 仅为域名根路径归属本工具时的片段，绝不能整文件覆盖现有 Nginx 或证书。

staging 验收完成、CI 成功且用户明确允许合并后，才可由仓库流程合并。生产只能从 GitHub 生产分支的批准提交部署，独立目录/项目名 `tingjian-production`/端口 18080/数据库与密钥，`COOKIE_SECURE=true`，`ALLOWED_ORIGINS=https://实际域名`。已有自动监听机制需要先识别，不能擅自另建部署方式。没有用户批准不接入生产监听、不部署 main、不切换生产入口。

Nginx 修改前备份，`nginx -t` 通过后才 reload；不要重启其他业务容器。

## 限制与运行检查

- 单个 Uvicorn 进程、两个后台语音线程；磁盘占用与内存要监控，声音最长 3 分钟合成超时，解析 60 秒超时。
- 无口令公开访问，以签名 Cookie 隔离浏览器历史；不是实名账户系统。Cookie 有效 180 天并续期，录音保存 30 天，清除 Cookie/换设备无法找回。
- Nginx 提供入口限制；不盲信任 X-Forwarded-For。数据库全站计数限制上传 120 次/小时、朗读 100 次/小时及 500 次/日，清除会话或记录不能绕过；每会话 10 次/小时，队列 20 个；草稿写入全站 600 次/小时。
- 原文件不长期保存；异常中断遗留的上传临时目录会在下次运行清理周期清除（至少一小时前的目录），维护时不能删除活跃任务。
- 手工备份 PostgreSQL 时注意包含用户文字，权限与保留时长应单独制定。自动清理不影响已导出的备份。
- 回滚应检出 GitHub 上一个批准提交重建应用，保留数据库卷；禁止 `down -v`。当前为兼容性增量迁移，新增列和用量表不影响旧版回滚；无需删除列或数据。
- 构建需要 Python 镜像、PyPI、Debian 包源。首尔通常可获取，但须实测；不要添加未核实的镜像源或关闭 TLS 验证。

## 实际独立验收环境（2026-11-06）

地址 https://read.tinsmartfm.com ，部署目录 `/home/ubuntu/tingjian-file-reader-staging`，Compose 项目 `tingjian-reader-staging`。配置 `.env.staging` 权限 600，包含独立随机密钥；口令不进入源码。使用 GitHub 提交 `f9ce9937e681b730cae0474b181a6ca538353862`，PR 尚未合并。

应用、数据库、Nginx 均属于新项目。现有 Caddy 入口仅新增 `/home/ubuntu/waste-proxy/tingjian-reader.caddy` 并在原 Caddyfile 末尾导入；备份命名为 `Caddyfile.before-tingjian-时间戳`。这个目录名称属于共享 HTTPS 网关，不代表把程序部署到 waste-web-production。网关容器已连接 `tingjian-reader-staging_egress` 网络。

Caddy 原有业务路由保留。以后如果重建共享网关容器，应在其部署配置中保留新网络连接，或由管理员重新连接；如果销毁本工具网络再创建，也需重连。仅重启现有容器通常不会丢失网络连接。不要执行清空所有 Docker 资源的命令。

日常查看：

```sh
cd /home/ubuntu/tingjian-file-reader-staging
sudo docker compose --env-file .env.staging --project-name tingjian-reader-staging ps
curl --fail https://read.tinsmartfm.com/api/health
```

升级时先从 GitHub 拉取经过 CI 的确切提交，在此独立环境验收。保留数据库和音频卷。当前没有自动部署主分支的监听，不会因其他业务仓库发布而更新本工具。

## 自然语音更新

仅在独立 reader staging 中设置 `ENABLE_EDGE_TTS=true`，重建 app 容器；数据库、音频卷及共享入口保持原样。先确认无 queued/processing 作业，再从 GitHub 获取通过 CI 的功能分支提交。不要修改 waste-web-production。启动时只给 jobs 增加 rate/kind 列，旧作业默认 normal/reading，保留草稿和录音。回滚到旧代码时无需删列，可恢复原配置关闭在线语音。

上线前从首尔用固定公共短句实测晓晓音色；成功不代表中国大陆所有网络均可访问。不得用现有用户文件测试外部服务。

## 无口令精简页面更新

用户明确授权公开扫码使用。删除应用 ACCESS_CODE 要求和登录接口；已有 .env 中废弃的 ACCESS_CODE 可以保留但不再读取，不修改 SESSION_SECRET，以便保留已有浏览器历史。新浏览器通过同源 POST /api/session 自动取得隔离身份。无需修改公网端口、共享网关或 DNS。

启动新增 jobs.title（旧值为“文件朗读”）及 usage_limits 表。用量计数只存时间桶/次数，不存 IP 或文件内容，保留约两天；历史文字和音频保留 30 天，草稿 24 小时。音频达到 5 GiB 或磁盘不足 1 GiB 时拒绝新增任务；单个运行中任务可能越过容量阈值少量空间。

获取 CI 成功的确切 GitHub 提交，保留所有卷，重建 app；对本项目 web 执行 nginx -t 后 nginx -s reload，移除旧登录限流配置并刷新应用地址解析。其他业务容器不改动。回滚旧提交重建时可继续使用旧环境中口令；数据库新增字段保留。

## EPUB / MOBI 更新

镜像新增发行版 `libmobi-tools` 软件包，Python 显式依赖 defusedxml。CI 也安装 mobitool 并进行真实 MOBI 解码测试。部署前镜像必须构建成功；只更新独立 reader app，无数据库迁移，无需修改其他服务或公网路由。

MOBI 子进程限制 45 秒墙钟时间、40 秒 CPU、256 MiB 地址空间（Linux）和 128 MiB 单输出文件；不能传入解密密钥。临时转换目录位于该次上传目录，成功/失败均清理。EPUB 不释放 ZIP 文件到磁盘，只按 spine 阅读安全路径里的正文，不加载外部资源。电子书支持 80 MB / 30 万字，普通文件 20 MB / 3 万字。详见 EBOOKS.md。

## 整本生成与取消

本版向 jobs 增加整本编号、部号、总数和排队时间，向 drafts 增加文件名，以 speech_usage 独立记录近一小时提交用量。启动时兼容已有录音，恢复中断任务。每个任务使用自己的临时目录，最终音频完成后原子发布。删除时先撤销数据库记录，再停止子进程组并清理部分音频。启动时清理意外中断遗留的未发布文件。

从通过 CI 的 GitHub 提交构建独立 app。更新 app 和本项目 web，保留数据库及音频卷。web 单文件挂载的 nginx 配置有变动时需重建 web 容器以重新挂载文件。上传缓冲使用数据卷中的 spool 目录。部署后验证 Nginx 配置、80 MB 上传入口、实际 FFmpeg 合并、单条/整本删除和健康状态。旧版本回滚前应完成或取消新版本的整本任务，旧程序无法继续 scheduled 部分。

## 按章朗读

本次增加 reading_outlines 表并向 jobs 增加 chapter_title、budget_due 两列，旧任务使用默认值。目录属于当前匿名会话，按文件名与正文摘要匹配，随草稿续期并定期清理。保留原 PostgreSQL 及音频卷，仅重建本工具 app。已合成的旧录音继续使用原格式。回滚前应完成或取消按新规则等待的章节任务。
