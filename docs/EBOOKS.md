# EPUB 与 MOBI 上传

页面仍是选择文件 → 生成朗读，固定晓晓女声。原文件只在首尔服务器本地解析，点击生成后才把提取出的文字发送至微软。扩展名支持大小写，上传控件从后端支持列表取得 `.epub` / `.mobi`。

## 范围与限制

- EPUB：根据 META-INF/container.xml 查找 OPF，再按 spine 的正文阅读顺序提取 XHTML/HTML。跳过 linear=no 附属条目、HTML 标题区、导航、脚本、样式和隐藏内容；保留正文标题、段落与中文标点。不按 ZIP 条目顺序或文件名排序。
- MOBI：校验 BOOKMOBI 数据库记录后，通过 libmobi 的 mobitool -e 在本地转换为临时 EPUB，再使用同一提取器。支持库可解码的无加密 MOBI（含 PalmDOC/HUFF-CDIC 压缩及兼容 KF8 内容），不是对所有 Kindle 格式的承诺；AZW/KFX/PDF Print Replica 不在本次支持范围。
- 每本最多 80 MB、30 万字，EPUB 解压目录最多 3000 个条目、256 MiB 声明内容。每个实际读取的成员最多 8 MiB。解析时从磁盘读入元数据和章节。
- 超过 5000 字时，一次点击提交整本。首段约 100 字，后续优先在标点处分割，每部分最多 5000 字。服务器保存顺序，各部分完成后即可播放、下载，关闭页面后继续。每个浏览器同时处理一本长书，全站最多三本。全站语音额度按实际部分数在提交时预留。
- 删除一部分会继续生成其余部分。“删除整本朗读”会停止并清除这次提交的所有部分。上传草稿单独保存，可重新生成。
- 拒绝加密/DRM 保护正文，不提供去 DRM 功能。仅字体被混淆且正文可读的 EPUB 可提取。纯图片电子书无正文时提示转换为图片识字。
- 损坏、缺失章节、无正文、超长、超时等情况显示中文提示，不执行或渲染上传内容，不下载书内 URL。

## 安全与运行

EPUB 不解压到文件系统；限制体积/条目/重复名称，章节引用只允许容器内部路径，XML 外部实体由 defusedxml 禁止。HTML 只作文字解析，不执行脚本，也不加载外部资源。

MOBI 使用固定文件名 book.mobi 与 book.epub，在上传专属临时目录运行。解析器进程限制墙钟 45 秒、CPU 40 秒、Linux 内存地址空间 256 MiB、输出单文件 128 MiB。失败与超时清除该目录；原有外层解析时限 60 秒和 Docker 资源限制继续生效。没有解密参数，也不记录文档正文到日志。

Docker 和 GitHub Actions 通过发行版仓库安装 libmobi-tools；本机没有 mobitool 时 MOBI 显示组件缺失，不影响 EPUB。CI 缺少真实解析器时测试直接失败。libmobi 的 LGPL 许可随发行版包保留，分发镜像应遵守相应许可；应用没有复制它的源代码。

参考：[W3C EPUB 规范](https://www.w3.org/TR/epub-33/)、[libmobi 上游](https://github.com/bfabiszewski/libmobi)、[mobitool 用法](https://manpages.debian.org/unstable/libmobi-tools/mobitool.1.en.html)。
