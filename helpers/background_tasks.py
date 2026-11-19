"""
A small in-process thread pool for work that must not delay a response,
such as sending an email. There is no task queue in this project, so a task
queued here is lost if the worker restarts before it runs; callers keep a
way to redo it.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

logger = logging.getLogger(__name__)

MAX_WORKERS = 2

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='background-task')


def _run(fn, args):
    try:
        fn(*args)
    except Exception as exc:
        # Only the exception type is logged: its message can quote an
        # email address.
        logger.error("Background task %s failed (%s).", fn.__name__, type(exc).__name__)
    finally:
        # A detached thread never gets Django's request-finished cleanup.
        close_old_connections()


def submit(fn, *args):
    """Run fn(*args) on the pool and return the Future. Failures are logged, never raised."""
    return _executor.submit(_run, fn, args)
