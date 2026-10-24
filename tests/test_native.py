"""Behavior tests for the native port; no external messages or real credentials."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('hermes_bili_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=package
spec.loader.exec_module(package)
from hermes_bili_test.settings import Config

def test_config_keeps_credentials_separate_and_reapplies_operator_limits(tmp_path):
    config=Config(tmp_path,{'SESSDATA':'dummy-session','ENABLE_LIVE_DANMAKU_REPLY':True,
        'PROACTIVE_COIN':True,'AUTONOMOUS_DYNAMIC_DAILY_LIMIT':99})
    config.save_config()
    public=json.loads(config.config_path.read_text())
    secret=json.loads(config.secret_path.read_text())
    assert 'SESSDATA' not in public and secret['SESSDATA']=='dummy-session'
    assert public['AUTONOMOUS_DYNAMIC_DAILY_LIMIT']==1
    assert public['AUTONOMOUS_PROACTIVE_DAILY_LIMIT']==3
    assert public['AUTONOMOUS_REPLY_DAILY_LIMIT']==20
    assert public['AUTONOMOUS_PRIVATE_DAILY_LIMIT']==20
    assert public['ENABLE_LIVE_DANMAKU_REPLY'] and not public['PROACTIVE_COIN']
    assert public['SHARED_HERMES_MEMORY']
    assert config.secret_path.stat().st_mode & 0o077 == 0

def test_hermes_model_route_and_persona_are_reused(tmp_path,monkeypatch):
    from hermes_bili_test.context import NativeContext
    from agent import auxiliary_client
    (tmp_path/'SOUL.md').write_text('native persona')
    observed=[]
    def fake(**kw):
        observed.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='中文结果'))])
    monkeypatch.setattr(auxiliary_client,'call_llm',fake)
    monkeypatch.setattr('hermes_bili_test.shared_memory.shared_context',lambda home:'shared preference')
    context=NativeContext(tmp_path)
    response=asyncio.run(context.llm_generate('question',system_prompt='persona'))
    assert response.completion_text=='中文结果'
    assert observed[0]['task'] is None and 'api_key' not in observed[0]
    assert 'shared preference' in observed[0]['messages'][0]['content']
    assert asyncio.run(context.persona_manager.get_default_persona_v3())['prompt']=='native persona'

def test_qr_confirmation_persists_cookie_but_never_returns_it(tmp_path):
    from hermes_bili_test.host import Host
    config=Config(tmp_path)
    runtime=SimpleNamespace(_login_qrcode_key='test-qr', config=config,
        _qr_login_poll=AsyncMock(return_value=(0,'登录成功',{'SESSDATA':'never-output','bili_jct':'fixture'})))
    fake=SimpleNamespace(runtime=runtime)
    result=asyncio.run(Host.control(fake,{'action':'confirm'}))
    assert result=={'code':0,'message':'登录成功'}
    assert 'never-output' not in json.dumps(result)
    assert json.loads(config.secret_path.read_text())['SESSDATA']=='never-output'

def test_native_management_rejects_bilibili_user_and_nonowner(monkeypatch):
    import hermes_bili_test.host as native
    from gateway import session_context
    monkeypatch.setattr(native,'load_config_readonly',lambda:{'plugins':{'entries':{'qq-group-tools':{'settings':{'owner_id':'owner'}}}}})
    for platform,user in [('bilibili','owner'),('qqbot','someone')]:
        tokens=session_context.set_session_vars(platform=platform,user_id=user)
        try:
            assert 'error' in json.loads(native.manage({'action':'start'}))
        finally:
            session_context.clear_session_vars(tokens)

def test_missing_account_does_not_start_polling(tmp_path):
    from hermes_bili_test.host import Host
    runtime=SimpleNamespace(check_cookie=AsyncMock(return_value=(False,'invalid')),
        _start_bot=AsyncMock())
    fake=SimpleNamespace(runtime=runtime,opened=True)
    with pytest.raises(RuntimeError):
        asyncio.run(Host.open(fake,start=True))
    runtime._start_bot.assert_not_awaited()

def test_durable_daily_budget_refuses_twenty_first_reply(tmp_path):
    from hermes_bili_test.core.layered_runtime import LayeredRuntime
    from hermes_bili_test.core.runtime import ActionRequest
    async def run():
        config=Config(tmp_path, {'BEHAVIOR_GLOBAL_MAX_PER_MINUTE':100})
        runtime=LayeredRuntime(config,tmp_path/'budget.sqlite3')
        await runtime.open()
        try:
            for i in range(20):
                receipt=await runtime.before_action(ActionRequest(key=f'reply-{i}',kind='comment_reply',event_key=f'event-{i}',target_id='target'))
                assert receipt[0] is True, receipt
            refused=await runtime.before_action(ActionRequest(key='reply-21',kind='comment_reply',event_key='event-21',target_id='target'))
            assert refused[0] is False
        finally:
            await runtime.close()
    asyncio.run(run())

def test_single_runtime_lock_and_private_ipc():
    import os
    import tempfile
    from pathlib import Path
    from hermes_bili_test.ipc import lock_runtime,RuntimeAlreadyOwned,start_server,request
    async def run():
        fd=lock_runtime(tmp_path)
        with pytest.raises(RuntimeAlreadyOwned):
            lock_runtime(tmp_path)
        native=SimpleNamespace(home=tmp_path,schemas=[{'name':'test'}],
            control=AsyncMock(return_value={'running':True}))
        server=await start_server(native)
        try:
            assert (tmp_path/'bilibili/runtime.sock').stat().st_mode & 0o077 == 0
            answer=await asyncio.to_thread(request,tmp_path,{'operation':'control','args':{'action':'status'}})
            assert answer=={'running':True}
            with pytest.raises(RuntimeError):
                await asyncio.to_thread(request,tmp_path,{'operation':'arbitrary-execution'})
        finally:
            server.close()
            await server.wait_closed()
            os.close(fd)
    with tempfile.TemporaryDirectory(prefix='hb-',dir='/tmp') as directory:
        tmp_path=Path(directory)
        asyncio.run(run())

def test_cli_discovery_does_not_start_background_runtime(monkeypatch):
    import hermes_bili_test.host as native
    from unittest.mock import Mock
    monkeypatch.setattr(native,'is_gateway',lambda:False)
    monkeypatch.setattr(native,'plugin_settings',lambda:{'enabled':True})
    forbidden=Mock(side_effect=AssertionError('discovery must not start a runtime'))
    monkeypatch.setattr(native,'host',forbidden)
    class Registry:
        def __init__(self):self.tools=[]
        def register_tool(self,**kw):self.tools.append(kw['name'])
        def register_cli_command(self,*args,**kwargs):pass
        def register_hook(self,*args,**kwargs):pass
    registry=Registry()
    package.register(registry)
    assert 'search_bilibili' in registry.tools and 'bilibili_manage' in registry.tools
    forbidden.assert_not_called()


def test_gateway_detection_handles_published_launcher(monkeypatch):
    import hermes_bili_test.host as native
    from agent import runtime_session_store
    monkeypatch.setattr(runtime_session_store,'is_worker_process',lambda:False)
    monkeypatch.setitem(sys.modules,'hermes_cli.main',SimpleNamespace(__file__='/repo/hermes_cli/main.py'))
    monkeypatch.setattr(sys,'argv',['-c','gateway','run'])
    assert native.is_gateway()
    monkeypatch.setattr(sys,'argv',['-c','bilibili','status'])
    assert not native.is_gateway()
    monkeypatch.setattr(runtime_session_store,'is_worker_process',lambda:True)
    monkeypatch.setattr(sys,'argv',['-c','gateway','run'])
    assert not native.is_gateway()


def test_external_downloader_does_not_inherit_hermes_python_abi(monkeypatch):
    from hermes_bili_test.core.utils import UtilsMixin
    monkeypatch.setenv('PYTHONPATH','/hermes/python314/site-packages')
    monkeypatch.setenv('PYTHONHOME','/hermes/python314')
    proc=SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(b'ok',b'')))
    start=AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio,'create_subprocess_exec',start)
    native=SimpleNamespace(_find_command=lambda name:'/usr/bin/'+name)
    assert asyncio.run(UtilsMixin._run_process(native,'yt-dlp','--version')) == (0,'ok','')
    env=start.call_args.kwargs['env']
    assert 'PYTHONPATH' not in env and 'PYTHONHOME' not in env
    assert env.get('PATH')


def test_bilibili_facts_write_native_hermes_memory_and_refresh_qq(tmp_path,monkeypatch):
    import hermes_constants
    from tools import memory_tool
    from hermes_bili_test.shared_memory import SharedMemory,shared_context,refresh_qq_memory
    monkeypatch.setattr(hermes_constants,'get_hermes_home',lambda:tmp_path)
    monkeypatch.setattr(memory_tool,'get_memory_dir',lambda:tmp_path/'memories')
    context=SimpleNamespace(llm_generate=AsyncMock(return_value=SimpleNamespace(completion_text='{"memory":"看过猫咪拟人生活短片，最喜欢猫在影院吃爆米花的场景"}')))
    shared=SharedMemory(tmp_path,context)
    record={'rpid':'video-1','memory_type':'video','user_id':'self','text':'看了猫咪生活视频，喜欢影院场景'}
    assert asyncio.run(shared.remember(record))
    native=(tmp_path/'memories/MEMORY.md').read_text()
    assert '影院' in native
    assert '影院' in shared_context(tmp_path)
    assert '影院' in refresh_qq_memory(platform='qqbot')['context']
    assert refresh_qq_memory(platform='other') is None
    assert not asyncio.run(shared.remember(record))
    assert context.llm_generate.await_count==1
    assert not (tmp_path/'bilibili/permanent_memory.json').exists()


def test_shared_memory_refuses_secret_and_uses_native_capacity(tmp_path,monkeypatch):
    import hermes_constants
    from tools import memory_tool
    from tools.memory_tool_store import MemoryStore
    import hermes_bili_test.shared_memory as native
    monkeypatch.setattr(hermes_constants,'get_hermes_home',lambda:tmp_path)
    monkeypatch.setattr(memory_tool,'get_memory_dir',lambda:tmp_path/'memories')
    store=MemoryStore(memory_char_limit=25)
    assert store.add('memory','原有长期记忆不能被覆盖')['success']
    store.load_from_disk()
    monkeypatch.setattr(native,'store_for',lambda home:store)
    context=SimpleNamespace(llm_generate=AsyncMock(return_value=SimpleNamespace(completion_text='{"memory":"这是一段超过剩余记忆容量的观看经历，不应该挤掉原有记忆"}')))
    shared=native.SharedMemory(tmp_path,context)
    assert not asyncio.run(shared.remember({'rpid':'secret','text':'SESSDATA=do-not-share'}))
    context.llm_generate.assert_not_awaited()
    assert not asyncio.run(shared.remember({'rpid':'event','user_id':'self','memory_type':'video','text':'观看了电影'}))
    assert (tmp_path/'memories/MEMORY.md').read_text().count('原有长期记忆不能被覆盖')==1


def test_live_can_be_enabled_without_entering_a_room(tmp_path):
    from hermes_bili_test.host import Host
    config=Config(tmp_path,{'ENABLE_LIVE_DANMAKU_REPLY':True,'LIVE_DANMAKU_ROOM_ID':123})
    runtime=SimpleNamespace(config=config,_stop_live_danmaku_listener=AsyncMock())
    fake=SimpleNamespace(runtime=runtime,control=AsyncMock(return_value={'live_room_id':0}),open=AsyncMock())
    asyncio.run(Host.control(fake,{'action':'live','room_id':0}))
    assert config['ENABLE_LIVE_DANMAKU_REPLY'] is True and config['LIVE_DANMAKU_ROOM_ID']==0
    fake.open.assert_not_awaited()
    with pytest.raises(ValueError):
        asyncio.run(Host.control(fake,{'action':'live','room_id':True}))


def test_unverified_video_never_becomes_a_long_term_fact(tmp_path):
    from hermes_bili_test.shared_memory import SharedMemory
    context=SimpleNamespace(llm_generate=AsyncMock())
    memory=SharedMemory(tmp_path,context)
    assert not asyncio.run(memory.remember({'memory_type':'video','content_analyzed':False,'text':'仅有标题'}))
    context.llm_generate.assert_not_awaited()


def test_live_background_uses_bilibili_scope_not_qq_owner_grant():
    import contextvars
    from hermes_bili_test.core.live_danmaku import LiveDanmakuMixin
    from gateway import session_context
    observed=[]
    class Listener(LiveDanmakuMixin):
        config={'ENABLE_LIVE_DANMAKU_REPLY':True,'LIVE_DANMAKU_ROOM_ID':123}
        _running=True
        async def _live_danmaku_loop(self):
            observed.append(session_context.get_session_env('HERMES_SESSION_PLATFORM',''))
    async def run():
        listener=Listener()
        listener._init_live_danmaku_state()
        tokens=session_context.set_session_vars(platform='bilibili',user_id='account')
        listener._background_context=contextvars.copy_context()
        session_context.clear_session_vars(tokens)
        tokens=session_context.set_session_vars(platform='qqbot',user_id='owner')
        try:
            ok,_=await listener._start_live_danmaku_listener()
            assert ok
            await asyncio.sleep(0)
            assert observed==['bilibili']
            await listener._stop_live_danmaku_listener()
        finally:
            session_context.clear_session_vars(tokens)
    asyncio.run(run())


@pytest.mark.parametrize('subtitle',['','可靠字幕正文'])
def test_subtitles_only_never_uses_visual_or_title_fallback(subtitle):
    from hermes_bili_test.core.video import VideoMixin
    native=SimpleNamespace(config={'VIDEO_SUBTITLES_ONLY':True},
        _build_joint_context=AsyncMock(return_value={'subtitle':subtitle}),
        _analyze_video_media=AsyncMock(side_effect=AssertionError('no visual analysis')),
        _analyze_video_text=AsyncMock(return_value='字幕摘要'))
    info={}
    result=asyncio.run(VideoMixin._analyze_video_with_vision_inner(native,info))
    native._analyze_video_media.assert_not_awaited()
    if subtitle:
        assert result=='字幕摘要' and info['analysis_basis']=='subtitles'
        native._analyze_video_text.assert_awaited_once()
    else:
        assert result is None and info['_subtitle_skipped']
        native._analyze_video_text.assert_not_awaited()


def test_skipped_video_is_not_cached_or_marked_seen():
    from hermes_bili_test.core.video import VideoMixin
    from unittest.mock import Mock
    info={'bvid':'BV-fixture'}
    async def skipped(video):
        video['_subtitle_skipped']=True
        return None
    native=SimpleNamespace(_video_analysis_lock=lambda bvid:asyncio.Lock(),
        _cached_video_memory=lambda bvid:None,_seen_video_record=AsyncMock(return_value=None),
        _analyze_video_with_vision_inner=skipped,
        _save_json=Mock(side_effect=AssertionError('skipped video must not be cached')))
    assert asyncio.run(VideoMixin._analyze_video_with_vision_impl(native,info)) is None
    native._save_json.assert_not_called()


@pytest.mark.parametrize('decision,accepted', [
    ({'relevant':True,'usable':True},True),
    ({'relevant':True,'usable':False},False),
    ({'relevant':False,'usable':True},False),
    ({'relevant':True},False),
])
def test_subtitles_require_content_not_just_relevance(decision,accepted):
    from hermes_bili_test.core.video import VideoMixin
    llm=AsyncMock(return_value=json.dumps(decision))
    native=SimpleNamespace(config={},_llm_call=llm,_repair_llm_json=lambda s:s)
    text='这段对白讨论本地模型的内存占用以及量化部署的具体过程'
    result=asyncio.run(VideoMixin._validate_video_subtitle(native,{},text))
    assert bool(result) is accepted


def test_music_only_is_rejected_without_model_call():
    from hermes_bili_test.core.video import VideoMixin
    native=SimpleNamespace(config={},_llm_call=AsyncMock())
    assert asyncio.run(VideoMixin._validate_video_subtitle(native,{},'[背景音乐]\n♪ BGM ♪')) == ''
    native._llm_call.assert_not_awaited()


def test_summary_admitting_no_content_is_not_watched():
    from hermes_bili_test.core.video import VideoMixin
    native=SimpleNamespace(config={'VIDEO_SUBTITLES_ONLY':True},
        _build_joint_context=AsyncMock(return_value={'subtitle':'候选字幕'}),
        _analyze_video_text=AsyncMock(return_value='仅依据标题、简介和观众评论，尚未确认完整内容'),
        _analyze_video_media=AsyncMock())
    info={}
    assert asyncio.run(VideoMixin._analyze_video_with_vision_inner(native,info)) is None
    assert info['_subtitle_skipped'] and info['analysis_basis']=='metadata'
    native._analyze_video_media.assert_not_awaited()


@pytest.mark.parametrize('media', ['实际观察到的画面',None])
def test_explicit_watch_can_try_media_but_never_metadata_fallback(media):
    from hermes_bili_test.core.video import VideoMixin
    native=SimpleNamespace(config={'VIDEO_SUBTITLES_ONLY':True},
        _build_joint_context=AsyncMock(return_value={'subtitle':''}),
        _should_use_visual_video_analysis=lambda joint:True,
        _analyze_video_media=AsyncMock(return_value=media),
        _merge_visual_and_joint=AsyncMock(return_value=media),
        _clip_media_text=VideoMixin._clip_media_text,
        _analyze_video_text=AsyncMock(side_effect=AssertionError('no metadata summary')))
    info={'_allow_video_media':True}
    result=asyncio.run(VideoMixin._analyze_video_with_vision_inner(native,info))
    native._analyze_video_media.assert_awaited_once()
    assert result==media
    assert info['analysis_basis']==('video_media' if media else 'metadata')
    if not media:
        assert info['_subtitle_skipped']
