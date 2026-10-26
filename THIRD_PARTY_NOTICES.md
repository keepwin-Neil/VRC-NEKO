# 来源、合作归属与未包含的资源

本公开快照整理自本地 YUI NPC Controller 0.5.19（本地版本记录 `7df787da4e4776f6804014fd2b6f688c9792b5e6`）与相应 Unity 自定义脚本。发布采用独立初始提交，以避免携带旧资产、凭据和个人信息；这不改变原作者的权利，也不将历史代码归为首次提交者原创。

项目关系以维护者确认的合作背景为准：**N.E.K.O 官方是上游基础项目，keepwin-Neil 与 ggg233m 是合作开发者，NVIDIA ARDY 是外部动作模型依赖。** 代码出现在某位合作开发者的仓库中，或两个仓库有相同文件，不足以将合作开发关系认定为单向上下游关系。

## 已知来源

| 来源 | 关系与处理 |
|---|---|
| [ggg233m / n.e.k.o_plugin_vrc_body](https://github.com/ggg233m/n.e.k.o_plugin_vrc_body) | 合作开发者的相关 VRChat 身体接入工作；未打包该完整仓库 |
| [ggg233m / n.e.k.o_plugin_vrc_npc_controller](https://github.com/ggg233m/n.e.k.o_plugin_vrc_npc_controller) | 合作开发的 YUI NPC 插件与协议相关仓库；本地版本包含合作成果及后续整合，尚未完成逐文件贡献整理 |
| [Project N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O)、[wehos](https://github.com/wehos) | N.E.K.O 官方为上游基础项目，提供对话、记忆、语音及 SDK 生态；感谢 wehos 的相关贡献，不包含宿主完整代码或个人角色资料 |
| [NVIDIA ARDY](https://github.com/nv-tlabs/ardy) | 外部动作生成依赖；仅引用官方来源，不包含完整模型实现、上游覆盖补丁、数据或权重 |
| FFmpeg、cloudflared | 可选语音工具；保留 `host/licenses/` 及依赖下载指纹，不包含可执行文件 |
| mido、python-rtmidi、Python、React、Unity、VRChat/UdonSharp | 运行/开发依赖，由使用者通过对应官方渠道获取，适用各自条款 |

`host/licenses/LICENSE` 及同目录文件只对应相应依赖，不能当作整个仓库的统一许可证。ARDY 官方代码与模型权重的许可分开，应以对应上游资源页面为准。

## 权利边界

N.E.K.O 官方代码按其适用许可证处理，不以合作开发者仓库中的许可情况替代官方条款。合作开发成果的公开范围、贡献署名、再分发条件和统一许可证安排，需要结合合作记录与相关贡献者的确认；本次关系说明不代表已经取得任何新的授权。

本地根目录及已检查的合作开发相关 NPC 仓库根目录未见覆盖全部内容的统一 LICENSE，因此本仓库未将全部源码统一标为 MIT/Apache。公开代码的查看、平台 Fork 与取得进一步使用授权是不同问题；复用、再发布或商业使用前请确认相关权利。

付费世界模板、第三方角色/衣装/动画/材质/字体/贴图、世界布局、NavMesh、模型文件、权重和训练数据均不在发布范围。源码中保留的旧角色或字体名称、Unity 相对资产路径只是集成约定，不附带资产或授权。没有提供对应资产的下载镜像或绕过购买的方法。
