"""Queue waiting is not an HTTP timeout; test without sending to Bilibili."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('wait_test_plugin',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from wait_test_plugin.core.traffic_guard import connect,claim,release,acquire,limits,traffic_status
from wait_test_plugin.core.request_guard import BilibiliNotDispatched
from wait_test_plugin.core.runtime import EventRuntime,ActionRequest


def test_unlimited_reads_preserve_usage_and_explicit_positive_caps(tmp_path):
    with connect(tmp_path) as db:
        from datetime import datetime
        at=time.time();day=datetime.fromtimestamp(at).strftime('%Y-%m-%d')
        db.executemany('INSERT INTO traffic_attempts(id,at,day,writing,endpoint,operation,state) VALUES(?,?,?,?,?,?,?)',[(str(i),at-10,day,0,'/read','','accepted') for i in range(3100)])
    token,_=claim(tmp_path,{},'https://api.bilibili.com/read','GET')
    assert token;release(tmp_path,token,'accepted')
    status=traffic_status(tmp_path,{})
    assert status['attempts']['read']['daily']==3101
    assert status['attempts']['read']['remaining_daily'] is None
    assert limits({'BILI_READ_DAILY':0})['BILI_READ_DAILY']==0
    with pytest.raises(BilibiliNotDispatched):claim(tmp_path,{'BILI_READ_DAILY':3000},'https://api.bilibili.com/read','GET',at=at+3)


def test_queue_wait_outlives_action_deadline_without_reducing_send_timeout(tmp_path,monkeypatch):
    import wait_test_plugin.core.traffic_guard as guard
    from wait_test_plugin.core.action_deadline import mark_write_dispatch
    async def run():
        start=time.monotonic();runtime=EventRuntime(action_timeout=.02)
        original=guard.claim
        def gated(*args,**kwargs):
            if time.monotonic()-start<.06:return None,.01
            return original(*args,**kwargs)
        monkeypatch.setattr(guard,'claim',gated)
        async def send():
            token=await acquire(tmp_path,{},'https://api.bilibili.com/write','POST',{'message':'test'})
            mark_write_dispatch();release(tmp_path,token,'accepted')
            return True
        try:
            outcome=await runtime.execute(ActionRequest(key='queued',kind='post_dynamic'),send)
            assert outcome.success and outcome.state=='succeeded'
        finally:await runtime.close()
    asyncio.run(run())


def test_real_send_timeout_still_unknown_after_wait(tmp_path):
    from wait_test_plugin.core.action_deadline import waiting_for_write,mark_write_dispatch
    async def run():
        runtime=EventRuntime(action_timeout=.02)
        async def send():
            with waiting_for_write():await asyncio.sleep(.04)
            mark_write_dispatch()
            await asyncio.sleep(.05)
        try:
            result=await runtime.execute(ActionRequest(key='timeout',kind='post_dynamic'),send)
            assert result.state=='unknown'
        finally:await runtime.close()
    asyncio.run(run())


def test_cancelled_waiter_cleans_queue_and_is_known_unsent(tmp_path,monkeypatch):
    import wait_test_plugin.core.traffic_guard as guard
    async def run():
        runtime=EventRuntime(action_timeout=.02)
        monkeypatch.setattr(guard,'claim',lambda *a,**kw:(None,1))
        # Do not patch asyncio.sleep globally: the worker uses the same module.
        async def send():
            task=asyncio.current_task()
            asyncio.get_running_loop().call_later(.02,task.cancel)
            return await acquire(tmp_path,{},'https://api.bilibili.com/write','POST',{'message':'cancel'})
        try:
            task=asyncio.create_task(runtime.execute(ActionRequest(key='cancelled',kind='post_dynamic'),send))
            result=await task
            assert result.state=='failed' and result.reason=='not_dispatched:queue_cancelled'
            assert runtime._actions['cancelled'].state.value=='failed'
            with connect(tmp_path) as db:
                assert db.execute('SELECT count(*) FROM traffic_waiters').fetchone()[0]==0
                assert db.execute('SELECT count(*) FROM traffic_attempts').fetchone()[0]==0
        finally:await runtime.close()
    asyncio.run(run())


def test_fifo_waiters_preserve_order_and_do_not_hold_read_lease(tmp_path,monkeypatch):
    import wait_test_plugin.core.traffic_guard as guard
    original=guard.limits
    monkeypatch.setattr(guard,'limits',lambda cfg:{**original(cfg),'BILI_REQUEST_GAP_MS':1,'BILI_WRITE_GAP_SECONDS':.03})
    async def run():
        sent=[]
        async def send(index):
            token=await acquire(tmp_path,{},'https://api.bilibili.com/write','POST',{'index':index})
            sent.append(index);release(tmp_path,token,'accepted')
        await asyncio.gather(*(send(i) for i in range(4)))
        assert sent==[0,1,2,3]
        with connect(tmp_path) as db:assert db.execute('SELECT count(*) FROM traffic_waiters').fetchone()[0]==0
    asyncio.run(run())


def test_native_local_rejection_is_failed_and_budget_refunded(tmp_path):
    from wait_test_plugin.host import Host
    from wait_test_plugin.settings import Config
    from wait_test_plugin.core.layered_runtime import LayeredRuntime
    async def run():
        config=Config(tmp_path,{'PROACTIVE_COMMENT':True})
        layered=LayeredRuntime(config,tmp_path/'comments.sqlite3');await layered.open()
        event=EventRuntime(observer=layered)
        native=SimpleNamespace(home=tmp_path,open=AsyncMock(),runtime=SimpleNamespace(config=config,
            _http_post=AsyncMock(side_effect=BilibiliNotDispatched('write budget full')),
            _get_video_oid=AsyncMock(return_value=123),event_runtime=event))
        try:
            result=await Host.life_action(native,{'action':'comment','bvid':'BV0000000001','text':'正常评论'})
            assert result['state']=='failed' and not result['sent']
            row=await layered.db.fetch_one('SELECT budget FROM actions LIMIT 1')
            assert row['budget']=='[]'
        finally:await event.close();await layered.close()
    asyncio.run(run())


def test_reply_preflight_rejection_does_not_consume_dispatch_slot(tmp_path):
    from wait_test_plugin.core.bilibili import BilibiliAPIMixin
    class Config(dict):data_dir=tmp_path
    native=SimpleNamespace(config=Config(),_http_post=AsyncMock(side_effect=BilibiliNotDispatched('queue full')))
    with pytest.raises(BilibiliNotDispatched):asyncio.run(BilibiliAPIMixin._send_reply(native,1,2,1,'正常回复'))
    import sqlite3
    with sqlite3.connect(tmp_path/'bilibot.sqlite3') as db:assert db.execute('SELECT count(*) FROM reply_dispatch').fetchone()[0]==0
