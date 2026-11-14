"""Frozen real-task protocol and descriptive scoring only. Never launches a task."""
import hashlib
import json
import math
from pathlib import Path

CASES = [
    {"id": "create", "title": "新建任务管理", "request": "创建中文任务管理应用，支持新增、完成、筛选、删除，刷新后保存数据。", "fixture": "空项目，空 localStorage；使用全新独立项目。", "checks": [
        {"id": "create-add", "action": "新增甲、乙两项任务", "expected": "恰有两项，文字正确"},
        {"id": "create-filter", "action": "完成甲，分别查看未完成与已完成", "expected": "两个列表分别只有乙和甲"},
        {"id": "create-persist", "action": "刷新，删除甲，再刷新", "expected": "完成状态曾保留；删除后只剩乙"}]},
    {"id": "change", "title": "增量修改与保留数据", "request": "为已有任务应用增加高、中、低优先级及筛选，保留旧任务与完成状态。", "fixture": "templates/evaluation/real-task-v1.json 的固定源码副本与种子数据；记录源码摘要与存储快照。", "checks": [
        {"id": "change-retain", "action": "载入升级前数据", "expected": "甲乙均保留，乙仍已完成"},
        {"id": "change-filter", "action": "将甲设为高优先级并筛选，再刷新", "expected": "只显示甲，优先级保存"},
        {"id": "change-regression", "action": "新增丙、完成并删除丙", "expected": "原有增删及完成操作正常，原始副本未改"}]},
    {"id": "repair", "title": "固定缺陷修复", "request": "修复任务状态筛选失效的问题，保留已有数据及其他操作。", "fixture": "templates/evaluation/real-task-v1.json 中的固定源码与 repair_mutation；先用 independent_filter_test 证明基线通过、注入后失败，再交给模型。", "checks": [
        {"id": "repair-red", "action": "在坏版本中分别筛选完成和未完成", "expected": "预检复现两种筛选均显示全部任务"},
        {"id": "repair-green", "action": "修复后分别筛选完成和未完成", "expected": "各只显示对应任务"},
        {"id": "repair-regression", "action": "新增、删除、刷新并核对旧数据", "expected": "无数据丢失，其余功能保持"}]},
    {"id": "knowledge", "title": "冻结资料驱动开发", "request": "根据提供的合成规则文档创建报名表单。", "fixture": "冻结文档：姓名必填；名额 2 人；重复姓名不允许；满额后拒绝新增；不使用外部知识库请求。", "checks": [
        {"id": "knowledge-required", "action": "空姓名提交，然后报名甲", "expected": "空姓名被拒绝，甲成功"},
        {"id": "knowledge-limits", "action": "再次报名甲，再报名乙、丙", "expected": "重复甲被拒绝；乙成功；丙因满额被拒绝"},
        {"id": "knowledge-source", "action": "核对引用与资料快照", "expected": "能够追溯上述规则到冻结资料，未把引用当作验收通过"}]},
    {"id": "crud", "title": "受控后端应用", "request": "创建使用 FastAPI/SQLite 的物品登记应用，字段为名称和数量，支持新增、编辑、删除和持久保存。", "fixture": "使用 templates/evaluation/real-task-v1.json 的 crud_schema 与空测试数据库，独立数据目录；不可使用用户真实数据库。", "checks": [
        {"id": "crud-write", "action": "新增甲数量 2，再编辑为 3", "expected": "页面及受控 API 均读取到数量 3"},
        {"id": "crud-persist", "action": "在独立测试环境重启应用后读取", "expected": "甲数量仍为 3"},
        {"id": "crud-delete", "action": "删除甲并重新读取，核对测试数据路径", "expected": "甲不存在；原工作台数据没有改变"}]},
]
STRATEGIES = ["single", "team"]
REPEATS = 3
FAILURES = ["requirements", "generation", "tool", "tests", "retrieval", "environment", "budget", "unknown"]


def finite_nonnegative(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def protocol():
    fixture = Path(__file__).resolve().parents[3] / 'templates/evaluation/real-task-v1.json'
    fixture_digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    body = {"id": "real-task-protocol-v1", "cases": CASES, "strategies": STRATEGIES, "repeats": REPEATS, "fixture_digest": fixture_digest}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**body, "digest": digest, "planned_attempts": len(CASES) * len(STRATEGIES) * REPEATS,
            "execution_enabled": False, "provider_requests": 0,
            "notice": "仅准备评测。单角色策略须另行准备执行适配；不会以三角色结果冒充单角色。运行前冻结基线源码/资料、独立检查、环境和每题预算，付费前告知用户。结果由人工登记，不自动核实证据。"}


def result_template():
    suite = protocol()
    return {"suite_digest": suite["digest"], "conditions": {
        "source_revision": "", "environment_digest": "", "fixture_digest": suite["fixture_digest"], "checks_digest": "",
        "provider": "", "model": "", "budget_per_attempt_cny": None,
    }, "attempts": [{"case_id": case["id"], "strategy": strategy, "repeat": repeat,
        "outcome": "not_run", "run_id": "", "source_digest": "", "checks": {},
        "build_passed": None, "repair_rounds": None, "interventions": None,
        "seconds": None, "estimated_cost_cny": None, "failure_category": None,
        "evidence_refs": []} for case in CASES for strategy in STRATEGIES for repeat in range(1, REPEATS + 1)]}


