# Product Architect Agent

当前版本0.4.1。唯一状态入口：[PROJECT_STATUS](https://github.com/Mindy2000/product-architect-agent/blob/main/docs/PROJECT_STATUS.md)。前四项收尾记录：[AUDIT_CLOSEOUT](https://github.com/Mindy2000/product-architect-agent/blob/main/docs/AUDIT_CLOSEOUT.md)。

中文本地产品架构与应用开发工作台。支持需求方案确认、统一调度与产品规划/开发/审查测试三个专业智能体、受控工具执行、React应用、可选FastAPI/SQLite CRUD、localStorage、文档参考/可选知识库、隔离构建与Playwright检查、有限自动修复、Git版本和源码/数据备份。

## 下载与安装

GitHub仓库提供两条路径：

- **源码**：克隆仓库或下载Source code ZIP，按docs/SETUP.md安装。
- **安装包**：正式 Release 尚未发布；发布后，从GitHub Releases下载macOS `.dmg`或Windows x64 `.exe`。同时提供带SHA256清单的安装ZIP，便于校验和并排升级。

安装包包含应用源码、预构建中文界面和安装启动入口。它是轻量本地安装器：首次使用仍需要Python 3.13、Git和Linux容器环境，并联网安装锁定的Python依赖。它不会把多GB Docker镜像、系统Python、API Key或用户项目塞进软件包。预构建安装包不要求宿主Node；从源码构建界面需要Node.js 24。

macOS使用Colima和Docker CLI；Windows使用Docker Desktop的Linux容器后端（通常为WSL2）。首次镜像准备建议另留8GiB磁盘。模型调用是用户供应商账户的消费，安装和诊断不调用模型。

macOS：打开DMG，将Product Architect.app拖入Applications，打开应用会在终端中引导安装并启动本机工作台；工作目录位于`~/Library/Application Support/ProductArchitect/versions/<版本>`。需要保留启动终端。

Windows：运行EXE，安装到当前用户目录的独立版本文件夹；从开始菜单的Start/Configure/Doctor入口使用。第一次Start会安装固定Python依赖。安装器不自动启用WSL、不替用户重启、不自动充值或连接私人账号。

目前候选包没有Developer ID公证或Windows发布者签名，系统可能要求人工确认来源；不会自动移除系统安全隔离。安装源码及发布者签名可审查，SHA256只校验完整性，不替代发布者身份验证。

## 配置与启动

macOS解压安装ZIP可运行`安装.command`、`配置.command`、`启动.command`、`停止.command`、`诊断.command`；Windows对应`install.cmd`、`config.cmd`、`start.cmd`、`stop.cmd`、`doctor.cmd`。

配置入口支持DeepSeek文字、千问视觉、Dify知识库。Key通过隐藏输入保存在当前版本`.env`，不会出现在诊断结果、命令参数或发行包中。填写Key不会自动开启调用。需要使用文字模型时，由用户在本地`.env`明确设置`MODEL_CALLS_ENABLED=true`，核对费用上限后重启。视觉默认关闭，另有次数/金额限制。Dify为可选插件：先保存Key，再在知识库面板核对并确认免费策略；保存策略和解除暂停都只改本机记录，不自动联网、重置台账或重试。

工作台地址：`http://127.0.0.1:5173/`。无需账号，仅限本机。界面使用预构建静态文件；生成代码始终在受限容器内运行。

## 升级与回退

升级采用并排新目录，旧版本保持完整。先暂停任务并停止旧服务，下载官方Release的带清单安装ZIP：

```sh
# 预览，不改文件
python3.13 scripts/manage.py upgrade /path/to/release.zip --destination /path/to/new-version
# 确认复制代码、配置和数据到独立新目录
python3.13 scripts/manage.py upgrade /path/to/release.zip --destination /path/to/new-version --apply
```

Windows使用`py -3.13`替代`python3.13`。升级副本包含原`.env`、费用台账、SQLite检查点、项目源码与交付；SQLite使用一致性备份复制，不覆盖旧目录，不复制旧`.venv`或依赖目录。然后在新目录运行安装入口。升级和安装均不会发起模型请求。

失败时可停止新版本、从旧目录启动。若新版本已经产生新资料，应先导出数据；不会为了“回退”自动用旧数据库覆盖新数据。`scripts/manage.py rollback-info`显示旧版本定位。不同版本的安装包各自独立；直接运行另一版本的安装器不自动覆盖或搬迁旧资料。

## 公开发布流程

GitHub Actions分别在macOS和Windows构建，包含安装器回归、API启动、前端类型检查/构建、白名单打包、DMG/EXE及SHA256生成。上传与package.json匹配的`v<版本>`标签后，只生成**Release草稿**，需维护者审阅再公开。手动触发只保留Actions产物，不自动发布。

源码ZIP和安装ZIP只包含白名单工程文件；排除.env、用户data/outputs/generated-projects、.git、node_modules、虚拟环境、个人协作记录和本地实验报告。本项目使用[MIT许可证](../LICENSE)，第三方组件保留各自许可，见[第三方声明](../THIRD_PARTY_NOTICES.md)。发布前仍需核对仓库内容与历史。

没有GitHub账号登录功能；使用GitHub下载不意味着模型服务免费。第三方包使用其各自许可证；Windows安装器采用免费的NSIS编译器。

## 验证范围与当前限制

已在macOS验证本地安装和隔离运行链路；GitHub的macOS、Windows构建包含安装升级回归、API启动和界面构建。Windows完整Docker Desktop/WSL2生成任务尚待设备验证。离线固定响应可验证工作流和工具执行，但不代表真实模型对任意需求的自主成功率。

工作台默认关闭模型调用。遇到服务未连接时，请运行启动入口并保持窗口打开；首次使用先配置自己的Key、明确启用文字模型和预算，再重启。恢复源码先生成暂停副本，手动继续才会构建验收。六套独立产品验收文件随源码和安装ZIP一同分发，不需要另外下载。

当前GitHub Actions产物保留14天；正式Release就绪前，不把构建产物链接视为长期下载地址。项目源码备份、业务数据库与附件备份仍可单独使用；整套工作台灾备现已在「设置与维护」提供。

## 设置与维护

左侧「设置与维护」集中提供三项功能：

- **首次使用向导**：检查本机工具与容器状态，填写DeepSeek/千问/Dify各自密钥。文字模型的费用许可须明确勾选，保存后停止并重新启动才能生效；向导不发起供应商验证请求。已有密钥不回显，留空保留。
- **整套工作台备份恢复**：先预览再下载，覆盖项目、方案、SQLite检查点、费用、Git历史、本地知识库、业务数据库与附件。排除.env、依赖、日志与缓存；Dify只备份本地记录，云端知识库不联网导出。先停止活动任务、关闭交互预览，再创建备份。备份包含私人业务资料，请妥善保管。
- **集中脱敏诊断**：先预览，再下载相同快照。只导出系统/依赖版本、配置布尔状态和任务状态计数；不含Key、安装路径、需求、文档、源码或原始日志，不自动上传。

工作台备份限制512MiB、单文件128MiB，恢复支持相同版本及兼容策略明确列出的升级路径，未知版本或降级会拒绝。上传预览24小时有效，可重新校验或移除；诊断预览10分钟有效。恢复会在当前工作台旁的 `<当前工作台目录名>-restored/<UUID>` 创建独立工作台，当前目录保留；新副本密钥为空、模型调用关闭、旧费用保留。停止旧工作台，在新目录运行安装入口，再配置和启动；恢复本身不执行生成代码或调用模型。

## 0.3 持续维护

新增离线完整流程 CI、0.2→0.3 工作台兼容恢复、可取消维护任务、父子版本回归比较和 Dify 离线故障检查。见 [操作与边界](https://github.com/Mindy2000/product-architect-agent/blob/main/docs/OFFLINE_QUALITY_AND_MAINTENANCE.md)。真实模型任务和 Windows 完整 Docker 验证仍暂缓，不代表已完成。

0.3.1 将“执行完成”与需求逐条验收分开；新增固定页面基础检查与源码绑定的人工/独立证据记录。交付报告和ZIP内包含交付时验收快照，最新记录可从项目面板导出。运行资源一次只允许一个构建/浏览器/预览，现有容量保持不变。

0.3.2修复上下文/缺失用量/项目选择竞态，验证缓存与验收绑定运行环境，固定基础检查覆盖已确认静态Hash页面。旧回执缺少环境指纹时显示待重新验证，不自动运行原项目。支持指定旧版本兼容升级，详见[AUDIT_OPTIMIZATION](https://github.com/Mindy2000/product-architect-agent/blob/main/docs/AUDIT_OPTIMIZATION.md)。

0.3.3补齐异步操作选择保护、父子回归页面检查及环境证据，历史报告会明确标注是否仍适用。正式Release与Windows完整运行仍暂缓。

## 0.4 三智能体协作

新开发任务默认由统一调度协调产品规划、开发、审查测试三个专业智能体。每个角色有独立提示、上下文和权限；只有开发角色可修改源码，审查意见按条关联冻结需求。三个角色共享单任务预算、步骤与最多三轮修复限制，仍采用同一个已选文字模型。无需新增API Key。旧任务保持原流程，恢复源码副本仍只执行无模型验证。见 [MULTI_AGENT](https://github.com/Mindy2000/product-architect-agent/blob/main/docs/MULTI_AGENT.md)。

0.4.1完成协作收尾：环境异常时保留安全源码导出，明确停止状态；在原限额内预留审查步骤/费用/调用次数；规划逐条分配责任；问题编号、历轮证据及无变化提前停止。新增规则只用于新任务，旧检查点不追加强制规划或资源限制。没有新增供应商或真实调用授权。
