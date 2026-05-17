import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from src.config import RegisterConfig, SchedulerConfig, TelegramConfig
from src.db.scheduler_run import SchedulerRunOperate
from src.db.user import UserOperate
from src.services import get_emby_client, EmbyService
from src.core.utils import timestamp, format_duration
from src.services.user_service import UserService

logger = logging.getLogger(__name__)


class RunContext:
    """传给 job 函数的上下文：累加 summary 字段、追加日志。

    用法：
        async def my_job(ctx: RunContext):
            ctx.log("开始处理…")
            ctx.summary['scanned'] = 10
            ctx.summary['disabled'] = 0
    """

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.summary: dict[str, Any] = {}
        self.logs: list[str] = []
        self._max_logs = 800  # 内存里挡一道，落库会再截断

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        if len(self.logs) >= self._max_logs:
            # 丢掉最早的，保留最新
            del self.logs[: len(self.logs) - self._max_logs + 1]
        self.logs.append(line)
        logger.info(f"[{self.job_id}] {message}")


class SchedulerService:
    _scheduler = None
    _scheduler_loop: Optional[asyncio.AbstractEventLoop] = None
    # 每个 job 的最近一次执行情况（运行中态用，DB 持久化负责完成态）
    # { job_id: {status, started_at, finished_at, error, summary?, trigger?} }
    _last_runs: dict[str, dict] = {}
    # 当前正在「运行中」的 job ID（用于幂等避免重复触发）
    _running: set[str] = set()

    # ============== Job 元数据注册表（用于前端列表 / 手动触发权限） ==============
    # 每条目: id, name, description, manual(是否允许手动触发)
    JOB_DEFINITIONS = [
        {
            'id': 'check_expired',
            'name': '过期用户检查',
            'description': '查找已过期账号，禁用本地状态并同步禁用 Emby 账户。',
        },
        {
            'id': 'check_expiring',
            'name': '即将过期检查',
            'description': '记录 3 天内到期的账号，供后续提醒任务使用。',
        },
        {
            'id': 'expiry_reminders',
            'name': '到期提醒推送',
            'description': '向到期前 N 天的用户发送提醒消息。',
        },
        {
            'id': 'daily_stats',
            'name': '每日统计汇总',
            'description': '汇总注册用户 / 活跃用户 / 注册码 / Emby 状态写入日志。',
        },
        {
            'id': 'cleanup_sessions',
            'name': '不活跃会话清理',
            'description': '巡检 Emby 当前会话数。',
        },
        {
            'id': 'emby_sync',
            'name': 'Emby 用户同步',
            'description': '校对本地 EMBYID、用户名、启停状态与下载权限。',
        },
        {
            'id': 'cleanup_no_emby',
            'name': '无 Emby 账户用户清理',
            'description': '清理注册超过配置天数仍未创建 Emby 账户的用户。开关：AUTO_CLEANUP_NO_EMBY',
        },
        {
            'id': 'enforce_group_membership',
            'name': 'Telegram 群组成员资格巡检',
            'description': '检查已绑定 Telegram 的用户是否仍在必需群组内；不在则禁用本地账号 + Emby。开关：REQUIRE_GROUP_MEMBERSHIP',
        },
    ]

    @classmethod
    def _record_run_start(cls, job_id: str, trigger: str) -> int:
        started = int(time.time())
        cls._last_runs[job_id] = {
            'status': 'running',
            'started_at': started,
            'finished_at': None,
            'error': None,
            'summary': None,
            'trigger': trigger,
        }
        cls._running.add(job_id)
        return started

    @classmethod
    def _record_run_end(
        cls,
        job_id: str,
        started: int,
        trigger: str,
        error: Optional[str],
        summary: Optional[dict],
    ) -> None:
        cls._last_runs[job_id] = {
            'status': 'failed' if error else 'success',
            'started_at': started,
            'finished_at': int(time.time()),
            'error': (error or None) and str(error)[:500],
            'summary': summary or None,
            'trigger': trigger,
        }
        cls._running.discard(job_id)

    @classmethod
    async def _run_with_tracking(
        cls,
        job_id: str,
        fn: Callable[..., Awaitable[Any]],
        *,
        trigger: str = 'scheduled',
    ) -> dict:
        """执行 job 并把 last-run 落库。供 APScheduler 调度与管理员手动触发共用。

        `fn` 既兼容老签名 `async def f()`，也接受新签名 `async def f(ctx: RunContext)`。
        """
        if job_id in cls._running:
            return cls._last_runs.get(job_id, {'status': 'running'})

        started = cls._record_run_start(job_id, trigger)
        logger.info(f"▶️ 任务 {job_id} 开始执行 ({trigger})")

        # 落库一条「运行中」记录，结束后回填
        try:
            run_id = await SchedulerRunOperate.start_run(job_id, trigger=trigger)
        except Exception as exc:  # pragma: no cover - 数据库不可用时不阻塞主任务
            logger.warning(f"无法创建 scheduler_run 记录: {exc}")
            run_id = 0

        ctx = RunContext(job_id)
        error_text: Optional[str] = None
        try:
            await fn(ctx)
        except Exception as exc:
            error_text = str(exc) or exc.__class__.__name__
            ctx.log(f"❌ 任务执行异常: {exc}")
            logger.exception(f"❌ 任务 {job_id} 执行异常: {exc}")
        finally:
            cls._record_run_end(
                job_id,
                started,
                trigger,
                error_text,
                dict(ctx.summary) if ctx.summary else None,
            )
            if run_id:
                try:
                    await SchedulerRunOperate.finish_run(
                        run_id,
                        status='failed' if error_text else 'success',
                        error=error_text,
                        summary=ctx.summary or None,
                        logs=ctx.logs or None,
                    )
                    await SchedulerRunOperate.trim_history(job_id)
                except Exception as exc:  # pragma: no cover
                    logger.warning(f"无法回填 scheduler_run #{run_id}: {exc}")

        result = cls._last_runs[job_id]
        elapsed = (result['finished_at'] or started) - started
        if error_text:
            logger.info(f"⏹️ 任务 {job_id} 失败结束 (耗时 {elapsed}s)")
        else:
            logger.info(f"✅ 任务 {job_id} 完成 (耗时 {elapsed}s)")
        return result

    @classmethod
    def _make_scheduled(cls, job_id: str, fn: Callable[..., Awaitable[Any]]):
        """生成给 APScheduler 用的 async wrapper（带 last-run 追踪）。"""
        async def runner():
            await cls._run_with_tracking(job_id, fn, trigger='scheduled')
        runner.__name__ = f"_run_{job_id}"
        return runner

    # ============== 手动触发入口（管理员 API 调用） ==============

    @classmethod
    def _resolve_job(cls, job_id: str) -> Optional[Callable[[], Awaitable[None]]]:
        mapping: dict[str, Callable[[], Awaitable[None]]] = {
            'check_expired': cls.check_expired_users,
            'check_expiring': cls.check_expiring_users,
            'expiry_reminders': cls.send_expiry_reminders,
            'daily_stats': cls.daily_stats,
            'cleanup_sessions': cls.cleanup_inactive_sessions,
            'emby_sync': cls.emby_sync,
            'cleanup_no_emby': cls.cleanup_no_emby_users,
            'enforce_group_membership': cls.enforce_group_membership,
        }
        return mapping.get(job_id)

    @classmethod
    async def trigger_job(cls, job_id: str) -> tuple[bool, str, Optional[dict]]:
        """手动触发指定 job。运行在调度器所在事件循环上（如果可用），
        否则就在当前协程里 await（API 线程）。

        Returns:
            (ok, message, run_record) —— ok 仅表示触发成功，job 本身的结果在
            run_record 中（status / error）。
        """
        fn = cls._resolve_job(job_id)
        if fn is None:
            return False, f"未知任务: {job_id}", None
        if job_id in cls._running:
            return False, f"任务 {job_id} 正在执行中，请稍候", cls._last_runs.get(job_id)

        sched_loop = cls._scheduler_loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        coro_factory = lambda: cls._run_with_tracking(job_id, fn, trigger='manual')

        if sched_loop is not None and sched_loop is not running_loop:
            # API 线程触发 → 把任务安排到调度器所在 loop，立即返回，不阻塞 API
            asyncio.run_coroutine_threadsafe(coro_factory(), sched_loop)
            return True, "已触发，正在后台执行", cls._last_runs.get(job_id, {'status': 'running'})

        # 没有独立 scheduler loop：交给共享后台 loop，避免 asgiref/WsgiToAsgi
        # 在请求结束后销毁 per-request executor 导致孤儿任务崩溃。
        from src.core.background import submit_background
        submit_background(coro_factory())
        return True, "已触发", cls._last_runs.get(job_id, {'status': 'running'})

    @classmethod
    async def list_jobs(cls) -> list[dict]:
        """返回 job 列表 + 计划时间 + 上次运行情况，供管理员前端展示。

        last_run 优先取数据库里的最后一条（重启后仍有效），
        正在运行中的 job 用内存 `_last_runs` 覆盖以拿到「未结束」状态。
        """
        sched = cls._scheduler
        scheduled_map: dict[str, object] = {}
        if sched is not None and sched.running:
            for j in sched.get_jobs():
                scheduled_map[j.id] = j

        items = []
        for definition in cls.JOB_DEFINITIONS:
            jid = definition['id']
            scheduled = scheduled_map.get(jid)
            next_run = None
            schedule_str = None
            if scheduled is not None:
                if getattr(scheduled, 'next_run_time', None):
                    next_run = int(scheduled.next_run_time.timestamp())
                trigger = scheduled.trigger
                schedule_str = str(trigger) if trigger else None

            is_running = jid in cls._running
            last_run: Optional[dict]
            if is_running:
                last_run = cls._last_runs.get(jid)
            else:
                try:
                    last_run = await SchedulerRunOperate.get_last_run_summary(jid)
                except Exception as exc:  # pragma: no cover
                    logger.warning(f"读取 scheduler_run 失败: {exc}")
                    last_run = cls._last_runs.get(jid)

            items.append({
                **definition,
                'enabled': scheduled is not None,
                'schedule': schedule_str,
                'next_run_at': next_run,
                'last_run': last_run,
                'is_running': is_running,
            })
        return items

    @classmethod
    async def get_job_history(cls, job_id: str, *, limit: int = 20) -> list[dict]:
        return await SchedulerRunOperate.get_history(job_id, limit=limit)

    @classmethod
    async def get_last_run_detail(cls, job_id: str) -> Optional[dict]:
        """完整的最近一次运行（含 logs）。"""
        if job_id in cls._running:
            return cls._last_runs.get(job_id)
        return await SchedulerRunOperate.get_last_run(job_id)

    @classmethod
    def get_scheduler(cls):
        if cls._scheduler is None:
            cls._scheduler = AsyncIOScheduler(timezone=SchedulerConfig.TIMEZONE)
        return cls._scheduler

    @staticmethod
    async def check_expired_users(ctx: RunContext):
        """检查过期用户并禁用"""
        ctx.log("🔍 开始检查过期用户...")
        try:
            expired_users = await UserOperate.get_expired_users()
            ctx.summary['scanned'] = len(expired_users)
            ctx.summary['disabled'] = 0
            ctx.summary['failed'] = 0
            if not expired_users:
                ctx.log("✅ 没有需要处理的过期用户")
                return

            ctx.log(f"📋 发现 {len(expired_users)} 个过期用户")
            emby = get_emby_client()

            for user in expired_users:
                try:
                    if user.EMBYID:
                        await emby.set_user_enabled(user.EMBYID, False)
                    user.ACTIVE_STATUS = False
                    await UserOperate.update_user(user)
                    ctx.summary['disabled'] += 1
                    ctx.log(f"  ⏹️ 已禁用: {user.USERNAME} (UID: {user.UID})")
                except Exception as e:
                    ctx.summary['failed'] += 1
                    ctx.log(f"  ❌ 禁用失败: {user.USERNAME} - {e}")
            ctx.log(
                f"✅ 过期用户检查完成: 禁用 {ctx.summary['disabled']} 个, "
                f"失败 {ctx.summary['failed']} 个"
            )
        except Exception as e:
            ctx.log(f"❌ 检查过期用户时发生错误: {e}")
            raise

    @staticmethod
    async def check_expiring_users(ctx: RunContext):
        """检查即将过期的用户（用于提醒）"""
        ctx.log("🔔 检查即将过期的用户...")
        try:
            expiring_users = await UserOperate.get_expiring_users(days=3)
            ctx.summary['scanned'] = len(expiring_users)
            if not expiring_users:
                ctx.log("✅ 没有即将过期的用户")
                return

            ctx.log(f"📋 发现 {len(expiring_users)} 个即将过期的用户:")
            current = timestamp()
            for user in expiring_users:
                remaining = user.EXPIRED_AT - current
                remaining_str = format_duration(remaining)
                ctx.log(f"  ⚠️ {user.USERNAME} (UID: {user.UID}) - {remaining_str}后过期")
        except Exception as e:
            ctx.log(f"❌ 检查即将过期用户时发生错误: {e}")
            raise

    @staticmethod
    async def cleanup_inactive_sessions(ctx: RunContext):
        """清理不活跃的会话"""
        ctx.log("🧹 清理不活跃会话...")
        try:
            emby = get_emby_client()
            sessions = await emby.get_sessions()
            active = len([s for s in sessions if s.is_active])
            total = len(sessions)
            ctx.summary['active'] = active
            ctx.summary['total'] = total
            ctx.log(f"📊 当前会话: {active} 活跃 / {total} 总计")
        except Exception as e:
            ctx.log(f"❌ 清理会话时发生错误: {e}")
            raise

    @staticmethod
    async def daily_stats(ctx: RunContext):
        """每日统计"""
        ctx.log("📊 生成每日统计...")
        try:
            from src.db.regcode import RegCodeOperate
            registered = await UserOperate.get_registered_users_count()
            active = await UserOperate.get_active_users_count()
            regcodes = await RegCodeOperate.get_active_regcodes_count()
            server_status = await EmbyService.get_server_status()

            ctx.summary.update({
                'registered': registered,
                'user_limit': RegisterConfig.USER_LIMIT,
                'active': active,
                'available_regcodes': regcodes,
                'emby_online': bool(server_status.get('online')),
                'active_sessions': server_status.get('active_sessions', 0)
                    if server_status.get('online') else 0,
            })

            ctx.log("=" * 30)
            ctx.log(f"👥 注册用户: {registered} / {RegisterConfig.USER_LIMIT}")
            ctx.log(f"✅ 活跃用户: {active}")
            ctx.log(f"🎫 可用注册码: {regcodes}")
            ctx.log(f"📺 Emby 状态: {'在线' if server_status.get('online') else '离线'}")
            if server_status.get('online'):
                ctx.log(f"   活跃会话: {server_status.get('active_sessions', 0)}")
            ctx.log("=" * 30)
        except Exception as e:
            ctx.log(f"❌ 生成统计时发生错误: {e}")
            raise

    @staticmethod
    async def send_expiry_reminders(ctx: RunContext):
        """发送到期提醒"""
        from src.services.admin_service import ReminderService
        ctx.log("📧 发送到期提醒...")
        try:
            result = await ReminderService.send_expiry_reminders()
            sent = int(result.get('sent', 0)) if isinstance(result, dict) else 0
            ctx.summary['sent'] = sent
            ctx.log(f"✅ 到期提醒发送完成: {sent} 条")
        except Exception as e:
            ctx.log(f"❌ 发送到期提醒出错: {e}")
            raise

    @staticmethod
    async def emby_sync(ctx: RunContext):
        """定期同步 Emby 用户数据"""
        ctx.log("🔄 开始 Emby 用户数据同步...")
        try:
            success, failed, errors = await EmbyService.sync_all_users()
            ctx.summary['success'] = int(success or 0)
            ctx.summary['failed'] = int(failed or 0)
            ctx.log(f"✅ Emby 同步完成: 成功 {success}, 失败 {failed}")
            if errors:
                for e in errors[:10]:
                    ctx.log(f"  ⚠️ {e}")
        except Exception as e:
            ctx.log(f"❌ Emby 同步出错: {e}")
            raise

    @staticmethod
    async def enforce_group_membership(ctx: RunContext):
        """定时巡检：绑定了 TG 但已退出必需群组的用户 → 禁用本地账号 + 禁用 Emby。

        仅在 `TelegramConfig.REQUIRE_GROUP_MEMBERSHIP` 开启且配置了 `GROUP_ID` 时执行。
        管理员、白名单不会被本任务处理（在 SQL 层面就过滤掉了）。
        """
        from src.services.telegram_membership import TelegramMembershipService
        if not TelegramMembershipService.enforcement_enabled():
            ctx.summary['enabled'] = False
            ctx.log("ℹ️ 群组成员巡检未启用")
            return

        ctx.summary['enabled'] = True
        ctx.log("🛂 开始群组成员资格巡检...")
        try:
            users = await UserOperate.get_active_telegram_bound_users()
            ctx.summary['scanned'] = len(users)
            ctx.summary['in_group'] = 0
            ctx.summary['disabled'] = 0
            ctx.summary['failed'] = 0
            if not users:
                ctx.log("✅ 没有需要检查的用户")
                return

            for u in users:
                try:
                    ok, missing = await TelegramMembershipService.check_user_in_groups(
                        u.TELEGRAM_ID, strict=False
                    )
                    if ok:
                        ctx.summary['in_group'] += 1
                        continue

                    # 拿到了「明确不在群」的判定 → 禁用
                    success, msg = await UserService.disable_user(
                        u, reason="未加入必需 Telegram 群组"
                    )
                    if success:
                        ctx.summary['disabled'] += 1
                        ctx.log(
                            f"  ⏹️ 已禁用 {u.USERNAME} (UID: {u.UID}, "
                            f"TG: {u.TELEGRAM_ID}) — 缺失群组: "
                            f"{', '.join(m.id for m in missing) or '未知'}"
                        )
                    else:
                        ctx.summary['failed'] += 1
                        ctx.log(f"  ⚠️ 禁用 {u.USERNAME} 失败: {msg}")
                except Exception as exc:  # pragma: no cover
                    ctx.summary['failed'] += 1
                    ctx.log(f"  ❌ 巡检 {u.USERNAME} (UID: {u.UID}) 出错: {exc}")

            ctx.log(
                f"✅ 群组成员资格巡检完成: 仍在群 {ctx.summary['in_group']} 个, "
                f"已禁用 {ctx.summary['disabled']} 个, 失败 {ctx.summary['failed']} 个"
            )
        except Exception as exc:
            ctx.log(f"❌ 群组成员资格巡检异常: {exc}")
            raise

    @staticmethod
    async def cleanup_no_emby_users(ctx: RunContext):
        """清理注册后长期未创建 Emby 账户的用户"""
        if not RegisterConfig.AUTO_CLEANUP_NO_EMBY:
            ctx.summary['enabled'] = False
            ctx.log("ℹ️ AUTO_CLEANUP_NO_EMBY 未启用，跳过")
            return
        days = RegisterConfig.AUTO_CLEANUP_NO_EMBY_DAYS
        ctx.summary['enabled'] = True
        ctx.summary['days_threshold'] = days
        ctx.log(f"🧹 开始清理注册超过 {days} 天无 Emby 账户的用户...")
        try:
            users = await UserOperate.get_no_emby_users(days)
            ctx.summary['scanned'] = len(users)
            ctx.summary['deleted'] = 0
            ctx.summary['failed'] = 0
            if not users:
                ctx.log("✅ 没有需要清理的无 Emby 账户用户")
                return

            for user in users:
                try:
                    success, msg = await UserService.delete_user(user, delete_emby=False)
                    if success:
                        ctx.summary['deleted'] += 1
                        ctx.log(f"  🗑️ 已删除: {user.USERNAME} (UID: {user.UID})")
                    else:
                        ctx.summary['failed'] += 1
                        ctx.log(f"  ⚠️ 删除失败: {user.USERNAME} - {msg}")
                except Exception as e:
                    ctx.summary['failed'] += 1
                    ctx.log(f"  ❌ 删除失败: {user.USERNAME} - {e}")
            ctx.log(
                f"✅ 无 Emby 账户用户清理完成: 删除 {ctx.summary['deleted']} 个, "
                f"失败 {ctx.summary['failed']} 个"
            )
        except Exception as e:
            ctx.log(f"❌ 清理无 Emby 账户用户时发生错误: {e}")
            raise

    @classmethod
    async def start(cls):
        """启动调度器"""
        if not SchedulerConfig.ENABLED:
            logger.info("ℹ️ 调度器已禁用")
            return

        # 进程上一次崩溃前的「running」状态行先回写为 failed，避免前端永远转圈
        try:
            reconciled = await SchedulerRunOperate.reconcile_orphans()
            if reconciled:
                logger.info(f"已将 {reconciled} 条残留运行中记录标记为失败")
        except Exception as exc:  # pragma: no cover
            logger.warning(f"reconcile orphans 失败: {exc}")

        scheduler = cls.get_scheduler()
        try:
            cls._scheduler_loop = asyncio.get_running_loop()
        except RuntimeError:
            cls._scheduler_loop = None

        # 解析配置时间
        def parse_time(time_str):
            try:
                hour, minute = map(int, time_str.split(':'))
                return hour, minute
            except Exception:
                return 0, 0

        # 注册定时任务（所有 add_job 都包一层 last-run 追踪）
        h, m = parse_time(SchedulerConfig.EXPIRED_CHECK_TIME)
        scheduler.add_job(cls._make_scheduled('check_expired', cls.check_expired_users), 'cron', hour=h, minute=m, id='check_expired')

        h, m = parse_time(SchedulerConfig.EXPIRING_CHECK_TIME)
        scheduler.add_job(cls._make_scheduled('check_expiring', cls.check_expiring_users), 'cron', hour=h, minute=m, id='check_expiring')
        scheduler.add_job(cls._make_scheduled('expiry_reminders', cls.send_expiry_reminders), 'cron', hour=h, minute=(m + 5) % 60, id='expiry_reminders')

        h, m = parse_time(SchedulerConfig.DAILY_STATS_TIME)
        scheduler.add_job(cls._make_scheduled('daily_stats', cls.daily_stats), 'cron', hour=h, minute=m, id='daily_stats')

        scheduler.add_job(cls._make_scheduled('cleanup_sessions', cls.cleanup_inactive_sessions), 'interval', hours=SchedulerConfig.SESSION_CLEANUP_INTERVAL, id='cleanup_sessions')

        # Emby 数据同步（每 6 小时）
        scheduler.add_job(cls._make_scheduled('emby_sync', cls.emby_sync), 'interval', hours=SchedulerConfig.EMBY_SYNC_INTERVAL, id='emby_sync')

        # 无 Emby 账户用户清理（每天过期检查后执行）
        h_cleanup, m_cleanup = parse_time(SchedulerConfig.EXPIRED_CHECK_TIME)
        scheduler.add_job(cls._make_scheduled('cleanup_no_emby', cls.cleanup_no_emby_users), 'cron', hour=h_cleanup, minute=(m_cleanup + 30) % 60, id='cleanup_no_emby')

        # 群组成员资格巡检（开关 + 群组配置齐备时才注册）
        from src.services.telegram_membership import TelegramMembershipService
        if TelegramMembershipService.enforcement_enabled():
            interval_minutes = max(1, int(TelegramConfig.GROUP_CHECK_INTERVAL_MINUTES or 30))
            scheduler.add_job(
                cls._make_scheduled('enforce_group_membership', cls.enforce_group_membership),
                'interval',
                minutes=interval_minutes,
                id='enforce_group_membership',
            )

        scheduler.start()
        logger.info("=" * 50)
        logger.info(f"🌙 Twilight Scheduler 已启动 ({SchedulerConfig.TIMEZONE})")
        logger.info(f"  - 过期检查: {SchedulerConfig.EXPIRED_CHECK_TIME}")
        logger.info(f"  - 到期提醒: {SchedulerConfig.EXPIRING_CHECK_TIME}")
        logger.info(f"  - 每日统计: {SchedulerConfig.DAILY_STATS_TIME}")
        logger.info(f"  - 会话清理: 每 {SchedulerConfig.SESSION_CLEANUP_INTERVAL} 小时")
        logger.info(f"  - Emby 同步: 每 {SchedulerConfig.EMBY_SYNC_INTERVAL} 小时")
        if RegisterConfig.AUTO_CLEANUP_NO_EMBY:
            logger.info(f"  - 无 Emby 清理: {SchedulerConfig.EXPIRED_CHECK_TIME} (注册超 {RegisterConfig.AUTO_CLEANUP_NO_EMBY_DAYS} 天)")
        if TelegramMembershipService.enforcement_enabled():
            logger.info(
                f"  - 群组成员巡检: 每 {max(1, int(TelegramConfig.GROUP_CHECK_INTERVAL_MINUTES or 30))} 分钟"
            )
        logger.info("=" * 50)
        
        # 立即运行一次统计（走 tracking 包装，复用同一份 ctx/落库逻辑）
        await cls._run_with_tracking('daily_stats', cls.daily_stats, trigger='startup')

    @classmethod
    async def stop(cls):
        """停止调度器"""
        if cls._scheduler and cls._scheduler.running:
            cls._scheduler.shutdown()
            logger.info("👋 调度器已关闭")
