"""
定时任务运行历史

持久化每个 job 每次运行的元数据（起止时间、状态、结构化 summary、文本日志），
供管理后台展示「上次运行了什么 / 处理了多少人」。
"""
import json
import time
from typing import Any, Optional

from sqlalchemy import Integer, String, Text, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import Config
from src.db.utils import create_database


class SchedulerRunDatabaseModel(AsyncAttrs, DeclarativeBase):
    pass


class SchedulerRunModel(SchedulerRunDatabaseModel):
    """一条定时任务执行记录。"""
    __tablename__ = "scheduler_run"

    ID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    JOB_ID: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    TRIGGER: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    STATUS: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    STARTED_AT: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    FINISHED_AT: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ERROR: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    SUMMARY: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    LOGS: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


create_database("scheduler_run", SchedulerRunDatabaseModel)
DATABASE_URL = f'sqlite+aiosqlite:///{Config.DATABASES_DIR / "scheduler_run.db"}'
ENGINE = create_async_engine(DATABASE_URL, echo=Config.SQLALCHEMY_LOG)
SchedulerRunSessionFactory = async_sessionmaker(bind=ENGINE, expire_on_commit=False)

# 单条 LOGS 字段最多保留多少行，避免长尾任务把数据库撑爆。
_MAX_LOG_LINES = 500
# 单条 ERROR 字段截断长度
_MAX_ERROR_LEN = 1000


def _serialize_summary(summary: Any) -> Optional[str]:
    if not summary:
        return None
    try:
        return json.dumps(summary, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _serialize_logs(logs: Any) -> Optional[str]:
    if not logs:
        return None
    if isinstance(logs, str):
        lines = logs.splitlines()
    else:
        lines = [str(x) for x in logs]
    if len(lines) > _MAX_LOG_LINES:
        # 保留首尾，中间打省略提示，便于排查异常
        head = lines[: _MAX_LOG_LINES // 2]
        tail = lines[-_MAX_LOG_LINES // 2 :]
        lines = head + [f"... 省略 {len(logs) - _MAX_LOG_LINES} 行 ..."] + tail
    return "\n".join(lines)


def _parse_summary(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def serialize_run(record: SchedulerRunModel) -> dict:
    """把 ORM 对象转成 JSON-friendly dict，给 API 用。"""
    return {
        'id': record.ID,
        'job_id': record.JOB_ID,
        'trigger': record.TRIGGER,
        'status': record.STATUS,
        'started_at': record.STARTED_AT,
        'finished_at': record.FINISHED_AT,
        'error': record.ERROR,
        'summary': _parse_summary(record.SUMMARY),
        'logs': (record.LOGS.split("\n") if record.LOGS else []),
    }


class SchedulerRunOperate:
    """`scheduler_run` 表的读写入口。"""

    # 单个 job 在表中最多保留多少条历史，超过则按 ID 升序删除最旧的
    HISTORY_LIMIT_PER_JOB = 50

    @staticmethod
    async def start_run(job_id: str, *, trigger: str = "scheduled") -> int:
        """插入一条「运行中」记录，返回主键 ID。"""
        async with SchedulerRunSessionFactory() as session:
            async with session.begin():
                record = SchedulerRunModel(
                    JOB_ID=job_id,
                    TRIGGER=trigger,
                    STATUS="running",
                    STARTED_AT=int(time.time()),
                )
                session.add(record)
                await session.flush()
                return int(record.ID)

    @staticmethod
    async def finish_run(
        run_id: int,
        *,
        status: str,
        error: Optional[str] = None,
        summary: Any = None,
        logs: Any = None,
    ) -> None:
        """结算一条记录。`status` 取 success / failed。"""
        async with SchedulerRunSessionFactory() as session:
            async with session.begin():
                record = await session.get(SchedulerRunModel, run_id)
                if not record:
                    return
                record.STATUS = status
                record.FINISHED_AT = int(time.time())
                if error:
                    record.ERROR = str(error)[:_MAX_ERROR_LEN]
                record.SUMMARY = _serialize_summary(summary)
                record.LOGS = _serialize_logs(logs)

    @staticmethod
    async def trim_history(job_id: str, *, keep: Optional[int] = None) -> int:
        """按 job 保留最近 `keep` 条，返回删除条数。

        注意：SQLAlchemy 2.x AsyncSession 的 `execute()` 会 autobegin 一个事务，
        在那之后再调 `session.begin()` 会抛 "A transaction is already begun"。
        所以 select + delete 必须在同一个事务里。
        """
        limit = keep if keep is not None else SchedulerRunOperate.HISTORY_LIMIT_PER_JOB
        if limit <= 0:
            return 0
        async with SchedulerRunSessionFactory() as session:
            async with session.begin():
                ids_to_keep_q = (
                    select(SchedulerRunModel.ID)
                    .where(SchedulerRunModel.JOB_ID == job_id)
                    .order_by(desc(SchedulerRunModel.ID))
                    .limit(limit)
                )
                keep_rows = (await session.execute(ids_to_keep_q)).scalars().all()
                if not keep_rows:
                    return 0
                min_keep = min(keep_rows)
                result = await session.execute(
                    delete(SchedulerRunModel)
                    .where(SchedulerRunModel.JOB_ID == job_id)
                    .where(SchedulerRunModel.ID < min_keep)
                )
                return int(result.rowcount or 0)

    @staticmethod
    async def get_last_run(job_id: str) -> Optional[dict]:
        async with SchedulerRunSessionFactory() as session:
            result = await session.execute(
                select(SchedulerRunModel)
                .where(SchedulerRunModel.JOB_ID == job_id)
                .order_by(desc(SchedulerRunModel.ID))
                .limit(1)
            )
            record = result.scalar_one_or_none()
            return serialize_run(record) if record else None

    @staticmethod
    async def get_last_run_summary(job_id: str) -> Optional[dict]:
        """轻量版：用于 list 接口，仅返回 status/时间/summary，不取 logs。"""
        full = await SchedulerRunOperate.get_last_run(job_id)
        if not full:
            return None
        full.pop('logs', None)
        return full

    @staticmethod
    async def get_history(job_id: str, *, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit or 20), 100))
        async with SchedulerRunSessionFactory() as session:
            result = await session.execute(
                select(SchedulerRunModel)
                .where(SchedulerRunModel.JOB_ID == job_id)
                .order_by(desc(SchedulerRunModel.ID))
                .limit(limit)
            )
            rows = result.scalars().all()
        return [serialize_run(r) for r in rows]

    @staticmethod
    async def reconcile_orphans(*, older_than_seconds: int = 6 * 3600) -> int:
        """把进程崩溃前残留的 `running` 行标记为 failed（启动时调用）。"""
        cutoff = int(time.time()) - max(60, int(older_than_seconds))
        async with SchedulerRunSessionFactory() as session:
            async with session.begin():
                result = await session.execute(
                    select(SchedulerRunModel)
                    .where(SchedulerRunModel.STATUS == "running")
                    .where(SchedulerRunModel.STARTED_AT < cutoff)
                )
                rows = list(result.scalars().all())
                for r in rows:
                    r.STATUS = "failed"
                    r.FINISHED_AT = r.FINISHED_AT or int(time.time())
                    r.ERROR = (r.ERROR or "进程异常退出，未能完成此次任务")[:_MAX_ERROR_LEN]
            return len(rows)
