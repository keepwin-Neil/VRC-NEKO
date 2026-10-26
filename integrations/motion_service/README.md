# 外部动作服务适配层

保留本项目的 HTTP 任务/连续会话、时间线、进程隔离和生成器适配代码；动作基础模型直接使用 [NVIDIA ARDY](https://github.com/nv-tlabs/ardy)。此目录不包含 ARDY 完整源码、修改后的上游文件、权重、NF4 编码服务或本地接入包。

## 兼容性边界

当前 `ardy_generator.py` 和 `optimized.py` 使用本地后端的扩展，例如 Core40/Core8 命名、`text_encoder_device` 参数和 XPU 图重放工具。它们依赖的完整本地扩展没有在本公开版重新分发。**仅安装官方 ARDY 后运行本目录，不保证能够成功加载模型或连续生成。** 后续需要增加经过验证的官方 API 适配、明确版本并补足干净环境验收。

以下是源码提供的开发入口，须先准备兼容的外部后端环境和获授权的本地权重目录：

```bash
python -m integrations.motion_service --help
python -m integrations.motion_service --device cuda --text-device cuda --checkpoints ./checkpoints
```

`checkpoints/` 是本地目录示意，必须自行准备并保持 Git 忽略；本服务不会替你下载。第二条命令说明入口参数，不代表本次完成 CUDA 或官方未修改环境的验证。默认设备仍沿用原开发代码的 XPU；不要据此推定当前机器可用。

服务监听 `127.0.0.1:2346`，无公网认证承诺。`ready`、世界会话绑定、`execution_ready` 及实际世界应用是不同状态；应检查实际回执，不能将健康接口成功作为角色执行成功。

本仓库移除了原来指向未公开接入包的启动包装器和失效安装说明。自动测试中的合成生成器不加载模型，只验证调度、超时、隔离与回执逻辑。原型仍有 README 中列明的路径约束、延迟和稳定性问题。
