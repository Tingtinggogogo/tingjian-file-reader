# 中文语音与中国大陆访问

当前页面固定晓晓自然女声 `zh-CN-XiaoxiaoNeural`，舒缓语速 `-15%`。无需音色/语速选择或试听步骤；点击“生成朗读”前展示明确说明，点击后以 `consent=true` 提交文字。后端拒绝客户端自定义引擎/音色/语速。旧录音仍可回听，不会自动转换。

## 微软在线服务

[Edge-TTS](https://github.com/rany2/edge-tts) 使用微软 Edge 在线语音服务，并非离线引擎，也不是有服务等级保障的付费 Azure Speech API。文字从首尔服务器发送给微软；仅打开页面或选择文件不会发送给微软。`ENABLE_EDGE_TTS=true` 为当前默认，设为 false 后拒绝生成，不会静默切换机械音色。

“舒缓”只指语速，不是 Azure 的 gentle 情感风格。Edge-TTS 不提供自定义情感 SSML。首尔到微软和大陆用户到首尔两段链路均可能波动，不能保证所有运营商始终可达。失败时明确提示重试，用户不应提交保密内容。

## 可选离线升级

当前镜像保留 eSpeak NG，固定 cmn 普通话，CPU 生成 WAV，但音色机械；macOS 的兼容测试可使用已安装的 Tingting。旧版离线作业可继续处理，用户页面不再提供离线入口。[eSpeak NG](https://github.com/espeak-ng/espeak-ng) 软件分发需保留许可。

更自然的离线方案可评估 [Piper](https://github.com/OHF-Voice/piper1-gpl) 中文模型，当前未集成。需核查引擎及模型许可、预下载校验模型、评估服务器内存与音质，通过功能分支与独立环境验收再引入。不在用户上传后自动下载模型。

[微软官方音色目录](https://learn.microsoft.com/azure/cognitive-services/speech-service/language-support?tabs=tts)。
