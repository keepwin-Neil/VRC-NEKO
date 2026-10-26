# Unity/Udon 源码

这里只包含经过筛选的 C# 源码及相应 `.cs.meta`。30 个原插件脚本已与提供的 Unity 主工程逐文件核对一致；另加入通用跟拍相机运行时。世界布局、场景专用配置、资产提取工具和所有资源文件都没有导出。

`Assets/NEKO/Npc/` 是 NPC 路由、导航、交互、同步、字幕、语音及可选相机；`ArdyLab/` 是生成动作的重定向和世界约束桥接；`Editor/` 是部分构建/配置辅助工具。

接入时自行创建合法的 VRChat Worlds SDK / UdonSharp 工程，并核对当前官方 SDK 所需 Unity 版本。历史工程使用 Unity 2022.3.22f1。将所需脚本及 `.meta` 放入工程，准备获授权的 Humanoid、动画、字体及导航，再根据协议与 Inspector 字段连接对象。这里没有世界场景、现成 Prefab、UdonProgram `.asset`、NavMesh、材质或字体；需在本机导入、编译或重新生成。

部分 Editor 工具仍按旧角色、字体名称及相对资源路径查找资产；它们是开发辅助源码，不是通用一键安装器。旧的测试场生成入口会修改当前测试场内容，正式世界请逐项检查工具逻辑后接入。对私有地图几何和付费资产依赖较强的 Formal 配置/动画提取工具未包含；因此无法复刻原世界全部配置。

NekoEyeCam / NekoStreamCam 提供相机基础，不代表 AI 视觉已实现。相机运行时的操作者名称应由使用者在本地配置，不能提交个人 Inspector 配置。

世界通信以 `../Docs/Protocols/` 为协议依据。C# 静态契约测试不能替代编辑器编译、Udon 编译、实际两客户端和 VR 验收。本次公开整理没有启动 Unity 构建或 VRChat Local Test。
