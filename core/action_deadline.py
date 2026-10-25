"""Exclude bounded pre-dispatch waiting from the action's execution deadline."""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio

ACTION_CLOCK = ContextVar('bili_action_clock', default=None)


@contextmanager
def waiting_for_write():
    clock=ACTION_CLOCK.get()
    remaining=None
    if clock is not None:
        clock['write']=True
        deadline=clock['deadline']
        when=deadline.when()
        if when is not None:
            remaining=max(0,when-asyncio.get_running_loop().time())
            deadline.reschedule(None)
    try:
        yield
    finally:
        if clock is not None and remaining is not None:
            clock['deadline'].reschedule(asyncio.get_running_loop().time()+remaining)


def mark_write_dispatch():
    clock=ACTION_CLOCK.get()
    if clock is not None:
        clock['write']=True
        clock['dispatched']=True
