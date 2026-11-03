"""Persistent run records used by API, background execution, and trace UI."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
RUNS_DB = DATA_DIR / "runs.sqlite3"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(RUNS_DB, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=20000")
    connection.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS run_controls (run_id TEXT PRIMARY KEY, pause_requested INTEGER NOT NULL DEFAULT 0)")
    connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP)')
    connection.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)')
    connection.commit()
    return connection


@contextmanager
def db_connection():
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_run(run_id: str, request: str, state: dict[str, Any], created_at: str) -> None:
    with db_connection() as connection:
        connection.execute("INSERT INTO runs VALUES (?, ?, ?, ?)", (run_id, request, json.dumps(state, ensure_ascii=False), created_at))


def update_run(run_id: str, state: dict[str, Any]) -> None:
    with db_connection() as connection:
        connection.execute("UPDATE runs SET state=? WHERE id=?", (json.dumps(state, ensure_ascii=False), run_id))


class CorruptRunRecord(Exception):pass


def get_run(run_id: str) -> dict[str, Any] | None:
    with db_connection() as connection:
        row = connection.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:return None
    try:
        value=json.loads(row['state'])
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ValueError,TypeError) as error:raise CorruptRunRecord('运行记录损坏，未自动改写。') from error


def list_runs(limit: int = 30) -> list[dict[str, Any]]:
    with db_connection() as connection:
        rows = connection.execute("SELECT id,state,created_at FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    result=[]
    for row in rows:
        try:
            value=json.loads(row['state'])
            if not isinstance(value,dict):raise ValueError()
        except (ValueError,TypeError):
            value={'run_id':row['id'],'created_at':row['created_at'],'status':'failed','storage_corrupt':True,'trace':[],'last_error':'运行记录损坏；未自动覆盖，请查看数据完整性面板。'}
        result.append(value)
    return result


def interrupt_active_runs() -> None:
    """Mark runs abandoned by a previous API process so the UI can offer checkpoint recovery."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    terminal = {"completed", "failed_validation", "failed", "failed_model", "failed_sandbox", "blocked_budget", "blocked_configuration", "interrupted", "paused", "cancelled"}
    with db_connection() as connection:
        rows = connection.execute("SELECT id, state FROM runs").fetchall()
        for row in rows:
            try:
                state=json.loads(row['state'])
                if not isinstance(state,dict):continue
            except (ValueError,TypeError):continue
            if state.get("status") in terminal:
                continue
            event = {"stage": state.get("stage", "workflow"), "status": "interrupted", "at": now, "summary": "上次本地服务结束；可从 SQLite checkpoint 尝试恢复"}
            state["status"] = "interrupted"
            state["can_resume"] = False
            state["trace"] = state.get("trace", []) + [event]
            connection.execute("UPDATE runs SET state=? WHERE id=?", (json.dumps(state, ensure_ascii=False), row["id"]))


def set_pause_requested(run_id: str, requested: bool) -> None:
    with db_connection() as connection:
        connection.execute("INSERT INTO run_controls VALUES (?, ?) ON CONFLICT(run_id) DO UPDATE SET pause_requested=excluded.pause_requested", (run_id, int(requested)))


def get_pause_requested(run_id: str) -> bool:
    with db_connection() as connection:
        row = connection.execute("SELECT pause_requested FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
    return bool(row and row[0])


class CancellationRequested(Exception):pass


def cancellation(run_id: str, requested: bool | None = None):
    with db_connection() as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS run_cancellations(run_id TEXT PRIMARY KEY,requested INTEGER NOT NULL)')
        if requested is not None:
            connection.execute('INSERT INTO run_cancellations VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET requested=excluded.requested',(run_id,int(requested)))
        row=connection.execute('SELECT requested FROM run_cancellations WHERE run_id=?',(run_id,)).fetchone()
    return bool(row and row[0])
