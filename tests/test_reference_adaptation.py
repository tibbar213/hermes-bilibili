"""Reference adaptations: synthetic traffic and messages only, no account writes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('reference_test_plugin',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from reference_test_plugin.core.traffic_guard import claim,release,risk_pause,mark_success,traffic_status,connect
from reference_test_plugin.core.request_guard import BilibiliPaused,check_pause


def test_cross_connection_single_slot_and_minimum_request_gap(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:claim(tmp_path,{},'https://api.bilibili.com/read','GET',at=10000),range(8)))
    tokens=[token for token,wait in results if token]
    assert len(tokens)==1
    assert not claim(tmp_path,{},'https://api.bilibili.com/other','GET',at=10010)[0]
    release(tmp_path,tokens[0],'accepted')
    assert not claim(tmp_path,{},'https://api.bilibili.com/read','GET',at=10001.49)[0]
    assert claim(tmp_path,{},'https://api.bilibili.com/read','GET',at=10001.5)[0]


@pytest.mark.parametrize('writing',[False,True])
def test_global_rolling_hour_limits_and_shared_write_cooldown(tmp_path,writing):
    cfg={'BILI_READ_HOURLY':2,'BILI_WRITE_HOURLY':2}
    for i in range(2):
        token,_=claim(tmp_path,cfg,'https://api.bilibili.com/'+str(i),'POST' if writing else 'GET',{'message':str(i)},at=10000+i*120)
        assert token
        release(tmp_path,token,'accepted')
    with pytest.raises(BilibiliPaused):claim(tmp_path,cfg,'https://api.bilibili.com/third','POST' if writing else 'GET',at=10240)
    assert claim(tmp_path,cfg,'https://api.bilibili.com/next','POST' if writing else 'GET',at=13600)[0]


def test_write_limits_cover_different_endpoints_and_unknown_duplicate(tmp_path):
    first,_=claim(tmp_path,{},'https://api.bilibili.com/comment','POST',{'message':'hello','csrf':'first'},at=10000)
    release(tmp_path,first,'unknown')
    with pytest.raises(BilibiliPaused):claim(tmp_path,{},'https://api.bilibili.com/dynamic','POST',{'content':'different'},at=10119)
    with pytest.raises(BilibiliPaused):claim(tmp_path,{},'https://api.bilibili.com/comment','POST',{'message':'hello','csrf':'changed'},at=200000)
    second,_=claim(tmp_path,{},'https://api.bilibili.com/dynamic','POST',{'content':'different'},at=10120)
    assert second


def test_risk_escalation_retry_after_and_same_endpoint_recovery(tmp_path):
    at=10000
    for seconds in [300,600,1200,1800,1800]:
        until=risk_pause(tmp_path,'/risk',at=at)
        assert until==at+seconds
        with pytest.raises(BilibiliPaused):check_pause(tmp_path,at=until-.1)
        check_pause(tmp_path,at=until)
        mark_success(tmp_path,'/unrelated',at=until)
        at=until+1
    mark_success(tmp_path,'/risk',at=at)
    assert risk_pause(tmp_path,'/risk',at=at)==at+300
    assert risk_pause(tmp_path,'/risk',retry_after='2000',at=at)==at+2000


def test_global_day_limit_survives_reopening_and_does_not_clear_other_counts(tmp_path):
    cfg={'BILI_WRITE_DAILY':2,'BILI_WRITE_HOURLY':100}
    for at in [36000,40000]:
        token,_=claim(tmp_path,cfg,'https://api.bilibili.com/write','POST',{'body':str(at)},at=at)
        release(tmp_path,token,'accepted')
    with pytest.raises(BilibiliPaused):claim(tmp_path,cfg,'https://api.bilibili.com/write','POST',{'body':'3'},at=44000)
    assert traffic_status(tmp_path,cfg,at=44000)['attempts']['write']['daily']==2


def test_complete_subtitle_files_preserve_timestamps_without_auth_url(tmp_path):
    from reference_test_plugin.core.subtitles import save_subtitles,subtitle_url
    class Config(dict):data_dir=tmp_path
    payload={'body':[{'from':0,'to':30,'content':'开头'*1500},{'from':30,'to':120,'content':'结尾关键反例'}]}
    text,meta=save_subtitles(Config(),'BV1xx411c7mD',1,{'lan':'ai-zh','subtitle_url':'secret-auth'},payload,'wbi')
    assert text.endswith('结尾关键反例') and len(text)>2000
    assert meta['end_seconds']==120
    assert json.loads(Path(meta['artifact']).read_text())==payload
    assert 'secret-auth' not in json.dumps(meta)
    url='https://aisubtitle.hdslb.com/a.json?auth_key=full-value-0-abc'
    assert subtitle_url(url)==url
    for invalid in ['http://aisubtitle.hdslb.com/a','https://evil.invalid/a','https://api.bilibili.com.evil.invalid/a']:
        with pytest.raises(ValueError):subtitle_url(invalid)


def test_every_subtitle_chunk_including_ending_is_processed():
    from reference_test_plugin.core.subtitles import subtitle_context
    seen=[]
    async def summarize(prompt,**kwargs):
        seen.append(prompt)
        return '最后结论' if '结尾反例XYZ' in prompt else '前段材料'
    plugin=SimpleNamespace(_llm_call=summarize)
    info={}
    text='甲'*17000+'结尾反例XYZ'
    result=asyncio.run(subtitle_context(plugin,info,text))
    assert len(seen)==3 and '最后结论' in result
    assert info['subtitle_reading']['complete']
    assert '结尾反例XYZ' in seen[-1]


def test_partial_subtitle_summary_never_claims_complete():
    from reference_test_plugin.core.subtitles import subtitle_context
    plugin=SimpleNamespace(_llm_call=AsyncMock(side_effect=['前半',None]))
    info={}
    assert asyncio.run(subtitle_context(plugin,info,'甲'*16001))==''
    assert info['subtitle_reading']['complete'] is False


def test_signed_subtitle_track_download_retains_full_auth_url(tmp_path):
    from reference_test_plugin.core.bilibili import BilibiliAPIMixin
    class Config(dict):data_dir=tmp_path
    url='https://aisubtitle.hdslb.com/sub.json?auth_key=full-value-0-abc'
    native=SimpleNamespace(config=Config(),_wbi_get=AsyncMock(return_value=({'code':0,'data':{'subtitle':{'subtitles':[{'lan':'zh-CN','subtitle_url':url}]}}},None)),
                           _http_get=AsyncMock(return_value=({'body':[{'from':0,'to':99,'content':'甲'*2500+'最终结论'}]},None)))
    text=asyncio.run(BilibiliAPIMixin._get_video_subtitles(native,'BV1xx411c7mD',1))
    assert text.endswith('最终结论') and len(text)>2000
    assert native._http_get.call_args.args[0]==url
    assert 'Cookie' not in native._http_get.call_args.kwargs['headers']
    assert native._wbi_get.call_args.args[0].endswith('/x/player/wbi/v2')


def msg(seq):
    return {'msg_seqno':seq,'msg_key':str(seq),'timestamp':int(time.time()),'sender_uid':'2','msg_type':1,'content':json.dumps({'content':str(seq)})}


def test_private_backlog_pages_overlap_dedup_and_sort():
    from reference_test_plugin.core.private_messages import PrivateMessageMixin
    plugin=SimpleNamespace(_fetch_private_session_messages=AsyncMock(side_effect=[
        {'messages':[msg(15),msg(14),msg(13)],'has_more':1},
        {'messages':[msg(13),msg(12),msg(11),msg(10)],'has_more':0}]))
    result=asyncio.run(PrivateMessageMixin._fetch_private_backlog(plugin,2,1,10))
    assert [row['msg_seqno'] for row in result['messages']]==[15,14,13,12,11,10]
    assert [call.args[2] for call in plugin._fetch_private_session_messages.call_args_list]==[0,13]


@pytest.mark.parametrize('second',[RuntimeError('page failure'),{'messages':[msg(15),msg(14)],'has_more':1}])
def test_private_partial_or_stalled_pages_fail_without_committing(second):
    from reference_test_plugin.core.private_messages import PrivateMessageMixin
    plugin=SimpleNamespace(_fetch_private_session_messages=AsyncMock(side_effect=[{'messages':[msg(15),msg(14)],'has_more':1},second]))
    with pytest.raises(RuntimeError):asyncio.run(PrivateMessageMixin._fetch_private_backlog(plugin,2,1,10))


def test_private_poll_does_not_jump_to_remote_max_or_lose_failed_page():
    from reference_test_plugin.core.private_messages import PrivateMessageMixin
    class Native(PrivateMessageMixin):
        config={'DEDE_USER_ID':'1','PRIVATE_MESSAGE_MAX_PER_POLL':3}
        def __init__(self):
            self.saved={'initialized':True,'account_uid':'1','sessions':{'1:2':10},'pending_messages':[],'processed_keys':[]}
        def _load_json(self,*args):return copy.deepcopy(self.saved)
        def _save_json(self,path,value):self.saved=copy.deepcopy(value)
    native=Native()
    native._get_private_sessions=AsyncMock(return_value=[{'talker_id':2,'session_type':1,'max_seqno':50}])
    native._fetch_private_session_messages=AsyncMock(return_value={'messages':[msg(12),msg(11)],'has_more':0})
    result=asyncio.run(native._poll_private_inbox())
    assert len(result)==2 and native.saved['sessions']['1:2']==12
    native.saved['pending_messages']=[]
    native._fetch_private_session_messages=AsyncMock(side_effect=[{'messages':[msg(15),msg(14)],'has_more':1},RuntimeError('page2 failed')])
    assert asyncio.run(native._poll_private_inbox())==[]
    assert native.saved['sessions']['1:2']==12 and native.saved['pending_messages']==[]


def test_comment_acceptance_readback_and_anonymous_visibility_are_distinct(tmp_path):
    from reference_test_plugin.core.comment_delivery import record_comment,verify_comment
    class Config(dict):data_dir=tmp_path
    record_comment(tmp_path,'token',{'oid':1,'type':1,'message':'hello'},{'code':0,'data':{'rpid':123}})
    reads=AsyncMock(side_effect=[({'code':0,'data':{'replies':[{'rpid':123}]}},None),({'code':0,'data':{'replies':[]}},None)])
    native=SimpleNamespace(config=Config(),_headers=lambda:{'Cookie':'private'},_http_get=reads)
    report=asyncio.run(verify_comment(native))
    assert report['status']=='readback_observed' and report['visible'] is None
    assert 'Cookie' not in reads.call_args_list[1].kwargs['headers']
    native._http_get=AsyncMock(return_value=({'code':0,'data':{'replies':[{'rpid_str':'123'}]}},None))
    assert asyncio.run(verify_comment(native,'123'))['visible'] is True
    # Verification only reads; it has no posting method to fall back to.


def test_missing_comment_after_bounded_pagination_stays_unknown(tmp_path):
    from reference_test_plugin.core.comment_delivery import record_comment,verify_comment
    class Config(dict):data_dir=tmp_path
    record_comment(tmp_path,'t',{'oid':1},{'data':{'rpid':5}})
    native=SimpleNamespace(config=Config(),_headers=lambda:{},_http_get=AsyncMock(return_value=({'code':0,'data':{'replies':[{'rpid':6}],'cursor':{'next':88}}},None)))
    report=asyncio.run(verify_comment(native))
    assert report['status']=='unconfirmed' and report['visible'] is None


def test_real_network_serializes_sessions_and_honors_business_risk_codes(tmp_path):
    from reference_test_plugin.core.network import plugin_http_session
    class Config(dict):data_dir=tmp_path
    observed=[]
    active=0
    maximum=0
    async def serve(reader,writer):
        nonlocal active,maximum
        head=await reader.readuntil(b'\r\n\r\n')
        observed.append((time.monotonic(),head))
        active+=1;maximum=max(maximum,active)
        await asyncio.sleep(.05)
        body=json.dumps({'code':-352 if b'/risk ' in head else 0,'data':{}}).encode()
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
        await writer.drain();active-=1;writer.close();await writer.wait_closed()
    async def run():
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        cfg=Config(PROXY_URL=f'http://127.0.0.1:{port}',BILI_REQUEST_GAP_MS=600)
        async def read(path):
            async with plugin_http_session(cfg) as session:
                async with session.get('http://api.bilibili.com/'+path) as response:return await response.json()
        try:
            await asyncio.gather(read('one'),read('two'),read('three'))
            assert maximum==1
            assert all(b[0]-a[0]>=.58 for a,b in zip(observed,observed[1:]))
            with pytest.raises(BilibiliPaused):await read('risk')
            count=len(observed)
            with pytest.raises(BilibiliPaused):await read('blocked')
            assert len(observed)==count
        finally:server.close();await server.wait_closed()
    asyncio.run(run())


def test_real_post_acceptance_saves_receipt_and_blocks_repeated_operation(tmp_path):
    from reference_test_plugin.core.network import plugin_http_session
    class Config(dict):data_dir=tmp_path
    requests=[]
    async def serve(reader,writer):
        head=await reader.readuntil(b'\r\n\r\n')
        length=next((int(line.split(b':',1)[1]) for line in head.split(b'\r\n') if line.lower().startswith(b'content-length:')),0)
        await reader.readexactly(length)
        requests.append(head)
        body=b'{"code":0,"data":{"rpid":987}}'
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
        await writer.drain();writer.close();await writer.wait_closed()
    async def run():
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        cfg=Config(PROXY_URL=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}',BILI_REQUEST_GAP_MS=600)
        try:
            async with plugin_http_session(cfg) as session:
                async with session.post('http://api.bilibili.com/x/v2/reply/add',data={'oid':5,'type':1,'message':'hello'}) as response:
                    assert (await response.json())['code']==0
            with connect(tmp_path) as db:
                assert db.execute('SELECT rpid,oid FROM comment_delivery').fetchone()==('987','5')
                assert db.execute('SELECT state,rpid FROM traffic_attempts').fetchone()==('accepted','987')
            with pytest.raises(BilibiliPaused):
                async with plugin_http_session(cfg) as session:
                    async with session.post('http://api.bilibili.com/x/v2/reply/add',data={'oid':5,'type':1,'message':'hello'}):pass
            assert len(requests)==1
        finally:server.close();await server.wait_closed()
    asyncio.run(run())
