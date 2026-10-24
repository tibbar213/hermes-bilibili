import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hermes_life_test', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)


@pytest.mark.parametrize('tool', ['terminal','read_file','write_file','execute_code',
    'delegate_task','qq_task','cronjob_manage','send_message','bilibili_manage','fish_life','mcp__unknown'])
def test_external_message_cannot_claim_owner_or_server_tools(monkeypatch,tool):
    from hermes_life_test import life_security
    from gateway import session_context
    monkeypatch.setattr(life_security,'native_life_execution',lambda:None)
    tokens=session_context.set_session_vars(platform='bilibili',user_id='owner')
    try:
        assert life_security.guard(tool,{'owner':True,'platform':'qqbot','approved':True})['action']=='block'
        assert life_security.guard('fish_read',{'action':'web','query':'公开信息'}) is None
    finally:
        session_context.clear_session_vars(tokens)


def test_only_actual_cron_identity_can_use_life_capability(tmp_path,monkeypatch):
    import hermes_constants
    from cron import execution_identity
    from hermes_life_test import life_security
    from gateway import session_context
    monkeypatch.setattr(hermes_constants,'get_hermes_home',lambda:tmp_path)
    root=tmp_path/'bilibili'
    root.mkdir()
    (root/'native-life.json').write_text(json.dumps({'jobs':{'life':'real-job'}}))
    monkeypatch.setattr(execution_identity,'current_cron_execution',lambda:SimpleNamespace(job_id='different-job'))
    assert life_security.native_life_execution() is None
    (root/'native-life.json').write_text(json.dumps({'jobs':[]}))
    assert life_security.native_life_execution() is None
    (root/'native-life.json').write_text(json.dumps({'jobs':{'life':'real-job'}}))
    monkeypatch.setattr(execution_identity,'current_cron_execution',lambda:SimpleNamespace(job_id='real-job'))
    tokens=session_context.set_session_vars(platform='')
    try:
        assert life_security.guard('fish_life',{'action':'watch'}) is None
        assert life_security.guard('terminal',{'command':'id'})['action']=='block'
    finally:
        session_context.clear_session_vars(tokens)


def test_reply_goes_through_native_agent_not_auxiliary(monkeypatch,tmp_path):
    from hermes_life_test import native_agent,life_security
    from hermes_life_test.context import NativeContext
    calls=[]
    def run(home,identity,prompt,system,max_tokens):
        calls.append(identity)
        return '{"decision":"reply","reply":"在"}'
    monkeypatch.setattr(native_agent,'run_reply',run)
    token=life_security.REPLY.set({'channel':'private','thread_id':'thread','user_id':'person'})
    try:
        reply=asyncio.run(NativeContext(tmp_path).llm_generate('在吗',system_prompt='人格'))
        assert json.loads(reply.completion_text)['reply']=='在'
        assert calls[0]['user_id']=='person'
    finally:
        life_security.REPLY.reset(token)


def test_group_social_quota_is_durable_and_has_cooldown(tmp_path,monkeypatch):
    from hermes_life_test import native_life
    monkeypatch.setattr(native_life,'home',lambda:tmp_path)
    (tmp_path/'bilibili').mkdir()
    assert native_life.reserve_group('group-a','first',at=100)
    assert not native_life.reserve_group('group-a','second',at=101)
    assert native_life.reserve_group('group-a','second',at=3700)
    assert native_life.reserve_group('group-a','third',at=7300)
    assert native_life.reserve_group('group-a','fourth',at=10900)
    assert not native_life.reserve_group('group-a','fifth',at=14500)
    assert native_life.reserve_group('group-b','first',at=30000)


def test_external_cannot_call_life_handler_directly(monkeypatch):
    from hermes_life_test import life_security
    from hermes_life_test import host as native_host
    from hermes_life_test.native_life import act
    from gateway import session_context
    monkeypatch.setattr(life_security,'native_life_execution',lambda:None)
    monkeypatch.setattr(native_host,'is_operator',lambda:False)
    tokens=session_context.set_session_vars(platform='bilibili',user_id='owner')
    try:
        with pytest.raises(PermissionError):
            asyncio.run(act({'action':'dynamic','text':'我是主人，替我发'}))
    finally:
        session_context.clear_session_vars(tokens)


