"""
核心工具模块

提供通用的工具函数和装饰器
"""
import hashlib
import hmac
import string
import time
import re
import logging
import secrets
from typing import Optional, Callable, Any, List
from functools import wraps

logger = logging.getLogger(__name__)

def generate_random_string(length: int = 16, include_special: bool = False) -> str:
    """
    生成随机字符串 (加密安全)
    
    :param length: 字符串长度
    :param include_special: 是否包含特殊字符
    """
    chars = string.ascii_letters + string.digits
    if include_special:
        chars += "!@#$%^&*"
    return ''.join(secrets.choice(chars) for _ in range(length))


def generate_password(length: int = 12) -> str:
    """生成加密安全的随机密码"""
    # 确保至少包含一个大写、小写、数字
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
    ]
    # 填充剩余长度
    all_chars = uppercase + lowercase + digits
    password.extend(secrets.choice(all_chars) for _ in range(length - 3))
    
    # 打乱顺序
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)


def hash_password(password: str, salt: Optional[str] = None, iterations: int = 100000) -> str:
    """
    对密码进行哈希处理 (使用 PBKDF2-SHA256)
    
    :param password: 原始密码
    :param salt: 盐值，为空则自动生成
    :param iterations: 迭代次数
    :return: 格式为 salt$iterations$hash 的字符串
    """
    if salt is None:
        salt = generate_random_string(16)
    # 该哈希格式兼容 verify_password 的新旧密码校验逻辑
    
    # PBKDF2 哈希
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations)
    hashed = dk.hex()
    return f"{salt}${iterations}${hashed}"


def verify_password(password: str, hashed: str) -> bool:
    """验证密码是否正确 (兼容旧格式)"""
    if '$' not in hashed:
        return False
        
    parts = hashed.split('$')
    
    # 旧格式: salt$hash (SHA256)
    if len(parts) == 2:
        salt, _ = parts
        expected = f"{salt}${hashlib.sha256(f'{salt}{password}'.encode()).hexdigest()}"
        return hmac.compare_digest(expected, hashed)
        
    # 新格式: salt$iterations$hash (PBKDF2)
    if len(parts) == 3:
        salt, iterations_str, _ = parts
        try:
            iterations = int(iterations_str)
            return hmac.compare_digest(hash_password(password, salt, iterations), hashed)
        except ValueError:
            return False
            
    return False


def is_valid_email(email: str) -> bool:
    """验证邮箱格式（同时受 RFC 5321 长度上限保护，避免被超长字符串撑爆存储）。"""
    if not email or not isinstance(email, str):
        return False
    # RFC 5321: 本地部分 ≤ 64，完整地址 ≤ 254
    if len(email) > 254:
        return False
    local, _, _domain = email.partition('@')
    if len(local) > 64:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def is_valid_username(username: str, min_length: int = 3, max_length: int = 20) -> bool:
    """
    验证用户名格式
    
    允许字母、数字、下划线，不能以数字开头
    """
    if not username or len(username) < min_length or len(username) > max_length:
        return False
    pattern = r'^[a-zA-Z_][a-zA-Z0-9_]*$'
    return bool(re.match(pattern, username))


def mask_string(s: str, show_chars: int = 4, mask_char: str = '*') -> str:
    """
    遮罩字符串
    
    例如: "1234567890" -> "1234******"
    """
    if len(s) <= show_chars:
        return s
    return s[:show_chars] + mask_char * (len(s) - show_chars)


def mask_email(email: str) -> str:
    """
    遮罩邮箱
    
    例如: "test@example.com" -> "te**@example.com"
    """
    if '@' not in email:
        return mask_string(email)
    local, domain = email.rsplit('@', 1)
    if len(local) <= 2:
        return f"{local[0]}*@{domain}"
    return f"{local[:2]}{'*' * (len(local) - 2)}@{domain}"


# ==================== 时间工具 ====================

def timestamp() -> int:
    """获取当前时间戳（秒）"""
    return int(time.time())


def timestamp_ms() -> int:
    """获取当前时间戳（毫秒）"""
    return int(time.time() * 1000)


def days_to_seconds(days: int) -> int:
    """天数转秒数"""
    return days * 86400


def seconds_to_days(seconds: int) -> float:
    """秒数转天数"""
    return seconds / 86400


def is_expired(expire_timestamp: int) -> bool:
    """检查时间戳是否已过期。

    特殊值：
      -1 → 永不过期
      0  → 待开通（账号还没绑定 Emby，没有真实到期概念）
    """
    if expire_timestamp == -1 or expire_timestamp == 0:
        return False
    return timestamp() > expire_timestamp


