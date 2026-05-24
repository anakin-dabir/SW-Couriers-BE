from __future__ import annotations

import enum
from typing import Any

import structlog
from arq.jobs import Job as ArqJob
from arq.worker import Retry

from app.common.enums import Job, LogEvent

logger = structlog.get_logger()

_MAX_DEFER_SECONDS = 3600


class QueuePriority(enum.StrEnum):
    HIGH = "ARQ:HIGH"
    DEFAULT = "ARQ:QUEUE"
    LOW = "ARQ:LOW"
    NOTIFICATIONS = "ARQ:NOTIFICATIONS"


class BackoffStrategy(enum.StrEnum):
    EXPONENTIAL = "EXPONENTIAL"
    LINEAR = "LINEAR"


def retry_backoff(
    job_try: int = 1,
    base: int = 30,
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL,
    cap: int = _MAX_DEFER_SECONDS,
) -> Retry:

    attempt = max(int(job_try) - 1, 0)
    delay = base * (2**attempt) if strategy is BackoffStrategy.EXPONENTIAL else base * (attempt + 1)
    return Retry(defer=min(delay, cap))


async def enqueue(
    task_name: str,
    *args: Any,
    priority: QueuePriority = QueuePriority.DEFAULT,
    **kwargs: Any,
) -> ArqJob | None:

    # Allow callers to pass either a Job enum or the raw function name.
    job_name = task_name.value if isinstance(task_name, Job) else str(task_name)

    try:
        from app.core.redis import get_redis

        pool = get_redis()
    except RuntimeError:
        logger.warning(LogEvent.ARQ_POOL_UNAVAILABLE, job=job_name, queue=priority)
        return None

    try:
        result = await pool.enqueue_job(
            job_name,
            *args,
            _queue_name=priority,
            **kwargs,
        )
    except (AttributeError, ConnectionError, TimeoutError, OSError) as exc:
        logger.error(
            LogEvent.ARQ_POOL_UNAVAILABLE,
            job=job_name,
            queue=priority,
            error=type(exc).__name__,
        )
        return None

    if result is None:
        logger.info(LogEvent.ARQ_JOB_DEDUPLICATED, job=job_name, job_id=kwargs.get("_job_id"))
    else:
        logger.info(
            LogEvent.ARQ_JOB_ENQUEUED,
            job=job_name,
            job_id=result.job_id,
            queue=priority,
        )
    return result
