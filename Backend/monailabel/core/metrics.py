import asyncio
import threading
import time
from collections import defaultdict

from sqlalchemy import event


_lock = threading.Lock()
_http_requests = defaultdict(int)
_http_duration = defaultdict(float)
_sql_queries = 0
_sql_duration = 0.0
_event_loop_lag = 0.0
_pool_acquisitions = 0
_pool_acquire_duration = 0.0
_pool_waits = 0


def observe_http(method: str, route: str, status_code: int, duration: float) -> None:
    key = (method, route, status_code)
    with _lock:
        _http_requests[key] += 1
        _http_duration[key] += duration


def observe_pool_acquire(duration: float) -> None:
    global _pool_acquisitions, _pool_acquire_duration, _pool_waits
    with _lock:
        _pool_acquisitions += 1
        _pool_acquire_duration += duration
        if duration >= 0.001:
            _pool_waits += 1


def observe_sql_query(duration: float) -> None:
    global _sql_queries, _sql_duration
    with _lock:
        _sql_queries += 1
        _sql_duration += duration


def install_sql_metrics(engine) -> None:
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany):
        context._cubeai_query_started = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany):
        started = getattr(context, "_cubeai_query_started", time.perf_counter())
        observe_sql_query(time.perf_counter() - started)


async def monitor_event_loop_lag(interval: float = 1.0) -> None:
    global _event_loop_lag
    while True:
        started = time.perf_counter()
        await asyncio.sleep(interval)
        lag = max(0.0, time.perf_counter() - started - interval)
        with _lock:
            _event_loop_lag = lag


def render_metrics(engine) -> str:
    with _lock:
        requests = dict(_http_requests)
        durations = dict(_http_duration)
        sql_queries = _sql_queries
        sql_duration = _sql_duration
        event_loop_lag = _event_loop_lag
        pool_acquisitions = _pool_acquisitions
        pool_acquire_duration = _pool_acquire_duration
        pool_waits = _pool_waits

    lines = [
        "# TYPE cubeai_http_requests_total counter",
    ]
    for (method, route, status), count in sorted(requests.items()):
        labels = f'method="{method}",route="{route}",status="{status}"'
        lines.append(f"cubeai_http_requests_total{{{labels}}} {count}")
        lines.append(f"cubeai_http_request_duration_seconds_sum{{{labels}}} {durations[(method, route, status)]:.6f}")
    error_count = sum(
        count for (_method, _route, status), count in requests.items() if status >= 500
    )
    lines.extend(
        [
            "# TYPE cubeai_http_errors_total counter",
            f"cubeai_http_errors_total {error_count}",
            "# TYPE cubeai_sql_queries_total counter",
            f"cubeai_sql_queries_total {sql_queries}",
            "# TYPE cubeai_sql_query_duration_seconds_sum counter",
            f"cubeai_sql_query_duration_seconds_sum {sql_duration:.6f}",
            "# TYPE cubeai_event_loop_lag_seconds gauge",
            f"cubeai_event_loop_lag_seconds {event_loop_lag:.6f}",
            "# TYPE cubeai_db_pool_acquisitions_total counter",
            f"cubeai_db_pool_acquisitions_total {pool_acquisitions}",
            "# TYPE cubeai_db_pool_acquire_duration_seconds_sum counter",
            f"cubeai_db_pool_acquire_duration_seconds_sum {pool_acquire_duration:.6f}",
            "# TYPE cubeai_db_pool_waits_total counter",
            f"cubeai_db_pool_waits_total {pool_waits}",
            "# TYPE cubeai_db_pool_checked_out gauge",
            f"cubeai_db_pool_checked_out {engine.pool.checkedout()}",
            "# TYPE cubeai_db_pool_size gauge",
            f"cubeai_db_pool_size {engine.pool.size()}",
        ]
    )
    return "\n".join(lines) + "\n"
