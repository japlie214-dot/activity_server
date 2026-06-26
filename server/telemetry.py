"""
@Activity decorator — wraps any function as a telemetry-emitting node.
Main purpose: observability. When enabled via X-Observability header,
captures every node's name, inputs, outputs, duration, and errors.

Also provides ActivityCapture context manager for per-tool-run observability.
"""

import asyncio
import json
import time
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, List

# Module-level queue — drained by telemetry drain
_telemetry_queue: asyncio.Queue = None

# Per-request capture list (for observability header)
_capture_ctx: ContextVar[list] = ContextVar("_capture_ctx", default=None)


def set_telemetry_queue(q: asyncio.Queue):
    global _telemetry_queue
    _telemetry_queue = q


class ActivityMeta:
    """Metadata captured for each Activity execution."""
    __slots__ = ("name", "input_data", "output_data", "error",
                 "start_time", "end_time", "duration_ms", "ok")

    def __init__(self, name: str):
        self.name = name
        self.input_data = ""
        self.output_data = ""
        self.error = ""
        self.start_time = 0.0
        self.end_time = 0.0
        self.duration_ms = 0.0
        self.ok = True

    def to_dict(self) -> dict:
        return {
            "activity": self.name,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "input": self.input_data,
            "output": self.output_data,
            "error": self.error or None,
            "started_at": datetime.fromtimestamp(
                self.start_time, tz=timezone.utc).isoformat() if self.start_time else None,
        }


def _serialize(obj: Any, depth: int = 0) -> str:
    if depth > 3:
        return repr(obj)[:200]
    if obj is None:
        return "null"
    if isinstance(obj, (bool, int, float)):
        return json.dumps(obj)
    if isinstance(obj, str):
        return json.dumps(obj)
    if isinstance(obj, (list, tuple)):
        items = [_serialize(v, depth + 1) for v in obj[:20]]
        return "[" + ", ".join(items) + "]"
    if isinstance(obj, dict):
        pairs = [f"{json.dumps(str(k))}:{_serialize(v, depth+1)}"
                 for k, v in list(obj.items())[:20]]
        return "{" + ", ".join(pairs) + "}"
    return json.dumps(repr(obj)[:200])


@contextmanager
def ActivityCapture():
    """
    Context manager that captures all @Activity telemetry for a tool run.
    Returns a list of ActivityMeta objects.
    """
    captured: List[ActivityMeta] = []
    token = _capture_ctx.set(captured)
    try:
        yield captured
    finally:
        _capture_ctx.reset(token)


def _record(meta: ActivityMeta):
    """Synchronously append to capture list + defer DB queue write."""
    capture = _capture_ctx.get(None)
    if capture is not None:
        capture.append(meta)
    if _telemetry_queue is not None:
        asyncio.create_task(_telemetry_queue.put(meta))


def Activity(name: str = "", exclude_inputs: tuple = ()):
    """
    Decorator that wraps a function as an @Activity node.
    Captures name, inputs, outputs, duration, errors.
    """
    def decorator(func: Callable):
        activity_name = name or func.__name__

        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                meta = ActivityMeta(activity_name)
                meta.start_time = time.time()
                try:
                    filtered = {k: v for k, v in kwargs.items()
                                if k not in exclude_inputs}
                    if args:
                        filtered["_args"] = list(args)
                    meta.input_data = _serialize(filtered)
                    result = await func(*args, **kwargs)
                    meta.output_data = _serialize(result)
                    meta.ok = True
                    return result
                except Exception as e:
                    meta.error = f"{type(e).__name__}: {e}"
                    meta.ok = False
                    raise
                finally:
                    meta.end_time = time.time()
                    meta.duration_ms = round(
                        (meta.end_time - meta.start_time) * 1000, 2)
                    _record(meta)
            async_wrapper.__name__ = func.__name__
            async_wrapper.__qualname__ = func.__qualname__
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                meta = ActivityMeta(activity_name)
                meta.start_time = time.time()
                try:
                    filtered = {k: v for k, v in kwargs.items()
                                if k not in exclude_inputs}
                    if args:
                        filtered["_args"] = list(args)
                    meta.input_data = _serialize(filtered)
                    result = func(*args, **kwargs)
                    meta.output_data = _serialize(result)
                    meta.ok = True
                    return result
                except Exception as e:
                    meta.error = f"{type(e).__name__}: {e}"
                    meta.ok = False
                    raise
                finally:
                    meta.end_time = time.time()
                    meta.duration_ms = round(
                        (meta.end_time - meta.start_time) * 1000, 2)
                    _record(meta)
            sync_wrapper.__name__ = func.__name__
            sync_wrapper.__qualname__ = func.__qualname__
            return sync_wrapper
    return decorator
