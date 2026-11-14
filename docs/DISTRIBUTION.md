# 安装、配置与发行说明

工程版本 **0.6.0**，当前为本地候选修复，尚未正式发行。本轮验收结果绑定候选文件 SHA256；既有 GitHub 构建或历史报告不能证明当前候选通过。最新能力、真实验证结果和固定收尾清单见 [项目状态](PROJECT_STATUS.md)。

## 环境与安装

这是个人本地中文工作台，无需注册账号。需要 Python 3.13、Git 和 Linux 容器环境。macOS 使用 Colima 与 Docker CLI；从源码构建界面还需 Node.js 24。安装依赖需要联网，建议为首次容器准备另留 8 GiB。模型费用由用户供应商账户承担；安装、配置和诊断不调用模型。

源码与预构建安装 ZIP 共用同一套程序及安装管理入口：

- **源码**：在新目录解压白名单源码 ZIP，运行 `python3.13 scripts/manage.py install`。该入口创建虚拟环境、安装锁定 Python/npm 依赖并构建界面。仓库源码准备说明见 [SETUP](SETUP.md)。
- **macOS 安装 ZIP**：解压到新目录，运行 `安装.command` 或上述安装命令。包内已有界面，无需宿主 Node；仍需安装 Python 依赖及准备容器环境。用 `python3.13 scripts/manage.py install --plan` 可先查看安装步骤。
- 固定容器镜像尚未准备时，可显式加 `--prepare-runtime`；本轮离线验收复用本机已存在的固定镜像，不代表首次镜像下载速度或供应商网络可用性。

仅用于本地验收的候选 ZIP 不是正式 Release，也不是 DMG/EXE。仓库保留 macOS/Windows 原生包与 GitHub Actions 构建配方，但本轮不生成原生安装器、不触发远程构建、不验证 Windows。不能宣称 Windows 完整运行已通过。未签名/公证的候选文件不会自动绕过操作系统安全保护。

## 配置和启动

macOS 入口：`配置.command`、`启动.command`、`停止.command`、`诊断.command`。跨入口的同一套命令为：

```sh
python3.13 scripts/manage.py config init
python3.13 scripts/manage.py config wizard
python3.13 scripts/manage.py serve --no-open
# 另一个终端停止当前目录的服务
python3.13 scripts/manage.py stop
```

工作台位于 `http://127.0.0.1:5173/`，只面向本机；保持启动终端打开。生成代码只在受控容器执行。安装不会自动调用供应商，也不扫描用户私人目录。

本地 `.env` 保存用户自己的 Key，禁止提交或分享。支持 DeepSeek 文字、千问文字及独立视觉通道；千问文字使用 `QWEN_TEXT_API_KEY`，或由用户显式设置 `QWEN_TEXT_USE_VISION_KEY=true` 复用已配置视觉 Key。角色模型在任务确认时冻结。填 Key 不等于允许付费调用：必须核对 `MODEL_CALLS_ENABLED`、`QWEN_TEXT_CALLS_ENABLED`、`VISION_CALLS_ENABLED` 及预算，并重启生效。示例配置默认关闭调用。

Dify 是可选知识库接口，不影响核心开发流程。当前不使用、不验证远程 Dify；用户无需为本轮验收配置它。本地文本/OCR/全文检索仍可使用。平台额度与价格不由本项目保证，不自动充值或切换付费备用服务。

## 升级和回退

先暂停任务并停止旧工作台。使用带 `RELEASE_MANIFEST.json` 和 SHA256 的安装 ZIP，将升级复制到独立新目录：

```sh
# 预览，不改文件
python3.13 scripts/manage.py upgrade /path/to/release.zip --destination /path/to/new-version
# 执行并排复制，不覆盖旧目录
python3.13 scripts/manage.py upgrade /path/to/release.zip --destination /path/to/new-version --apply
```

升级保留旧目录，复制 `.env`、费用记录、SQLite 检查点、项目源码和交付；数据库使用一致性备份，不复制旧虚拟环境和依赖。新目录运行安装入口后再启动。升级不调用模型。

回退先停止新版本，再从旧目录启动；`scripts/manage.py rollback-info` 显示旧版本位置。新版本若已有新资料，先导出再决定，不用旧数据库自动覆盖新数据。

## 备份、恢复和诊断

「设置与维护」提供首次使用向导、整套工作台备份恢复和集中脱敏诊断。备份包含私人业务资料，需妥善保管；不含 `.env`、依赖、日志与缓存。工作台恢复创建旁侧独立副本，密钥为空、调用关闭、历史费用保留；不覆盖当前工作台，不联网导出 Dify 云端内容。升级复制本机配置与工作台备份排除密钥是两种不同操作。

备份最大 512 MiB、单文件 128 MiB；仅允许同版本及明确支持的兼容升级。活动任务和预览须先停止。诊断只导出白名单系统/配置状态，不包含密钥、需求、文档、源码或原始日志，不自动上传。详见 [维护说明](WORKBENCH_MAINTENANCE.md)。

## 验收和分发边界

统一离线验收命令与范围见 [离线质量说明](OFFLINE_QUALITY_AND_MAINTENANCE.md)。固定模型响应能够验证调度、工具、故障恢复及交付，不能代表真实自主开发能力；剩余一次 CRUD 真实复测须另行授权费用。单次通过也不等于任意需求都可靠。

源码/安装 ZIP 按白名单打包，排除真实 `.env`、data、outputs、generated-projects、Git 历史、依赖目录、私人协作记录及本地实验报告；公开说明和六套标准独立验收文件随包分发。`scripts/manage.py verify <zip>` 校验文件与清单。SHA256 证明文件一致性，不证明发布者身份。

正式发行仍暂停。未来 GitHub Actions 可基于同一源码构建原生包；工作流配置不等于构建已执行。匹配版本的标签流程生成草稿，维护者审阅后才公开。当前不上传本地候选文件、不创建标签、不发布草稿。

使用 [MIT 许可证](../LICENSE)，第三方许可见 [第三方声明](../THIRD_PARTY_NOTICES.md)。
