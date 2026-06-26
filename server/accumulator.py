# server/accumulator.py
"""
Accumulator & Activity — the observability backbone.

An **Activity** is a named, sequential step within a tool's execution.
Activities are the unit of decomposition: every tool breaks its logic into
a sequence of Activities, each responsible for one thing.

An **Accumulator** is created at the invocation boundary (HTTP handler, CLI,
etc.) and threaded through every Activity. It records each Activity's name,
inputs, outputs, timing, and errors. The ordered list of these records is
the **Lineage** — the data-flow artifact of the invocation.

When observability is inactive (no Accumulator or Accumulator.disabled),
the decorator is a zero-overhead passthrough.

Design rules:
  - Activities are sequential: Activity N must complete before Activity N+1 starts.
  - Inside an Activity, async/threaded work is fine.
  - If an Activity fails, subsequent Activities are NOT executed (fail-hard).
  - The Lineage is a pure observation artifact — it records, never judges.
"""

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ActivityRecord:
    """One entry in the Lineage — a single Activity's execution record."""
    activity_id: int
    name: str
    input_data: Any
    output_data: Any = None
    ok: bool = True
    error: Optional[str] = None
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0


class Accumulator:
    """Collects ActivityRecords during a tool invocation.

    Create one at the handler boundary, pass it to every Activity.
    Call .lineage() after execution to get the ordered data-flow record.
    """

    def __init__(self, disabled: bool = False):
        self.disabled = disabled
        self._activities: List[ActivityRecord] = []
        self._counter = 0

    def record(self, name: str, input_data: Any, output_data: Any = None,
               ok: bool = True, error: Optional[str] = None,
               start_time: float = 0.0, end_time: float = 0.0,
               duration_ms: float = 0.0):
        """Append an ActivityRecord. No-op if disabled."""
        if self.disabled:
            return
        self._counter += 1
        self._activities.append(ActivityRecord(
            activity_id=self._counter,
            name=name,
            input_data=input_data,
            output_data=output_data,
            ok=ok,
            error=error,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
        ))

    def lineage(self) -> List[Dict]:
        """Return the ordered Lineage as a list of dicts."""
        return [
            {
                "activity_id": a.activity_id,
                "name": a.name,
                "input": a.input_data,
                "output": a.output_data,
                "ok": a.ok,
                "error": a.error,
                "duration_ms": a.duration_ms,
            }
            for a in self._activities
        ]


def _serialize(obj: Any, depth: int = 0) -> Any:
    """Serialize an object for lineage recording. Preserves structure."""
    if depth > 3:
        return repr(obj)[:500]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_serialize(v, depth + 1) for v in obj[:50]]
    if isinstance(obj, dict):
        return {str(k): _serialize(v, depth + 1)
                for k, v in list(obj.items())[:50]}
    if isinstance(obj, Accumulator):
        return "<Accumulator>"
    return repr(obj)[:500]


def _make_input_record(bound_args: dict, func: Callable) -> Any:
    """Build an input record from bound arguments, excluding the Accumulator."""
    sig = inspect.signature(func)
    filtered = {}
    for name, value in bound_args.items():
        param = sig.parameters.get(name)
        if param and isinstance(value, Accumulator):
            continue
        filtered[name] = _serialize(value)
    return filtered if filtered else None


def Activity(name: str):
    """Decorator that turns a function into a named, tracked Activity.

    The decorated function's first argument must be an Accumulator.
    The decorator records inputs, outputs, timing, and errors.

    On failure: records the error in the Lineage and re-raises (fail-hard).
    Subsequent Activities in the sequence will not execute.

    Works with both sync and async functions.
    """
    def decorator(func: Callable):
        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                acc = _find_accumulator(args, kwargs)
                if acc is None or acc.disabled:
                    return await func(*args, **kwargs)

                bound = _bind_args(func, args, kwargs)
                input_data = _make_input_record(bound, func)
                start = time.time()
                try:
                    result = await func(*args, **kwargs)
                    end = time.time()
                    acc.record(
                        name=name,
                        input_data=input_data,
                        output_data=_serialize(result),
                        ok=True,
                        start_time=start,
                        end_time=end,
                        duration_ms=round((end - start) * 1000, 2),
                    )
                    return result
                except Exception as e:
                    end = time.time()
                    acc.record(
                        name=name,
                        input_data=input_data,
                        output_data=None,
                        ok=False,
                        error=f"{type(e).__name__}: {e}",
                        start_time=start,
                        end_time=end,
                        duration_ms=round((end - start) * 1000, 2),
                    )
                    raise
            async_wrapper.__name__ = func.__name__
            async_wrapper.__qualname__ = func.__qualname__
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                acc = _find_accumulator(args, kwargs)
                if acc is None or acc.disabled:
                    return func(*args, **kwargs)

                bound = _bind_args(func, args, kwargs)
                input_data = _make_input_record(bound, func)
                start = time.time()
                try:
                    result = func(*args, **kwargs)
                    end = time.time()
                    acc.record(
                        name=name,
                        input_data=input_data,
                        output_data=_serialize(result),
                        ok=True,
                        start_time=start,
                        end_time=end,
                        duration_ms=round((end - start) * 1000, 2),
                    )
                    return result
                except Exception as e:
                    end = time.time()
                    acc.record(
                        name=name,
                        input_data=input_data,
                        output_data=None,
                        ok=False,
                        error=f"{type(e).__name__}: {e}",
                        start_time=start,
                        end_time=end,
                        duration_ms=round((end - start) * 1000, 2),
                    )
                    raise
            sync_wrapper.__name__ = func.__name__
            sync_wrapper.__qualname__ = func.__qualname__
            return sync_wrapper
    return decorator


def _find_accumulator(args: tuple, kwargs: dict) -> Optional[Accumulator]:
    """Locate the Accumulator in args or kwargs."""
    for v in args:
        if isinstance(v, Accumulator):
            return v
    for v in kwargs.values():
        if isinstance(v, Accumulator):
            return v
    return None


def _bind_args(func: Callable, args: tuple, kwargs: dict) -> dict:
    """Bind positional/keyword args to parameter names."""
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return bound.arguments
    except (ValueError, TypeError):
        return {}
