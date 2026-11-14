# 安装与本机运行

支持目标：macOS（Apple Silicon优先）和Windows x64。当前Mac有实际运行证据；Windows保留构建配方，完整运行未验收且不在本轮范围，不据此宣称已验收支持。

## 基础工具

- Python **3.13**、Git。
- macOS：Colima与Docker CLI；可使用Homebrew安装`python@3.13 git colima docker`。
- Windows：Python 3.13（包含`py`启动器）、Git和[Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，启用Linux容器后端。WSL2/虚拟化要求以Docker官方说明为准，安装器不会自动修改或重启系统。
- 从源码构建时还需要Node.js **24**；预构建安装包无需宿主Node。
- 首次准备镜像建议至少8GiB空闲磁盘，宿主依赖安装至少2GiB。容器首次联网拉取固定依赖，执行生成代码时禁网。

## 一键入口

macOS：`安装.command`，之后`配置.command`、`启动.command`；停止用`停止.command`，诊断用`诊断.command`。

Windows：`install.cmd`，之后`config.cmd`、`start.cmd`；停止用`stop.cmd`，诊断用`doctor.cmd`。

也可在项目目录执行：

```sh
python3.13 scripts/manage.py doctor
python3.13 scripts/manage.py install --plan
python3.13 scripts/manage.py install
python3.13 scripts/manage.py config wizard
python3.13 scripts/manage.py serve
```

Windows把`python3.13`换成`py -3.13`。配置入口使用隐藏输入，不把Key写入命令历史。`.env`初始调用开关关闭，填Key后仍需要自行确认`MODEL_CALLS_ENABLED=true`及预算，再重启。诊断不连接模型供应商。API只监听127.0.0.1:8000，界面127.0.0.1:5173；API文档默认不公开。

源码安装固定使用requirements.lock、package-lock.json；原有环境或配置不被自动删除。运行中的项目拒绝重新安装或升级。安装包内的预构建界面由独立静态服务提供，不使用前端开发服务器。

## 可选功能

- 本地免费OCR：macOS先准备Tesseract，再运行`python3.13 scripts/manage.py install --ocr`。Windows先自行安装Tesseract并加入PATH，再使用同一入口（改用py）。语言数据有固定版本/哈希。没有付费OCR请求。
- 提前准备运行镜像：`scripts/manage.py install --prepare-runtime`。macOS按需启动Colima；Windows需先启动Docker Desktop。会下载数GB镜像，保留足够磁盘；不需要每次运行。
- Docker context默认macOS为`colima`、Windows为`default`。Docker Desktop使用其他context时，可在本地.env设置`PA_DOCKER_CONTEXT=desktop-linux`。
- Dify、千问为可选配置；核心路径不依赖RAG。不自动上传资料、安装外部MCP或调用模型。

## 可选千问文字和角色模型

在“本机设置→配置服务”选择“千问·可选文字生成与审查”。本轮支持北京 `qwen3.8-flash`，接口默认为 `https://dashscope.aliyuncs.com/compatible-mode/v1`；北京业务空间专属官方域名也可填写。可单独填文字Key，或显式勾选复用已填的北京视觉Key。保存不发起验证；模型权限和费用以当前账户为准。

对应环境变量：`QWEN_TEXT_API_KEY`、`QWEN_TEXT_USE_VISION_KEY`、`QWEN_TEXT_BASE_URL`、`QWEN_TEXT_CALLS_ENABLED`。复用和调用两个开关默认false，独立于视觉许可；文字千问还需要 `MODEL_CALLS_ENABLED=true` 总许可。许可修改后重启。保持原单任务与累计费用上限；不假定免费、不自动重试、不切换备用供应商。

创建方案前展开“三个智能体的文字模型”，分别选规划/开发/审查模型；空项沿用任务默认模型。方案和模板保存这些选择，确认开发后冻结，不改写已有任务。设置页默认文字模型仍配置DeepSeek，新任务上方可选择千问；使用不同模型不代表已证明审查效果改善。

## 排错

先运行诊断入口；已占用的8000/5173端口不会被强行终止。启动日志仅保存在本版本data/api.log，请不要把包含业务资料的日志公开上传。

旧`scripts/start.command`和`scripts/stop.command`保留为开发模式入口；安装版使用根目录入口。日常启动无需打开源码开发服务器。升级和回退见[DISTRIBUTION.md](DISTRIBUTION.md)。

## 可视化首次配置

安装并启动后，未启用模型的新用户会进入「设置与维护」的首次使用向导。可以在页面检查工具、镜像和预算，分别填写文字/视觉/知识库密钥；可选服务不需要同时配置。文字模型的费用许可需要明确勾选。保存后先停止并重新启动，再点击「重新检查」；检查全部就绪后可标记准备完成。不会自动验证供应商密钥或产生模型费用。

「设置与维护」还提供整套工作台备份恢复和可预览的脱敏诊断包。恢复副本需要单独安装依赖；当前工作台与新副本使用相同端口，切换前先停止当前服务。

0.3.1：当前范围以[PROJECT_STATUS](PROJECT_STATUS.md)为准。Dify先填Key，再在面板确认账户免费策略；只有主动远程操作才会请求平台。运行时先关闭交互预览，再开始其他构建或浏览器检查。
