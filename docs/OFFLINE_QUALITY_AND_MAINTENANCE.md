> 历史验证与实现说明：当前状态以 [PROJECT_STATUS.md](PROJECT_STATUS.md) 为准。新增验证已暂停，下列命令仅保留为操作参考；本次上传准备没有执行它们。

# 0.3 离线质量与持续维护

本轮五项范围：完整离线流程 CI、跨版本工作台恢复、可取消维护任务、父子版本影响与回归、Dify 故障测试。真实模型任务和 Windows 完整 Docker 链路继续推迟。无需新增供应商或充值。

## 1. 可复现完整流程

`python scripts/offline_quality.py` 在白名单发行源码的临时副本运行；先构建工作台前端，并安装本地 Python Playwright 浏览器。8000/5173 已占用时拒绝启动，避免影响当前工作台。Linux CI 显式设置 `PA_DOCKER_CONTEXT=default`，提前构建固定前端和全栈镜像。

使用固定模型响应，覆盖需求编辑确认、计划、暂停、重新打开 SQLite checkpoint、构建故意失败、一次修复、Playwright、预览、源码/Trace/Git diff 交付；另覆盖全栈 CRUD、附件、业务备份、本地知识库、整套工作台恢复及恢复后的 checkpoint。生成代码只在禁网固定容器执行，不运行生成项目自带的安装脚本。CI 没有供应商凭证，不验证真实模型成功率。

`.github/workflows/offline-quality.yml` 原先随 main 更新或 PR 运行，当前已移除自动触发，并将执行 job 暂停；原生包和草稿发布工作流也暂停。只保留手动/复用入口与配方，未恢复前不会执行 job。过去的证据产物名为 `offline-quality-evidence`、保留 14 天；以后明确恢复时仍可使用该路径。不能把旧 CI 结果当作新同步提交已通过。

### 当前统一入口（0.6.0 候选）

准备锁定依赖、Playwright 浏览器和固定容器镜像后，先停止当前工作台，在工程根目录运行：

```sh
python -m unittest discover -s tests -v
npm run test:frontend
npm run format:check
npm run typecheck
npm run build
python scripts/offline_quality.py --output outputs/offline-quality-new
```

`python` 指已安装锁定依赖的虚拟环境解释器。默认使用 Playwright 安装的 Chromium；本机已安装 Chrome 时可显式设置 `PA_UI_BROWSER_CHANNEL=chrome` 复用它（CI 仍使用固定 Playwright 浏览器）。输出目录必须新建或为空，避免旧通过证据混入。`--list` 只列清单；`--scenario <名称>` 仅复核受影响场景，不等于完整验收。

默认固定 12 组：ui_workflow、checkpoint_restart、fullstack_roundtrip、workbench_roundtrip、regression_roundtrip、audit_quality、team_checkpoint、team_scenarios、interactive_expansion、interface_feedback、review_submission、review_submission_stop。后三组分别验证交互失败反馈与修复、末步错误后纠正、连续错误在限额内停止。持续错误按预期停止属于该故障测试通过，不是生成任务成功。

每组保存日志及 JSON/截图，总报告包含应执行清单，只有全部完成才标记通过。超时、非零退出或缺失报告均阻止通过。默认关闭 DeepSeek、千问文字和视觉许可，移除继承凭证；在不含用户资料的新源码副本运行固定响应。既有本地 Dify 保护单元测试使用模拟传输，不请求远程 Dify，也不将 Dify 远程问题纳入本轮验收。

冻结验收应先构建界面、生成源码与 macOS 安装 ZIP、记录 SHA256，再从解压的候选目录执行上述检查。只在合成资料副本测试安装/启动/停止/并排升级，保留原工作台和费用。任何源码变化会形成新候选摘要；原失败证据不覆盖。正式发行、Windows 和真实模型复测不由该命令触发。

## 2. 跨版本恢复

预览返回来源/目标版本、兼容性、迁移步骤与文件摘要。支持同版本，以及已登记的 **0.2.0 → 0.3.0**：只为文档 jobs 表增加 cancel_requested 字段，保持原记录与列。未知版本、降级或破坏性迁移拒绝，需另行决定。

恢复仍创建同级独立目录。迁移前原始 ZIP 保存在新副本 `data/workbench/migration-original.zip`，并记录回执；失败或提交前取消删除本次临时目录，不覆盖旧安装。命令行并排升级共用相同版本策略；旧知识库数据库迁移前另存为新目录的 `data/upgrade-pre-migration/library.sqlite3`。本机并排升级沿用原有私下复制配置的行为（包含本机密钥，不上传）；工作台 ZIP 恢复仍清空密钥。回退使用旧目录及原数据，不把新版数据库写回旧版。新副本不带 Key，模型调用关闭，费用记录保留。

## 3. 进度与取消

工作台备份、恢复、父子版本回归在持久化本地队列执行（并发 1、排队最多 4）。显示当前阶段、该阶段实际处理量和已用时间，不估算剩余时间。维护任务可查看、取消、手动重试；服务重启将旧任务标为中断，绝不自动继续。控制数据库位于 `data/workbench/jobs/tasks.sqlite3`，不随备份复制。

备份和恢复期间阻止其他工作台请求，进度/取消仍可用。提交独立目录后不能取消已完成的恢复。最多保留约 1 GiB 的任务备份后提示移除旧产物；移除只针对选中的维护 ZIP，不删除项目。诊断仍为显式预览后下载。

文档索引可取消，本地解析进程组停止，原上传文件保留。重建取消时旧片段仍可检索；无旧片段时可手动重试。OCR 的并发 1、768 MiB 进程组 RSS 采样保护、单页/整份时限保持原约束；这不是硬内存隔离。

## 4. 持续维护

项目记录支持人工页面路由→源码映射。影响分析沿相对 import、动态 import、export 反向闭包推导，再结合人工验收关联形成候选。未解析导入明确列出；不声称理解完整运行时路由或行为。

父子版本分别复制到独立临时目录，使用固定构建和 Playwright 命令。只比较同身份测试：基线通过当前失败为新增回归；两边失败为原有问题；仅当前失败为基线未覆盖；基线失败当前通过才为恢复通过。删除/跳过测试不算修复。测试内容改变需要人工核对。原项目源码摘要前后校验，结果存在当前项目元数据。取消只清理由该维护任务唯一标签关联的容器。

## 5. Dify 免费限额保护

使用内存假传输，不访问 Dify。覆盖认证错误、额度拒绝、频控、超时不确定状态、额度预留、缓存复用/过期、空结果、非法响应、未登记来源与去重。不重试、不付费回退。超时保留预留额度，认证/额度错误暂停，避免重复消耗。真实免费账户额度和实时服务效果仍需以后显式验证。

## 本轮本地验证

153 项 Python 检查、前端类型检查及构建通过。五组完整离线流程全部通过：工作台 UI、SQLite checkpoint 重建、全栈 CRUD/附件/业务备份/知识库、整套工作台恢复后执行、父子版本故意回归及取消清理。Dify 的 9 项离线故障检查包含在 153 项之内。没有新增模型、视觉或 Dify 请求，没有导出或恢复真实用户资料。GitHub 结果以 Actions 状态为准，不将包构建当作 Windows 完整验证。
