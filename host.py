"""Native Hermes plugin host: one profile-bound loop, native tools and explicit lifecycle."""
import asyncio
import atexit
import concurrent.futures
import contextvars
import json
import os
import shlex
import sys
import threading
from types import SimpleNamespace

from hermes_constants import get_hermes_home
from hermes_cli.config import load_config_readonly
from agent.memory_provider import spawn_context_thread
from .settings import Config, LIMITS, SOCIAL_LIMITS
from .context import NativeContext
from .native_types import ContextWrapper

def plugin_settings():
    cfg=load_config_readonly()
    return ((((cfg.get('plugins') or {}).get('entries') or {}).get('hermes-bilibili') or {}).get('settings') or {})

class Host:
    def __init__(self,service=False):
        self.home=get_hermes_home()
        self.service=service
        self.lock_fd=None
        self.server=None
        if service:
            from .ipc import lock_runtime
            self.lock_fd=lock_runtime(self.home)
        self.loop=asyncio.new_event_loop()
        self.ready=threading.Event()
        self.error=None
        self.runtime=None
        self.opened=False
        self.thread=spawn_context_thread(self._run,name='hermes-bilibili',daemon=True)
        self.thread.start()
        if not self.ready.wait(20):
            raise TimeoutError('Bilibili native plugin initialization timed out')
        if self.error:
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd=None
            raise RuntimeError('Bilibili native plugin could not initialize') from self.error
        atexit.register(self.close)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            from .runtime import BilibiliRuntime
            self.runtime=BilibiliRuntime(NativeContext(self.home),Config(self.home))
            from gateway.session_context import set_session_vars
            set_session_vars(platform='bilibili',user_id=str(self.runtime.config.get('DEDE_USER_ID','')),
                chat_id='bilibili',chat_type='dm',session_key='agent:main:bilibili:account')
            self.runtime._background_context=contextvars.copy_context()
            if self.service:
                from .ipc import start_server
                self.server=self.loop.run_until_complete(start_server(self))
        except Exception as exc:
            self.error=exc
        finally:
            self.ready.set()
        if not self.error:
            self.loop.run_forever()
        self.loop.close()

    @property
    def schemas(self):
        return [{'name':t.name,'description':t.description,'parameters':t.parameters}
            for t in self.runtime.context.tools]

    def submit(self,coro,timeout=600):
        future=asyncio.run_coroutine_threadsafe(coro,self.loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError('Bilibili operation timed out; cancelled, do not blindly repeat a write') from None

    async def open(self,start=False):
        if not self.opened:
            await self.runtime.layered_runtime.open()
            await self.runtime._initialize_unified_memory()
            await self.runtime._initialize_seen_videos()
            self.opened=True
        if start:
            ok,_=await self.runtime.check_cookie()
            if not ok:
                raise RuntimeError('Bilibili login is missing or expired; use login/confirm')
            await self.runtime._start_bot()

    async def invoke(self,name,args):
        await self.open()
        if name=='bili_feed':
            return json.dumps(await self.video_feed(args),ensure_ascii=False)
        if name=='bili_parse_video':
            target=await self.runtime._extract_bili_share_target(str(args.get('target','')))
            if not target:
                raise ValueError('No valid Bilibili video link or BV ID')
            info=await self.runtime._get_video_info_by_share_target(target)
            return json.dumps(info,ensure_ascii=False)
        tool=next((t for t in self.runtime.context.tools if t.name==name),None)
        if not tool:
            raise ValueError('Unknown Bilibili tool')
        return str(await tool.call(ContextWrapper(SimpleNamespace(event=None)),**args))

    async def video_feed(self,args):
        """Bounded metadata candidates only; selection never counts as watching."""
        import re
        import html
        source=str(args.get('source') or '')
        if source not in {'home','following'}:
            raise ValueError('Unknown video feed')
        if not self.runtime.config.get('SESSDATA'):
            return {'source':source,'items':[],'reason':'login_required'}
        limit=max(1,min(12,int(args.get('limit',12))))
        rows=(await self.runtime._get_rcmd_videos() if source=='home'
              else await self.runtime.get_following_updates(limit=20))
        watched=await self.runtime._seen_video_bvids()
        result=[]
        selected=set()
        filtered_seen=0
        def text(value,size):
            return html.unescape(re.sub(r'<[^>]*>','',str(value or '')))[:size]
        for row in rows:
            bvid=str(row.get('bvid') if source=='home' else row.get('video_bvid') or '')
            if not re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid) or bvid in selected:
                continue
            selected.add(bvid)
            if bvid in watched:
                filtered_seen+=1
                continue
            result.append({'bvid':bvid,'title':text(row.get('title') if source=='home' else row.get('video_title'),200),
                'description':text(row.get('desc') if source=='home' else row.get('text'),300),
                'up_name':text(row.get('up_name'),80),'source':source,
                'url':'https://www.bilibili.com/video/'+bvid})
            if len(result)>=limit:
                break
        return {'source':source,'items':result,'filtered_seen':filtered_seen,
                'untrusted_content':True,'reason':'ok' if result else 'no_candidates_or_unavailable',
                'note':'只提供当前一页的标题与简介，不代表已观看；空列表时可换其他来源或搜索。'}

    async def life_action(self,args):
        await self.open()
        action=args.get('action')
        if action=='remember':
            text=str(args.get('text') or '').strip()
            if not text or len(text)>1200:
                raise ValueError('Invalid memory candidate')
            import hashlib
            saved=await self.runtime.shared_memory.remember({'rpid':'native-life:'+hashlib.sha256(text.encode()).hexdigest(),
                'user_id':'self','memory_type':'chat','text':text})
            return {'saved':bool(saved),'backend':'hermes'}
        if action=='maintain':
            return {'result':await self.runtime._consolidation.run_daily()}
        if action=='comment':
            if not self.runtime.config.get('PROACTIVE_COMMENT',False):
                return {'skipped':True,'reason':'主动评论未启用'}
            import re
            from .core.runtime import ActionRequest
            from .core.config import BILI_REPLY_URL
            from .core.security.redact import contains_credentials,redact_outbound
            bvid=str(args.get('bvid') or '').strip()
            text=str(args.get('text') or '').strip()
            if not re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid) or not text or len(text)>500 or contains_credentials(text):
                raise ValueError('Invalid video comment')
            text=redact_outbound(text,internal=False)[0]
            oid=await self.runtime._get_video_oid(bvid)
            if not oid:
                return {'skipped':True,'reason':'视频不存在或信息不可读'}
            async def send():
                try:
                    payload,_=await self.runtime._http_post(BILI_REPLY_URL,
                        data={'oid':int(oid),'type':1,'message':text,'csrf':self.runtime.config.get('BILI_JCT','')},retries=0)
                    if not isinstance(payload,dict) or 'code' not in payload:
                        raise ValueError('Missing result')
                except Exception as exc:
                    raise TimeoutError('评论发送结果未确认，不自动重发') from exc
                return payload['code']==0
            outcome=await self.runtime.event_runtime.execute(ActionRequest(
                key='native_life_comment:'+bvid,kind='proactive_comment',
                event_key='native_life_comment:'+bvid,target_id=str(oid)),send)
            return {'sent':bool(outcome.success and not outcome.duplicate),
                    'already_sent':bool(outcome.success and outcome.duplicate),
                    'duplicate':bool(outcome.duplicate),'state':outcome.state,'reason':outcome.reason}
        if action=='dynamic':
            if not self.runtime.config.get('ENABLE_DYNAMIC',False):
                return {'skipped':True,'reason':'自主动态未启用'}
            from .core.config import DYNAMIC_LOG_FILE
            from .core.security.redact import contains_credentials,redact_outbound
            text=str(args.get('text') or '').strip()
            if not text or len(text)>1000 or contains_credentials(text):
                raise ValueError('Invalid dynamic text')
            text=redact_outbound(text,internal=False)[0]
            success=await self.runtime._queue_dynamic_post(text,lambda:self.runtime._post_dynamic_text(text,strict=True))
            if success:
                from datetime import datetime
                rows=self.runtime._load_json(DYNAMIC_LOG_FILE,[])
                rows.append({'time':datetime.now().strftime('%Y-%m-%d %H:%M'),'text':text,'native_agent':True})
                self.runtime._save_json(DYNAMIC_LOG_FILE,rows[-100:])
                await self.runtime._save_self_memory_record('dynamic',text,memory_type='dynamic')
            return {'sent':bool(success)}
        if action=='watch':
            if not self.runtime.config.get('ENABLE_PROACTIVE',False):
                return {'skipped':True,'reason':'自主浏览未启用'}
            from .core.runtime import ActionRequest
            from datetime import datetime
            import re
            bvid=str(args.get('bvid') or '')
            if not re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid):
                raise ValueError('Invalid BV ID')
            if await self.runtime._seen_video_record(bvid):
                return await self.runtime._watch_video_and_save_memory(bvid, memory_source='native_life')
            result={}
            async def watch():
                value=await self.runtime._watch_video_and_save_memory(bvid, memory_source='native_life')
                result.update(value)
                return bool(value.get('ok'))
            outcome=await self.runtime.event_runtime.execute(ActionRequest(
                key='native_watch:'+datetime.now().strftime('%Y-%m-%d')+':'+bvid,
                kind='proactive_watch',event_key='native_life_watch:'+bvid,target_id=bvid,
                metadata={'native_round':str(args.get('_native_round') or '')}),watch)
            return result or {'skipped':True,'reason':outcome.reason}
        raise ValueError('Unknown native life action')

    async def control(self,args):
        action=args.get('action','status')
        if action=='status':
            return {'running':self.runtime._running,'credentials_present':self.runtime._has_cookie(),
                    'limits':LIMITS,'social_limits':SOCIAL_LIMITS,'memory_backend':'hermes','reply_engine':'hermes_agent','scheduler':'hermes_cron','native_life_enabled':bool(self.runtime.config.get('NATIVE_LIFE_ENABLED')),'video_subtitles_only':bool(self.runtime.config.get('VIDEO_SUBTITLES_ONLY')),'live_enabled':bool(self.runtime.config.get('ENABLE_LIVE_DANMAKU_REPLY')),
                    'live_room_id':self.runtime._live_room_id(),
                    'live_running':bool(self.runtime._live_danmaku_task and not self.runtime._live_danmaku_task.done()),'tools':[t.name for t in self.runtime.context.tools]}
        if action=='check':
            ok,msg=await self.runtime.check_cookie()
            return {'valid':ok,'message':msg}
        if action=='start':
            await self.open(start=True)
        elif action=='stop':
            await self.runtime._stop_bot()
        elif action=='login':
            qr=await self.runtime.generate_qr()
            self.runtime._login_qrcode_key=qr['key']
            return {'login_url':qr['url'],'next':'Scan on Bilibili, then confirm; do not share login URL publicly'}
        elif action=='confirm':
            key=self.runtime._login_qrcode_key
            if not key:
                raise ValueError('Generate a login QR first')
            code,message,cookies=await self.runtime._qr_login_poll(key)
            if code==0:
                for source,destination in [('SESSDATA','SESSDATA'),('bili_jct','BILI_JCT'),
                        ('DedeUserID','DEDE_USER_ID'),('REFRESH_TOKEN','REFRESH_TOKEN')]:
                    if cookies.get(source):
                        self.runtime.config[destination]=cookies[source]
                self.runtime.config.save_config()
                self.runtime._login_qrcode_key=None
            return {'code':code,'message':message}
        elif action=='plan':
            from .native_life import jobs_state
            from cron.jobs import get_job
            return {'scheduler':'hermes_cron','jobs':{kind:get_job(job_id) for kind,job_id in jobs_state()['jobs'].items()}}
        elif action in {'life_start','life_pause'}:
            from .native_life import install_jobs,jobs_state
            from cron.jobs import pause_job,resume_job
            enabled=action=='life_start'
            self.runtime.config['NATIVE_LIFE_ENABLED']=enabled
            self.runtime.config.save_config()
            state=install_jobs() if enabled else jobs_state()
            for job_id in state['jobs'].values():
                (resume_job if enabled else pause_job)(job_id)
            return {'scheduler':'hermes_cron','enabled':enabled,'jobs':state['jobs']}
        elif action=='live':
            room=args.get('room_id')
            if isinstance(room,bool) or not isinstance(room,int) or room<0:
                raise ValueError('Provide room_id as a non-negative integer; zero leaves the room')
            await self.runtime._stop_live_danmaku_listener()
            self.runtime.config['ENABLE_LIVE_DANMAKU_REPLY']=True
            self.runtime.config['LIVE_DANMAKU_ROOM_ID']=room
            self.runtime.config.save_config()
            if room:
                await self.open(start=True)
                await self.runtime._start_live_danmaku_listener()
        elif action=='dynamic':
            await self.open()
            return await self.runtime._run_dynamic(human_initiated=True)
        else:
            raise ValueError('Unknown Bilibili management action')
        return await self.control({'action':'status'})

    def close(self):
        if not self.thread.is_alive() or self.error:
            return
        try:
            self.submit(self.runtime.terminate(),timeout=8)
        finally:
            if self.server:
                self.loop.call_soon_threadsafe(self.server.close)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=3)
            if self.lock_fd is not None:
                (self.home/'bilibili'/'runtime.sock').unlink(missing_ok=True)
                os.close(self.lock_fd)
                self.lock_fd=None

