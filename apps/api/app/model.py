"""DeepSeek text model adapter with a local, conservative spend guard."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar, get_args

import truststore
truststore.inject_into_ssl()

from .scope import MAX_PAGES

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
USAGE_DB = DATA_DIR / "api_usage.sqlite3"
from .security_policy import protect_secret_file, check_project_roots, install_log_redaction
protect_secret_file()
check_project_roots()
load_dotenv(ROOT / ".env", override=False)
install_log_redaction()

T = TypeVar("T", bound=BaseModel)
SUPPORTED_MODELS = ("deepseek-flash", "deepseek-v4-pro")


class BudgetExceeded(Exception):
    pass


class ModelNotConfigured(Exception):
    pass


class ModelCallFailed(Exception):
    pass


class ModelOutputInvalid(ModelCallFailed):
    """A settled response failed parsing; distinct from uncertain network failures."""
    def __init__(self,message: str,usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


ExecutorTool = Literal['web.search','web.read','web.read_evidence','knowledge.read_evidence','reference.read','project.list_files','project.route_template','project.request_dependency','project.read_file','project.apply_changes','project.build','project.test','git.snapshot','git.versions','git.restore']


class ProductSpec(BaseModel):
    product_name: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    target_users: list[str] = Field(min_length=1, max_length=5)
    pages: list[str] = Field(min_length=1, max_length=MAX_PAGES)
    core_features: list[str] = Field(min_length=2, max_length=12)
    assumptions: list[str] = Field(max_length=6)
    non_goals: list[str] = Field(min_length=1, max_length=6)
    acceptance_criteria: list[str] = Field(min_length=4, max_length=16)


class PlanStep(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=280)
    depends_on: list[int] = Field(max_length=5)
    acceptance_indices: list[int] = Field(default_factory=list,max_length=16)


class PlanResult(BaseModel):
    steps: list[PlanStep] = Field(min_length=3, max_length=8)

    @model_validator(mode='after')
    def ordered_dependencies(self):
        for index, step in enumerate(self.steps, start=1):
            if len(set(step.acceptance_indices))!=len(step.acceptance_indices) or any(value<0 for value in step.acceptance_indices):
                raise ValueError('验收关联序号须为不重复的非负整数。')
            if len(set(step.depends_on)) != len(step.depends_on) or any(dependency < 1 or dependency >= index for dependency in step.depends_on):
                raise ValueError('依赖必须是此前步骤的序号，不能自依赖、重复依赖或构成循环。')
        return self


class RepairDecision(BaseModel):
    action: Literal["repair", "replan", "stop"]
    reason: str = Field(min_length=1,max_length=400)


class ExecutorDecision(BaseModel):
    model_config = {'extra':'forbid'}
    action: Literal['tool', 'complete_task', 'finish', 'stop']
    task_id: str | None = None
    tool: ExecutorTool | None = None
    arguments: dict = Field(default_factory=dict)
    summary: str = Field(min_length=1,max_length=500)

    @model_validator(mode='before')
    @classmethod
    def canonical_tool_action(cls,value):
        if not isinstance(value,dict):return value
        names=get_args(ExecutorTool)
        action=value.get('action')
        if isinstance(action,str) and action in names and (value.get('tool') is None or value.get('tool')==action):
            return {**value,'action':'tool','tool':action}
        return value

    @model_validator(mode='after')
    def valid_action(self):
        if self.action == 'tool' and (not self.tool or not self.task_id):
            raise ValueError('工具操作需要task_id和tool。')
        if self.action != 'tool' and (self.tool is not None or self.arguments):
            raise ValueError('非工具操作不能携带工具参数。')
        if self.action == 'complete_task' and not self.task_id:
            raise ValueError('完成任务需要task_id。')
        return self


class GeneratedFile(BaseModel):
    path: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=24_000)


class GeneratedBundle(BaseModel):
    files: list[GeneratedFile] = Field(min_length=4, max_length=4)
    summary: str = Field(default="前端原型已生成", min_length=1, max_length=400)

    @field_validator("files", mode="before")
    @classmethod
    def normalize_file_map(cls, value):
        # Accept equivalent path -> source JSON while retaining count/path checks.
        if isinstance(value, dict) and all(isinstance(v, str) for v in value.values()):
            return [{"path": key, "content": content} for key, content in value.items()]
        return value


def _get_settings() -> dict:
    return {
        "enabled": os.getenv("MODEL_CALLS_ENABLED", "false").lower() == "true",
        "provider": os.getenv("MODEL_PROVIDER", "deepseek").strip().lower(),
        "model": os.getenv("MODEL_NAME", "deepseek-flash").strip(),
        "base_url": os.getenv("MODEL_BASE_URL", "https://api.deepseek.com").strip(),
        "api_key": os.getenv("MODEL_API_KEY", "").strip(),
        "budget_cny": float(os.getenv("MODEL_BUDGET_CNY", "99")),
    }


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(USAGE_DB, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE IF NOT EXISTS model_usage (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL, reserve_cny REAL NOT NULL,
        actual_cny REAL, input_tokens INTEGER, output_tokens INTEGER,
        status TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    connection.execute("CREATE TABLE IF NOT EXISTS model_receipts (run_id TEXT NOT NULL, stage TEXT NOT NULL, fingerprint TEXT NOT NULL, value_json TEXT NOT NULL, metadata_json TEXT NOT NULL, PRIMARY KEY(run_id,stage,fingerprint))")
    return connection


@contextmanager
def _db_connection():
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def budget_status(run_id: str | None = None) -> dict:
    settings = _get_settings()
    sql="SELECT COALESCE(SUM(CASE WHEN actual_cny IS NULL THEN reserve_cny ELSE actual_cny END),0) AS spent,COUNT(*) AS calls FROM model_usage"
    with _db_connection() as connection:
        global_row=connection.execute(sql).fetchone()
        row=connection.execute(sql+' WHERE run_id=?',(run_id,)).fetchone() if run_id else global_row
    limit=settings['budget_cny']
    if run_id:
        from .store import get_run
        from .designs import get_design
        state=get_run(run_id) or get_design(run_id) or {}
        limit=float(state.get('run_budget_cny',5))
    spent=float(row['spent'])
    remaining=round(max(0,limit-spent),6)
    global_remaining=round(max(0,settings['budget_cny']-float(global_row['spent'])),6)
    return {'spent_cny_estimate':round(spent,6),'budget_cny':limit,'remaining_cny_estimate':remaining,'calls':row['calls'],
        'scope':'run' if run_id else 'global','available_for_new_calls_cny':min(remaining,global_remaining),
        'global_budget_cny':settings['budget_cny'],'global_spent_cny_estimate':round(float(global_row['spent']),6),
        'global_remaining_cny_estimate':global_remaining}


def budget_breakdown(run_id: str) -> dict:
    """Read ledger summaries only; no model client, prompt or response content."""
    with _db_connection() as connection:
        rows = connection.execute('''SELECT stage,model,COUNT(*) AS calls,
            COALESCE(SUM(actual_cny),0) AS settled_cny,
            COALESCE(SUM(CASE WHEN actual_cny IS NULL THEN reserve_cny ELSE 0 END),0) AS held_cny,
            SUM(CASE WHEN actual_cny IS NULL THEN 1 ELSE 0 END) AS unsettled_calls,
            COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens
            FROM (SELECT CASE WHEN stage LIKE 'executor-%' THEN 'executor'
                WHEN stage LIKE 'design-%' THEN 'product_design'
                WHEN stage LIKE 'repair-%' THEN 'repair' ELSE stage END AS stage,
                model,actual_cny,reserve_cny,input_tokens,output_tokens
                FROM model_usage WHERE run_id=?)
            GROUP BY stage,model ORDER BY stage,model''',(run_id,)).fetchall()
    return {'groups':[{**dict(row),'settled_cny':round(row['settled_cny'],6),
        'held_cny':round(row['held_cny'],6)} for row in rows]}


def _reserve(run_id: str, stage: str, provider: str, model: str, budget: float, reserve_cny: float | None = None) -> str:
    # Reserve against peak, uncached rates and 8 CNY/USD. V4 Pro has a higher
    # output price, so it gets a larger reserve before the request is sent.
    if reserve_cny is None:
        reserve_cny = 0.35 if model == "deepseek-v4-pro" else 0.25
    call_id = str(uuid.uuid4())
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT COALESCE(SUM(CASE WHEN actual_cny IS NULL THEN reserve_cny ELSE actual_cny END), 0) AS spent FROM model_usage").fetchone()
        if float(row["spent"]) + reserve_cny > budget:
            raise BudgetExceeded("本地模型预算即将达到上限；为避免超支，已阻止新调用。")
        from .store import get_run
        from .designs import get_design
        run = get_run(run_id) or get_design(run_id) or {}
        spent = connection.execute('SELECT COALESCE(SUM(CASE WHEN actual_cny IS NULL THEN reserve_cny ELSE actual_cny END),0), COUNT(*) FROM model_usage WHERE run_id=?',(run_id,)).fetchone()
        if int(spent[1]) >= int(run.get('model_call_limit',80)):
            raise BudgetExceeded('该任务已达到模型调用次数上限，已停止新增调用。')
        remaining=max(0,float(run.get('run_budget_cny',5))-float(spent[0]))
        if reserve_cny>remaining:
            raise BudgetExceeded(f'本任务剩余估算额度¥{remaining:.4f}，不足以覆盖本次调用预留¥{reserve_cny:.4f}；已保留当前结果并停止新调用。')
        connection.execute("INSERT INTO model_usage VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 'reserved', ?)", (call_id, run_id, stage, provider, model, reserve_cny, datetime.now(timezone.utc).isoformat()))
        connection.commit()
        return call_id
    finally:
        connection.close()


def _settle(call_id: str, model: str, input_tokens: int, output_tokens: int) -> float:
    # Conservative peak price; never assume cache discounts or off-peak rates.
    input_rate, output_rate = (0.66, 3.96) if model == "deepseek-v4-pro" else (0.30, 1.20)
    usd = input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000
    cny = usd * 8.0
    with _db_connection() as connection:
        connection.execute("UPDATE model_usage SET actual_cny=?, input_tokens=?, output_tokens=?, status='completed' WHERE id=?", (cny, input_tokens, output_tokens, call_id))
    return round(cny, 6)


def _client(settings: dict) -> OpenAI:
    if not settings["enabled"]:
        raise ModelNotConfigured("模型调用未启用。请在项目 .env 中打开 MODEL_CALLS_ENABLED。")
    if settings["provider"] != "deepseek":
        raise ModelNotConfigured("当前首轮仅接入 DeepSeek；其他供应商稍后通过适配器加入。")
    if not settings["api_key"]:
        raise ModelNotConfigured("DeepSeek API Key 未配置。")
    return OpenAI(api_key=settings["api_key"], base_url=settings["base_url"], timeout=45, max_retries=0)


def parse_model_json(content: str,schema: type[T]) -> T:
    candidate=content.strip()
    if candidate.startswith('```json\n') and candidate.endswith('```'):
        candidate=candidate[8:-3].strip()
    elif candidate.startswith('```\n') and candidate.endswith('```'):
        candidate=candidate[4:-3].strip()
    return schema.model_validate(json.loads(candidate))


def generate_json(run_id: str, stage: str, system_prompt: str, user_prompt: str, schema: type[T], max_tokens: int = 900, model_name: str | None = None) -> tuple[T, dict]:
    settings = _get_settings()
    selected_model = model_name or settings["model"]
    if selected_model not in SUPPORTED_MODELS:
        raise ModelNotConfigured("所选 DeepSeek 模型不在允许列表中。")
    settings["model"] = selected_model
    user_prompt += "\n输出必须符合此 JSON Schema：\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    import hashlib
    fingerprint = hashlib.sha256(json.dumps({'system':system_prompt,'user':user_prompt,'model':selected_model,'max_tokens':max_tokens},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    with _db_connection() as connection:
        receipt = connection.execute('SELECT value_json,metadata_json FROM model_receipts WHERE run_id=? AND stage=? AND fingerprint=?',(run_id,stage,fingerprint)).fetchone()
    if receipt:
        return schema.model_validate(json.loads(receipt['value_json'])), {**json.loads(receipt['metadata_json']),'replayed':True}
    client = _client(settings)
    input_rate, output_rate = (0.66, 3.96) if selected_model == "deepseek-v4-pro" else (0.30, 1.20)
    input_bound = len((system_prompt + user_prompt).encode("utf-8")) + 2000
    reserve = max(0.35 if selected_model == "deepseek-v4-pro" else 0.25, round((input_bound * input_rate + max_tokens * output_rate) / 1_000_000 * 8 * 1.1 + 0.01, 4))
    call_id = _reserve(run_id, stage, settings["provider"], settings["model"], settings["budget_cny"], reserve)
    try:
        response = client.chat.completions.create(
            model=settings["model"],
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=0.2,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:
        with _db_connection() as connection:
            # Preserve the full reserve: a connection failure may happen after the provider received the request.
            connection.execute("UPDATE model_usage SET status='uncertain' WHERE id=?", (call_id,))
        raise ModelCallFailed("DeepSeek 请求失败；密钥已隐藏，预留额度暂时保留以防重复超支。") from exc

    usage = response.usage
    input_tokens = getattr(usage, 'prompt_tokens', None)
    output_tokens = getattr(usage, 'completion_tokens', None)
    usage_known = all(type(value) is int and value >= 0 for value in (input_tokens, output_tokens))
    if usage_known:
        cost_cny = _settle(call_id, settings["model"], input_tokens, output_tokens)
    else:
        # A successful response is not evidence of its billed token count.
        # Keep the original reservation, including on receipt replay and parse errors.
        with _db_connection() as connection:
            connection.execute("UPDATE model_usage SET status='usage_missing' WHERE id=?", (call_id,))
        cost_cny = reserve
        input_tokens = output_tokens = None
    if response.choices and response.choices[0].finish_reason == "length":
        raise ModelCallFailed(f"模型输出达到 {max_tokens} token 上限而被截断；费用已记录，请缩小任务或调整输出容量。")
    content = response.choices[0].message.content if response.choices else None
    metadata = {"provider": settings["provider"], "model": settings["model"], "input_tokens": input_tokens, "output_tokens": output_tokens, "estimated_cost_cny": cost_cny,
                "usage_status": "reported" if usage_known else "missing_reservation_held"}
    try:
        if not content:
            raise ValueError("empty JSON response")
        value = parse_model_json(content,schema)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        failure_directory=DATA_DIR/'model_failures'
        failure_directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        failure_path=failure_directory/(call_id+'.json')
        descriptor=os.open(failure_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump({'run_id':run_id,'stage':stage,'content':(content or '')[:200000],'metadata':metadata,'error_type':type(exc).__name__},stream,ensure_ascii=False)
        if isinstance(exc, ValidationError):
            issues = [f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}" + (':' + str(error.get('ctx',{}).get('error',''))[:140] if error['type']=='value_error' else '') for error in exc.errors()[:5]]
            reason = "字段校验 " + ", ".join(issues)
        elif isinstance(exc, json.JSONDecodeError):
            reason = f"JSON格式不完整，位置{exc.pos}、第{exc.lineno}行"
        else:
            reason = "模型未返回有效内容"
        failure=ModelOutputInvalid(f"模型返回内容未通过结构校验（{reason}）；调用费用已计入预算。",metadata)
        raise failure from exc

    with _db_connection() as connection:
        connection.execute('INSERT OR REPLACE INTO model_receipts VALUES (?,?,?,?,?)',(run_id,stage,fingerprint,json.dumps(value.model_dump(),ensure_ascii=False),json.dumps(metadata,ensure_ascii=False)))
    return value, metadata