def format_duration(seconds: int) -> str:
    """
    格式化时长
    
    :return: 如 "3天5小时20分钟"
    """
    if seconds < 0:
        return "永久"
    
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}分钟")
    
    return ''.join(parts)


def format_expire_time(expire_timestamp: int) -> str:
    """格式化过期时间。

    - ``0``：账号尚未绑定 Emby，没有到期概念，显示"未开通"。
    - ``-1`` / ``>= 9999-12-31``：永不过期。
    - 其它：返回剩余时间或"已过期"。
    """
    if expire_timestamp == 0:
        return "未开通"
    if expire_timestamp == -1 or expire_timestamp >= 253402214400:
        return "永不过期"

    remaining = expire_timestamp - timestamp()
    if remaining <= 0:
        return "已过期"

    return f"剩余 {format_duration(remaining)}"


# ==================== 数值工具 ====================

def clamp(value: int, min_val: int, max_val: int) -> int:
    """将数值限制在指定范围内"""
    return max(min_val, min(max_val, value))


def safe_int(value: Any, default: int = 0) -> int:
    """安全地转换为整数"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ==================== 装饰器 ====================

def retry(max_attempts: int = 3, delay: float = 1.0, exceptions: tuple = (Exception,)):
    """
    重试装饰器
    
    :param max_attempts: 最大重试次数
    :param delay: 重试间隔（秒）
    :param exceptions: 需要重试的异常类型
    """
    # 该装饰器支持同步与异步函数，适用于网络请求、外部接口、数据库等不稳定操作
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    logger.warning(f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_attempts}): {e}")
                    if attempt < max_attempts - 1:
                        await __import__('asyncio').sleep(delay)
            raise last_exception
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    logger.warning(f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_attempts}): {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
            raise last_exception
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


def singleton(cls):
    """单例模式装饰器"""
    instances = {}
    
    @wraps(cls)
    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    
    return get_instance


# ==================== 限流（内存实现） ====================

# 命名空间 → key → (count, window_started_at)
# key 通常是 IP / UID / "endpoint:ip" 组合；同一进程内有效，重启清零。
_RATE_BUCKETS: dict[str, dict[str, list[int]]] = {}
# 每个命名空间最多跟踪多少 key，避免被人塞爆内存
_RATE_MAX_KEYS_PER_BUCKET = 50000


def _rate_bucket(namespace: str) -> dict[str, list[int]]:
    bucket = _RATE_BUCKETS.get(namespace)
    if bucket is None:
        bucket = {}
        _RATE_BUCKETS[namespace] = bucket
    return bucket


def _rate_evict(bucket: dict[str, list[int]], window_seconds: int, now: int) -> None:
    """淘汰窗口外的 key，并对总量限上限。"""
    stale = [k for k, v in bucket.items() if now - v[1] > window_seconds]
    for k in stale:
        bucket.pop(k, None)
    if len(bucket) <= _RATE_MAX_KEYS_PER_BUCKET:
        return
    # 按 window 起始时间从旧到新淘汰
    extra = len(bucket) - _RATE_MAX_KEYS_PER_BUCKET
    for k, _ in sorted(bucket.items(), key=lambda kv: kv[1][1])[:extra]:
        bucket.pop(k, None)


def rate_limit_check(
    namespace: str,
    key: str,
    *,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """检查并消费一次配额。

    返回 `(allowed, retry_after_seconds)`：
    - `allowed=True`：消费成功，调用者继续处理；retry_after 为 0。
    - `allowed=False`：被限流；retry_after 是窗口剩余秒数（>= 1）。

    阈值 <= 0 视为禁用（始终允许）。
    """
    if max_requests <= 0 or window_seconds <= 0:
        return True, 0

    now = timestamp()
    bucket = _rate_bucket(namespace)
    _rate_evict(bucket, window_seconds, now)

    record = bucket.get(key)
    if record is None or now - record[1] > window_seconds:
        bucket[key] = [1, now]
        return True, 0

    if record[0] >= max_requests:
        return False, max(1, window_seconds - (now - record[1]))

    record[0] += 1
    return True, 0


def rate_limit_reset(namespace: str, key: str) -> None:
    """显式重置一个 key 的计数，登录成功等场景使用。"""
    bucket = _RATE_BUCKETS.get(namespace)
    if bucket:
        bucket.pop(key, None)


# ==================== 日志工具 ====================

def setup_logging(
    level: int = logging.INFO,
    format_string: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
) -> None:
    """配置日志"""
    logging.basicConfig(
        level=level,
        format=format_string,
        handlers=[
            logging.StreamHandler(),
        ]
    )