_host=None
_host_lock=threading.Lock()
def host():
    global _host
    with _host_lock:
        if _host is None:
            from .ipc import Proxy,RuntimeAlreadyOwned
            if is_gateway():
                try:
                    _host=Host(service=True)
                except RuntimeAlreadyOwned:
                    _host=Proxy(get_hermes_home())
            elif (get_hermes_home()/'bilibili'/'runtime.sock').exists():
                _host=Proxy(get_hermes_home())
            else:
                _host=Host()
        if _host.home!=get_hermes_home():
            raise RuntimeError('This Bilibili runtime must run in its owning Hermes profile')
        return _host

def is_gateway():
    from gateway.status import looks_like_gateway_runtime_command_line
    from agent.runtime_session_store import is_worker_process
    argv=list(sys.argv)
    # Published Hermes launcher uses python -I -c and leaves a placeholder in argv[0].
    entry=sys.modules.get('hermes_cli.main')
    if argv[:1]==['-c'] and getattr(entry,'__file__',None):
        argv[0]=entry.__file__
    return not is_worker_process() and looks_like_gateway_runtime_command_line(shlex.join(argv))

def schemas():
    # Registry discovery must never start a poller in every CLI/worker that imports the plugin.
    from .core.tools import create_tools
    stub=SimpleNamespace(config=Config(get_hermes_home()))
    return [{'name':t.name,'description':t.description,'parameters':t.parameters} for t in create_tools(stub)]

