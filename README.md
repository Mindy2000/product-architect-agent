# Product Architect Agent

中文个人本地应用开发工作台：确认需求 → 规划/开发/审查三个专业智能体 → 隔离构建与交互测试 → 有限修复 → 交付与版本管理。

当前工程版本 0.6.0，业务实现冻结于本地候选 5，尚未正式发行。支持 DeepSeek 与千问按角色配置；需要用户自己的 API Key 和明确的调用预算。默认不调用模型，无需登录账号。

- [当前能力、证据和固定收尾范围](docs/PROJECT_STATUS.md)
- [安装、配置、升级与发行说明](docs/DISTRIBUTION.md)
- [源码环境准备](docs/SETUP.md)
- [统一离线验收](docs/OFFLINE_QUALITY_AND_MAINTENANCE.md)
- [备份恢复与诊断](docs/WORKBENCH_MAINTENANCE.md)

现有离线记录为 400 项 Python、13 项前端检查和 12 组按影响范围复核的离线流程通过。最近一次真实 CRUD 任务的独立功能检查 4/4 通过，但千问最终审查遇到连接错误，完整自主闭环未通过。固定响应验收不代表真实模型自主成功率。Dify 为可选接口，当前不使用；Windows 完整运行不在本轮范围。正式发行暂缓。当前停止新增验证，GitHub 工作流已暂停；同步源码不会自动启动测试、Windows 构建或发布。

[MIT 许可证](LICENSE) · [第三方声明](THIRD_PARTY_NOTICES.md)