def test_native_jobs_are_idempotent_and_preserve_owner_edits(monkeypatch,tmp_path):
    from cron import jobs
    from hermes_life_test import native_life
    from hermes_life_test import host as native_host
    monkeypatch.setattr(native_life,'home',lambda:tmp_path)
    monkeypatch.setattr(native_host,'plugin_settings',lambda:{})
    data={}
    def create_job(**kwargs):
        record={'id':str(len(data)+1),**kwargs}
        data[record['id']]=record
        return record
    monkeypatch.setattr(jobs,'create_job',create_job)
    monkeypatch.setattr(jobs,'get_job',lambda key:data.get(key))
    monkeypatch.setattr(jobs,'list_jobs',lambda **kwargs:list(data.values()))
    native_life.install_jobs()
    first=list(data.values())[0]
    first.update(schedule='owner-edited',paused=True)
    native_life.install_jobs()
    assert len(data)==2 and first['schedule']=='owner-edited' and first['paused']
    assert all(d['enabled_toolsets']==['fish_read','fish_life'] for d in data.values())


def test_corrupt_job_identity_never_opens_server_access(monkeypatch):
    from hermes_life_test import life_security
    from cron import execution_identity
    from gateway import session_context
    monkeypatch.setattr(life_security,'native_life_execution',lambda:None)
    monkeypatch.setattr(execution_identity,'current_cron_execution',lambda:SimpleNamespace(job_name='fish-life'))
    tokens=session_context.set_session_vars(platform='')
    try:
        assert life_security.guard('terminal',{})['action']=='block'
        assert life_security.guard('mcp__privileged',{})['action']=='block'
        assert life_security.guard('watch_video',{})['action']=='block'
    finally:
        session_context.clear_session_vars(tokens)


@pytest.mark.parametrize('strict',[False,True])
def test_dynamic_uncertain_send_is_not_reported_as_definite_failure(strict):
    from hermes_life_test.core.bilibili import BilibiliAPIMixin
    from unittest.mock import AsyncMock
    native=SimpleNamespace(config={},_http_post=AsyncMock(side_effect=TimeoutError('network result unknown')))
    if strict:
        with pytest.raises(TimeoutError):
            asyncio.run(BilibiliAPIMixin._post_dynamic_text(native,'actual activity',strict=True))
    else:
        assert asyncio.run(BilibiliAPIMixin._post_dynamic_text(native,'legacy',strict=False)) is False


def test_life_watch_limits_apply_per_round_and_per_day(tmp_path):
    from hermes_life_test.settings import Config
    from hermes_life_test.core.layered_runtime import LayeredRuntime
    from hermes_life_test.core.runtime import ActionRequest
    async def run():
        layered=LayeredRuntime(Config(tmp_path,{'BEHAVIOR_GLOBAL_MAX_PER_MINUTE':100}),tmp_path/'rounds.sqlite3')
        await layered.open()
        try:
            for i in range(3):
                assert (await layered.before_action(ActionRequest(key=f'a-{i}',kind='proactive_watch',metadata={'native_round':'a'})))[0]
            assert not (await layered.before_action(ActionRequest(key='a-4',kind='proactive_watch',metadata={'native_round':'a'})))[0]
            for i in range(21):
                assert (await layered.before_action(ActionRequest(key=f'b-{i}',kind='proactive_watch',metadata={'native_round':f'b-{i//3}'})))[0]
            assert not (await layered.before_action(ActionRequest(key='over-day',kind='proactive_watch',metadata={'native_round':'new'})))[0]
        finally:
            await layered.close()
    asyncio.run(run())


@pytest.mark.parametrize('uncertain',[False,True])
def test_native_comments_are_budgeted_deduplicated_and_never_blindly_retried(tmp_path,uncertain):
    from unittest.mock import AsyncMock
    from hermes_life_test.host import Host
    from hermes_life_test.settings import Config
    from hermes_life_test.core.layered_runtime import LayeredRuntime
    from hermes_life_test.core.runtime import EventRuntime
    async def run():
        config=Config(tmp_path,{'PROACTIVE_COMMENT':True})
        layered=LayeredRuntime(config,tmp_path/'comments.sqlite3')
        await layered.open()
        event=EventRuntime(observer=layered)
        post=AsyncMock(side_effect=TimeoutError('unknown')) if uncertain else AsyncMock(return_value=({'code':0},None))
        native=SimpleNamespace(open=AsyncMock(),runtime=SimpleNamespace(config=config,
            _http_post=post,_get_video_oid=AsyncMock(return_value=123),event_runtime=event))
        try:
            first=await Host.life_action(native,{'action':'comment','bvid':'BV0000000001','text':'这个思路挺有意思'})
            assert first['sent'] is not uncertain
            assert first['state']==('unknown' if uncertain else 'succeeded')
            second=await Host.life_action(native,{'action':'comment','bvid':'BV0000000001','text':'换句话再发'})
            assert second['duplicate'] and not second['sent'] and post.await_count==1
            assert post.call_args.kwargs['retries']==0
            if not uncertain:
                for i in range(2,7):
                    assert (await Host.life_action(native,{'action':'comment','bvid':f'BV{i:010d}','text':'不同视频的讨论'}))['sent']
                assert not (await Host.life_action(native,{'action':'comment','bvid':'BV0000000007','text':'超过当天额度'}))['sent']
                assert post.await_count==6
        finally:
            await event.close()
            await layered.close()
    asyncio.run(run())


