"""Shared user-facing evidence contract; pure data, no provider or storage access."""

EVIDENCE_LEVELS = [
    {"id": "execution", "title": "执行完成", "meaning": "流程已结束；不表示全部需求已验收。"},
    {"id": "build", "title": "构建检查", "meaning": "当前源码可编译；不证明业务行为正确。"},
    {"id": "generated", "title": "生成测试", "meaning": "智能体编写的测试通过；可能与实现遗漏同一需求。"},
    {"id": "independent", "title": "固定独立套件", "meaning": "预先定义的检查通过；只覆盖套件明确检查的内容。"},
    {"id": "health", "title": "页面基础检查", "meaning": "检查挂载、错误、溢出等基础行为；不判断美观或完整无障碍。"},
    {"id": "review", "title": "独立角色审查", "meaning": "独立上下文与权限的辅助审查；角色可配置不同文字模型；不同模型仍可能存在共同偏差。"},
    {"id": "acceptance", "title": "逐条需求确认", "meaning": "用户登记当前源码对应的检查证据；源码、需求或环境变化后需重新核对。"},
]

BOUNDARIES = [
    {"id": "models", "title": "模型范围", "meaning": "文字支持 DeepSeek 与北京千问 qwen3.8-flash，角色选择随任务冻结；千问文字与视觉许可分开，不自动切换供应商。"},
    {"id": "rag", "title": "知识检索", "meaning": "本地全文检索与可选 Dify 远程语义检索分开；未自建语义检索平台。"},
    {"id": "visual", "title": "图像差异检查", "meaning": "按指定区域比较像素变化；不判断设计美观或需求是否满足。"},
    {"id": "source", "title": "源码关联", "meaning": "本地语法树辅助定位JSX与组件；缺少解析环境时退回文字候选。静态候选仍需核对，动态元素不保证精确定位。"},
    {"id": "replay", "title": "受控操作重放", "meaning": "在独立文本副本重复固定操作、核对静态断言并读取冻结回执；不恢复模型调用和真实数据库副作用。"},
    {"id": "cost", "title": "费用估算", "meaning": "本地估算用于预算保护；实际费用以供应商账单为准。"},
]


def contract():
    return {"revision": 2, "levels": EVIDENCE_LEVELS, "boundaries": BOUNDARIES}


def report_notice(state):
    model = state.get("model_name") or "未登记"
    return [
        "", "## 结果含义与能力边界", "",
        f"本次记录的默认文字模型：{model}。历史记录没有模型字段时不推断供应商。",
        "角色模型：" + "；".join(f"{role}={name}" for role,name in state.get("role_models",{}).items()) if state.get("role_models") else "角色模型未单独登记，沿用任务模型。",
        *[f"- {item['title']}：{item['meaning']}" for item in EVIDENCE_LEVELS],
        *[f"- {item['title']}：{item['meaning']}" for item in BOUNDARIES],
        "- 以上是交付时快照；后续逐条确认以项目面板的当前导出为准。",
    ]
