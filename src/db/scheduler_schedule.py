"""
定时任务计划覆盖

`scheduler_schedule` 表用于持久化管理员通过后台 UI 设置的「触发间隔」覆盖值。
进程启动时会查表，存在覆盖项时优先使用，没有则回退到 `SchedulerConfig` /
`TelegramConfig` 的默认值。

字段说明：
- TRIGGER_TYPE: 'cron_daily' 或 'interval'
- CRON_HOUR / CRON_MINUTE: cron_daily 时使用，0-23 / 0-59
- INTERVAL_SECONDS: interval 时使用，正整数（前端按分钟/小时换算后下发）
"""
import time
from typing import Optional

from sqlalchemy import Integer, String, delete, select
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import Config
from src.db.utils import create_database


class SchedulerScheduleDatabaseModel(AsyncAttrs, DeclarativeBase):
    pass


class SchedulerScheduleModel(SchedulerScheduleDatabaseModel):
    __tablename__ = "scheduler_schedule"

    JOB_ID: Mapped[str] = mapped_column(String(64), primary_key=True)
    TRIGGER_TYPE: Mapped[str] = mapped_column(String(16), nullable=False)
    CRON_HOUR: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    CRON_MINUTE: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    INTERVAL_SECONDS: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    UPDATED_AT: Mapped[int] = mapped_column(Integer, nullable=False)


create_database("scheduler_schedule", SchedulerScheduleDatabaseModel)
DATABASE_URL = f'sqlite+aiosqlite:///{Config.DATABASES_DIR / "scheduler_schedule.db"}'
ENGINE = create_async_engine(DATABASE_URL, echo=Config.SQLALCHEMY_LOG)
SchedulerScheduleSessionFactory = async_sessionmaker(bind=ENGINE, expire_on_commit=False)


TRIGGER_CRON_DAILY = "cron_daily"
TRIGGER_INTERVAL = "interval"
VALID_TRIGGER_TYPES = {TRIGGER_CRON_DAILY, TRIGGER_INTERVAL}

# 防呆：interval 最短 60 秒、最长 7 天，避免误操作造成压垮服务或永不触发
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 7 * 86400


def serialize_override(record: SchedulerScheduleModel) -> dict:
    return {
        'job_id': record.JOB_ID,
        'type': record.TRIGGER_TYPE,
        'hour': record.CRON_HOUR,
        'minute': record.CRON_MINUTE,
        'seconds': record.INTERVAL_SECONDS,
        'updated_at': record.UPDATED_AT,
    }


class SchedulerScheduleOperate:
    @staticmethod
    async def get_override(job_id: str) -> Optional[dict]:
        async with SchedulerScheduleSessionFactory() as session:
            row = await session.get(SchedulerScheduleModel, job_id)
            return serialize_override(row) if row else None

    @staticmethod
    async def list_overrides() -> list[dict]:
        async with SchedulerScheduleSessionFactory() as session:
            result = await session.execute(select(SchedulerScheduleModel))
            return [serialize_override(r) for r in result.scalars().all()]

    @staticmethod
    async def upsert_override(
        job_id: str,
        *,
        trigger_type: str,
        hour: Optional[int] = None,
        minute: Optional[int] = None,
        seconds: Optional[int] = None,
    ) -> dict:
        if trigger_type not in VALID_TRIGGER_TYPES:
            raise ValueError(f"无效的 trigger_type: {trigger_type}")

        if trigger_type == TRIGGER_CRON_DAILY:
            if hour is None or minute is None:
                raise ValueError("cron_daily 需要 hour 和 minute")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError("时间范围越界：hour 0-23 / minute 0-59")
            payload_seconds = None
            payload_hour = int(hour)
            payload_minute = int(minute)
        else:  # interval
            if seconds is None:
                raise ValueError("interval 需要 seconds")
            if not (MIN_INTERVAL_SECONDS <= int(seconds) <= MAX_INTERVAL_SECONDS):
                raise ValueError(
                    f"间隔越界：必须在 {MIN_INTERVAL_SECONDS}-{MAX_INTERVAL_SECONDS} 秒之间"
                )
            payload_seconds = int(seconds)
            payload_hour = None
            payload_minute = None

        now = int(time.time())
        async with SchedulerScheduleSessionFactory() as session:
            async with session.begin():
                existing = await session.get(SchedulerScheduleModel, job_id)
                if existing:
                    existing.TRIGGER_TYPE = trigger_type
                    existing.CRON_HOUR = payload_hour
                    existing.CRON_MINUTE = payload_minute
                    existing.INTERVAL_SECONDS = payload_seconds
                    existing.UPDATED_AT = now
                else:
                    existing = SchedulerScheduleModel(
                        JOB_ID=job_id,
                        TRIGGER_TYPE=trigger_type,
                        CRON_HOUR=payload_hour,
                        CRON_MINUTE=payload_minute,
                        INTERVAL_SECONDS=payload_seconds,
                        UPDATED_AT=now,
                    )
                    session.add(existing)
            await session.refresh(existing)
            return serialize_override(existing)

    @staticmethod
    async def delete_override(job_id: str) -> bool:
        async with SchedulerScheduleSessionFactory() as session:
            async with session.begin():
                result = await session.execute(
                    delete(SchedulerScheduleModel).where(SchedulerScheduleModel.JOB_ID == job_id)
                )
                return bool(result.rowcount)