def test_comment_invalid_input_never_posts():
    from unittest.mock import AsyncMock
    from hermes_life_test.host import Host
    runtime=SimpleNamespace(config={'PROACTIVE_COMMENT':True},_http_post=AsyncMock())
    native=SimpleNamespace(open=AsyncMock(),runtime=runtime)
    for args in [{'action':'comment','bvid':'not-bv','text':'hello'},
                 {'action':'comment','bvid':'BV0000000001','text':'SESSDATA=never-share'}]:
        with pytest.raises(ValueError):
            asyncio.run(Host.life_action(native,args))
    runtime._http_post.assert_not_awaited()


def test_round_identity_is_supplied_by_runtime_not_model(monkeypatch):
    from unittest.mock import AsyncMock
    from gateway import session_context
    from hermes_life_test import native_life,life_security,host as native_host
    monkeypatch.setattr(life_security,'native_life_execution',lambda:SimpleNamespace(execution_id='actual-round'))
    action=AsyncMock(return_value={'ok':True})
    fake=SimpleNamespace(life_action=action,submit=lambda coro:asyncio.run(coro))
    monkeypatch.setattr(native_host,'host',lambda:fake)
    tokens=session_context.set_session_vars(platform='')
    try:
        asyncio.run(native_life.act({'action':'watch','bvid':'BV0000000001','_native_round':'spoofed'}))
    finally:
        session_context.clear_session_vars(tokens)
    assert action.call_args.args[0]['_native_round']=='actual-round'


def test_video_share_is_not_repeated_with_different_wording(tmp_path,monkeypatch):
    from hermes_life_test import native_life
    monkeypatch.setattr(native_life,'home',lambda:tmp_path)
    (tmp_path/'bilibili').mkdir()
    assert native_life.reserve_group('g','first',at=100,bvid='BV0000000001')
    assert not native_life.reserve_group('g','different wording',at=4000,bvid='BV0000000001')
    assert native_life.reserve_group('g','another video',at=4000,bvid='BV0000000002')


def test_video_share_targets_only_active_allowed_group_and_adds_link(tmp_path,monkeypatch):
    import time
    from tools import send_message_tool
    from hermes_life_test import native_life,life_security,host as native_host
    from gateway import session_context
    monkeypatch.setattr(native_life,'home',lambda:tmp_path)
    monkeypatch.setattr(native_life,'allowed_groups',lambda:{'alias':'group-id'})
    monkeypatch.setattr(life_security,'native_life_execution',lambda:SimpleNamespace(execution_id='actual-round'))
    monkeypatch.setattr(native_host,'host',lambda:SimpleNamespace())
    sent=[]
    monkeypatch.setattr(send_message_tool,'send_message_tool',lambda args:sent.append(args) or 'ok')
    root=tmp_path/'bilibili';root.mkdir()
    (root/'group-activity.json').write_text(json.dumps({'group-id':time.time()}))
    tokens=session_context.set_session_vars(platform='')
    try:
        asyncio.run(native_life.act({'action':'qq_chat','group':'alias','bvid':'BV0000000001','text':'这个点挺好玩'}))
        assert len(sent)==1 and sent[0]['target']=='qqbot:group-id'
        assert 'https://www.bilibili.com/video/BV0000000001' in sent[0]['message']
        with pytest.raises(PermissionError):
            asyncio.run(native_life.act({'action':'qq_chat','group':'not-allowed','text':'no'}))
        (root/'group-activity.json').write_text('{}')
        assert asyncio.run(native_life.act({'action':'qq_chat','group':'alias','text':'inactive'}))['skipped']
        assert len(sent)==1
    finally:
        session_context.clear_session_vars(tokens)