def handler(name):
    def invoke(args,**kw):
        try:
            if name=='bili_recall':
                from gateway.session_context import get_session_env
                if get_session_env('HERMES_SESSION_PLATFORM','') and not is_operator():
                    return json.dumps({'error':'Bilibili personal memory is available only to the operator'})
            h=host()
            return json.dumps({'result':h.submit(h.invoke(name,args))},ensure_ascii=False)
        except Exception as exc:
            # Never echo credentials/HTTP details from account APIs into a public chat.
            return json.dumps({'error':'Bilibili operation failed','type':type(exc).__name__},ensure_ascii=False)
    return invoke

def is_operator():
    from gateway.session_context import get_session_env
    cfg=load_config_readonly()
    owner=(((((cfg.get('plugins') or {}).get('entries') or {}).get('qq-group-tools') or {}).get('settings') or {}).get('owner_id'))
    owner=owner or plugin_settings().get('owner_qq')
    return bool(owner and get_session_env('HERMES_SESSION_PLATFORM','')=='qqbot'
        and get_session_env('HERMES_SESSION_USER_ID','')==str(owner))

def manage(args,**kw):
    from gateway.session_context import get_session_env
    platform=get_session_env('HERMES_SESSION_PLATFORM','')
    if platform:
        if not is_operator():
            return json.dumps({'error':'Only the configured operator may manage the Bilibili account'})
        if args.get('action') in {'login','confirm'} and get_session_env('HERMES_SESSION_CHAT_TYPE','') not in {'dm','private'}:
            return json.dumps({'error':'Login must be requested in the operator private chat or terminal'})
    h=host()
    try:
        return json.dumps(h.submit(h.control(args)),ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'error':'Bilibili management failed','type':type(exc).__name__})