def assess(document):
    """Missing, stopped and assisted attempts never count as autonomous successes."""
    suite = protocol()
    if document.get("suite_digest") != suite["digest"]:
        raise ValueError("评测协议不一致，请重新下载结果模板。")
    conditions = document.get("conditions")
    if not isinstance(conditions, dict):
        raise ValueError("缺少冻结条件。")
    ready = all(isinstance(conditions.get(k), str) and conditions[k].strip() for k in
                ("source_revision", "environment_digest", "fixture_digest", "checks_digest", "provider", "model"))
    budget = conditions.get("budget_per_attempt_cny")
    ready = bool(ready and finite_nonnegative(budget) and budget > 0)
    cases = {case["id"]: {check["id"] for check in case["checks"]} for case in CASES}
    rows = document.get("attempts")
    if not isinstance(rows, list) or len(rows) > suite["planned_attempts"]:
        raise ValueError("评测记录数量或格式异常。")
    seen = set()
    run_ids = set()
    stats = {s: {"planned": len(CASES) * REPEATS, "recorded_runs": 0, "autonomous_passed": 0,
                 "first_passed": 0, "assisted_passed": 0, "unverified": 0, "known_cost_cny": 0,
                 "cost_unknown_runs": 0, "over_budget_runs": 0, "seconds": [], "interventions": 0, "unknown_intervention_runs": 0,
                 "failures": {category: 0 for category in FAILURES}} for s in STRATEGIES}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("评测条目须为对象。")
        key = (row.get("case_id"), row.get("strategy"), row.get("repeat"))
        if key[0] not in cases or key[1] not in STRATEGIES or type(key[2]) is not int or key[2] not in range(1, REPEATS + 1) or key in seen:
            raise ValueError("用例、策略、重复编号无效或重复。")
        seen.add(key)
        outcome = row.get("outcome")
        if outcome not in {"not_run", "passed", "failed", "stopped"}:
            raise ValueError("未知评测结果。")
        if outcome == "not_run":
            continue
        run_id = row.get("run_id")
        if run_id:
            if not isinstance(run_id, str) or run_id in run_ids:
                raise ValueError("同一次运行不能重复计为不同评测尝试。")
            run_ids.add(run_id)
        for field in ("seconds", "estimated_cost_cny", "repair_rounds", "interventions"):
            value = row.get(field)
            if value is not None and not finite_nonnegative(value):
                raise ValueError("费用、时间及次数须为非负有限数字或空值。")
        for field in ("repair_rounds", "interventions"):
            if row.get(field) is not None and type(row[field]) is not int:
                raise ValueError("修复和介入次数必须是整数。")
        checks = row.get("checks", {})
        if not isinstance(checks, dict) or set(checks) - cases[key[0]] or any(type(v) is not bool for v in checks.values()):
            raise ValueError("独立检查编号或结果无效。")
        stat = stats[key[1]]
        stat["recorded_runs"] += 1
        if row.get("estimated_cost_cny") is None:
            stat["cost_unknown_runs"] += 1
        else:
            stat["known_cost_cny"] += row["estimated_cost_cny"]
            if ready and row["estimated_cost_cny"] > budget:
                stat["over_budget_runs"] += 1
        if row.get("seconds") is not None:
            stat["seconds"].append(row["seconds"])
        if row.get("interventions") is None:
            stat["unknown_intervention_runs"] += 1
        else:
            stat["interventions"] += row["interventions"]
        refs = row.get("evidence_refs")
        proven = bool(ready and outcome == "passed" and row.get("build_passed") is True
                      and set(checks) == cases[key[0]] and all(checks.values())
                      and row.get("run_id") and row.get("source_digest")
                      and isinstance(refs, list) and refs and all(isinstance(v, str) and v.strip() for v in refs))
        repairs = row.get("repair_rounds")
        if proven and row.get("interventions") == 0 and repairs is not None and repairs <= 3:
            stat["autonomous_passed"] += 1
            stat["first_passed"] += int(repairs == 0)
        elif proven and row.get("interventions", 0) is not None and row.get("interventions", 0) > 0:
            stat["assisted_passed"] += 1
        elif outcome == "passed":
            stat["unverified"] += 1
        if outcome in {"failed", "stopped"}:
            category = row.get("failure_category") or "unknown"
            if category not in FAILURES:
                raise ValueError("未知失败分类。")
            stat["failures"][category] += 1
    for stat in stats.values():
        stat["pending"] = stat["planned"] - stat["recorded_runs"]
        stat["autonomous_rate_of_plan"] = stat["autonomous_passed"] / stat["planned"]
        stat["first_pass_rate_of_plan"] = stat["first_passed"] / stat["planned"]
        times = stat.pop("seconds")
        stat["mean_seconds_known"] = sum(times) / len(times) if times else None
        stat["timed_runs"] = len(times)
    comparable = ready and all(s["pending"] == 0 and s["cost_unknown_runs"] == 0
                              and s["unknown_intervention_runs"] == 0 and s["over_budget_runs"] == 0
                              and s["unverified"] == 0 and s["timed_runs"] == s["recorded_runs"] for s in stats.values())
    return {"suite_digest": suite["digest"], "conditions_frozen": ready, "comparison_ready": comparable, "strategies": stats,
            "complete": all(s["pending"] == 0 for s in stats.values()), "provider_requests": 0,
            "notice": "人工登记的描述性统计，未验证引用文件真实性；所有计划次数均计入分母，未运行不算失败但不提供成功证据。条件、费用或证据不完整时不能作模型排名。"}
