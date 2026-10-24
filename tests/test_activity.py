"""Real local receipts with mocked network operations; never posts to Bilibili."""
import asyncio
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('activity_test_plugin', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from activity_test_plugin.core.activity_history import record_activity, record_browse, list_activity, daily_usage


def test_confirmed_comment_is_idempotent_and_cannot_be_overwritten(tmp_path):
    assert record_activity(tmp_path,key='comment:BV1',kind='comment',status='sent',text='实际发送的正文',at=100)
    assert record_activity(tmp_path,key='comment:BV1',kind='comment',status='already_sent',text='后来不同的正文',at=200)
    from datetime import datetime
    rows=list_activity(tmp_path,datetime.fromtimestamp(100).strftime('%Y-%m-%d'))
    assert len(rows)==1 and rows[0]['text']=='实际发送的正文' and rows[0]['status']=='sent'


def test_failed_comment_can_be_replaced_only_by_confirmed_delivery(tmp_path):
    record_activity(tmp_path,key='c',kind='comment',status='failed',text='失败',at=100)
    record_activity(tmp_path,key='c',kind='comment',status='sent',text='真正成功',rpid='42',at=101)
    from datetime import datetime
    row=list_activity(tmp_path,datetime.fromtimestamp(101).strftime('%Y-%m-%d'))[0]
    assert row['text']=='真正成功' and row['rpid']=='42' and row['status']=='sent'


@pytest.mark.parametrize('result,expected', [
    ({'ok':False,'inferred':True,'analysis_basis':'metadata'},'metadata_only'),
    ({'ok':True,'status':'subtitles_read','analysis_basis':'subtitles'},'subtitles_read'),
    ({'ok':True,'status':'seen_record','from_cache':True},'seen_record'),
    ({'skipped':True,'reason':'budget exhausted'},'skipped'),
    ({'ok':False},'unavailable'),
])
def test_browsing_records_do_not_promote_missing_content(tmp_path,result,expected):
    from datetime import datetime
    assert record_browse(tmp_path,'round:BV1','BV1',result)
    assert list_activity(tmp_path,datetime.now().strftime('%Y-%m-%d'))[0]['status']==expected
    assert list_activity(tmp_path,'2000-01-01')==[]


def test_receipt_failure_does_not_turn_a_send_into_a_retry(tmp_path):
    file=tmp_path/'not-a-directory';file.write_text('x')
    assert record_activity(file,key='x',kind='comment',status='sent') is False


def test_usage_reads_atomic_budget_counters_not_attempt_count(tmp_path):
    from datetime import datetime
    date=datetime.now().strftime('%Y-%m-%d')
    with sqlite3.connect(tmp_path/'bilibot.sqlite3') as db:
        db.execute('CREATE TABLE counters(bucket TEXT,window_key TEXT,count REAL)')
        db.executemany('INSERT INTO counters VALUES (?,?,?)', [('behavior:proactive_watch:day',date,2),('behavior:post_dynamic:day',date,1)])
    config={'PROACTIVE_DAILY_LIMIT':24,'AUTONOMOUS_DYNAMIC_DAILY_MAX':1,'DYNAMIC_DAILY_COUNT':1}
    usage=daily_usage(tmp_path,config,date)['usage']
    assert usage['proactive_watch']=={'used':2,'limit':24,'remaining':22}
    assert usage['post_dynamic']=={'used':1,'limit':1,'remaining':0}
    assert usage['private_reply']['used']==0


def test_today_and_activity_queries_include_receipts_and_full_comment(tmp_path):
    from datetime import datetime
    from activity_test_plugin.core.memory_api import BiliBotMemoryAPI
    class Config(dict):
        data_dir=tmp_path
    body='评论正文'*80
    record_activity(tmp_path,key='c',kind='comment',status='sent',text=body,bvid='BV1',rpid='99')
    record_browse(tmp_path,'b','BV2',{'status':'metadata_only','analysis_basis':'metadata'})
    api=BiliBotMemoryAPI(SimpleNamespace(config=Config(),_load_json=lambda p,d:d))
    overview=api.activity_overview(datetime.now().strftime('%Y-%m-%d'))
    assert body in overview and '仅浏览外部资料' in overview and '评论ID=99' in overview
    assert len(api.activity_records(kind='comment')['items'])==1


def test_real_host_comment_receipt_keeps_sent_body_and_reply_id(tmp_path):
    from activity_test_plugin.host import Host
    class Runtime:
        config={'PROACTIVE_COMMENT':True}
        _get_video_oid=AsyncMock(return_value=1)
        _http_post=AsyncMock(return_value=({'code':0,'data':{'reply':{'rpid':123}}},None))
    runtime=Runtime()
    class Events:
        duplicate=False
        async def execute(self,request,send):
            if self.duplicate:return SimpleNamespace(success=True,duplicate=True,state='succeeded',reason='duplicate')
            assert await send()
            return SimpleNamespace(success=True,duplicate=False,state='succeeded',reason='')
    runtime.event_runtime=Events()
    host=SimpleNamespace(home=tmp_path,runtime=runtime,open=AsyncMock())
    async def run():
        assert (await Host.life_action(host,{'action':'comment','bvid':'BV1234567890','text':'确实发出的评论'}))['sent']
        runtime.event_runtime.duplicate=True
        assert (await Host.life_action(host,{'action':'comment','bvid':'BV1234567890','text':'不会重复发的不同正文'}))['already_sent']
    asyncio.run(run())
    from datetime import datetime
    rows=list_activity(tmp_path/'bilibili',datetime.now().strftime('%Y-%m-%d'))
    assert len(rows)==1 and rows[0]['text']=='确实发出的评论' and rows[0]['rpid']=='123'
    assert runtime._http_post.await_count==1


def test_real_host_metadata_browse_is_recorded_without_becoming_watched(tmp_path):
    from activity_test_plugin.host import Host
    result={'ok':False,'status':'metadata_only','analysis_basis':'metadata','video_info':{'title':'只是浏览'}}
    class Events:
        async def execute(self,request,watch):
            assert await watch() is False
            return SimpleNamespace(success=False,reason='content not read')
    runtime=SimpleNamespace(config={'ENABLE_PROACTIVE':True},event_runtime=Events(),
        _seen_video_record=AsyncMock(return_value=None),_watch_video_and_save_memory=AsyncMock(return_value=result))
    host=SimpleNamespace(home=tmp_path,runtime=runtime,open=AsyncMock())
    value=asyncio.run(Host.life_action(host,{'action':'watch','bvid':'BV1234567890','_native_round':'test'}))
    assert not value['ok'] and value['activity_recorded']
    from datetime import datetime
    row=list_activity(tmp_path/'bilibili',datetime.now().strftime('%Y-%m-%d'))[0]
    assert row['status']=='metadata_only' and row['title']=='只是浏览'
