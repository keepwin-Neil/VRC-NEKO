# 来源、归属与未包含的资源

本公开快照整理自本地 YUI NPC Controller 0.5.19（本地版本记录 `7df787da4e4776f6804014fd2b6f688c9792b5e6`）与相应 Unity 自定义脚本。发布采用独立初始提交，以避免携带旧资产、凭据和个人信息；这不改变原作者的权利，也不将历史代码归为首次提交者原创。

## 已知来源

| 来源 | 关系与处理 |
|---|---|
| [ggg233m / n.e.k.o_plugin_vrc_body](https://github.com/ggg233m/n.e.k.o_plugin_vrc_body) | VRChat 身体接入历史基础；未打包上游完整仓库 |
| [ggg233m / n.e.k.o_plugin_vrc_npc_controller](https://github.com/ggg233m/n.e.k.o_plugin_vrc_npc_controller) | YUI NPC 插件与协议的重要上游；本地版本包含后续集成和修改，尚无逐文件完整贡献审计 |
| [Project N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O)、[wehos](https://github.com/wehos) | 对话、记忆、语音及 SDK 生态；不包含宿主完整代码或个人角色资料 |
| [NVIDIA ARDY](https://github.com/nv-tlabs/ardy) | 外部动作生成依赖；仅引用官方来源，不包含完整模型实现、上游覆盖补丁、数据或权重 |
| FFmpeg、cloudflared | 可选语音工具；保留 `host/licenses/` 及依赖下载指纹，不包含可执行文件 |
| mido、python-rtmidi、Python、React、Unity、VRChat/UdonSharp | 运行/开发依赖，由使用者通过对应官方渠道获取，适用各自条款 |

`host/licenses/LICENSE` 及同目录文件只对应相应依赖，不能当作整个仓库的统一许可证。ARDY 官方代码与模型权重的许可分开，应以对应上游资源页面为准。

## 权利边界

本地根目录及 2026-10-26 检查到的 NPC 上游根目录没有统一 LICENSE。当前来源与历史授权不足以支持给全部源码重新授予一个统一的开源许可，因此本仓库未添加该类许可。公开代码的查看、平台 Fork 与取得进一步使用授权是不同问题；复用、再发布或商业使用前请确认相关权利。

付费世界模板、第三方角色/衣装/动画/材质/字体/贴图、世界布局、NavMesh、模型文件、权重和训练数据均不在发布范围。源码中保留的旧角色或字体名称、Unity 相对资产路径只是集成约定，不附带资产或授权。没有提供对应资产的下载镜像或绕过购买的方法。
