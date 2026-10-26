# 项目说明、外部依赖与未包含的资源

本公开快照整理自本地 YUI NPC Controller 0.5.19（本地版本记录 `7df787da4e4776f6804014fd2b6f688c9792b5e6`）与相应 Unity 自定义脚本。发布采用独立初始提交，以避免携带旧资产、凭据和个人信息；这不改变原作者的权利，也不将历史代码归为首次提交者原创。

本项目是 **Project N.E.K.O 的官方附属项目**，由 [keepwin-Neil](https://github.com/keepwin-Neil) 的个人 GitHub 账号发布，目前由其持续维护。相关致谢见 [English README](README.md#acknowledgements) 和 [中文说明](README.zh-CN.md#致谢)。

## 相关项目与依赖

| 来源 | 关系与处理 |
|---|---|
| [ggg233m / YUI NPC Controller](https://github.com/ggg233m/n.e.k.o_plugin_vrc_npc_controller) | 0.5.14 及以前的合作开发版本，由双方共同完成并在该仓库发布；0.5.15 起主要由本仓库维护者升级和维护 |
| [Project N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) | 宿主及插件 SDK 依赖；不包含宿主完整代码或个人角色资料 |
| [NVIDIA ARDY](https://github.com/nv-tlabs/ardy) | 外部动作生成依赖；仅引用官方来源，不包含完整模型实现、上游覆盖补丁、数据或权重 |
| FFmpeg、cloudflared | 可选语音工具；保留 `host/licenses/` 及依赖下载指纹，不包含可执行文件 |
| mido、python-rtmidi、Python、React、Unity、VRChat/UdonSharp | 运行/开发依赖，由使用者通过对应官方渠道获取，适用各自条款 |

`host/licenses/LICENSE` 及同目录文件只对应相应依赖，不能当作整个仓库的统一许可证。ARDY 官方代码与模型权重的许可分开，应以对应上游资源页面为准。

## 权利边界

项目的版权署名、公开范围与许可证遵循 N.E.K.O 官方的发布安排。N.E.K.O 官方代码和外部依赖分别按其适用许可证处理，已有版权、许可及 NOTICE 声明继续保留。

本仓库目前未为全部源码统一标注 MIT/Apache 许可。具体使用、修改、再分发和商用条件，以适用的许可证与官方发布说明为准；官方附属项目身份和个人账号维护本身不改变第三方材料的许可条款。

付费世界模板、第三方角色/衣装/动画/材质/字体/贴图、世界布局、NavMesh、模型文件、权重和训练数据均不在发布范围。源码中保留的旧角色或字体名称、Unity 相对资产路径只是集成约定，不附带资产或授权。没有提供对应资产的下载镜像或绕过购买的方法。
