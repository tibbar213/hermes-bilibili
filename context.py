"""Model, persona and operator notifications through Hermes-owned interfaces."""
import asyncio
from types import SimpleNamespace

class Persona:
    def __init__(self, home):
        self.home = home

    async def get_default_persona_v3(self):
        path = self.home/'SOUL.md'
        return {'prompt': path.read_text(encoding='utf-8') if path.exists() else '用自然简短的中文交流。'}

class NativeContext:
    def __init__(self, home):
        self.home = home
        self.persona_manager = Persona(home)
        self.tools = []

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)

    def get_using_provider(self):
        return SimpleNamespace(meta=lambda:SimpleNamespace(id='hermes'))

    def get_all_providers(self):
        return [self.get_using_provider()]

    def get_all_stars(self):
        return []

    async def llm_generate(self, prompt, system_prompt='', max_tokens=600, chat_provider_id='hermes', include_memory=True, **kwargs):
        from .life_security import REPLY
        identity = REPLY.get()
        if identity is not None and system_prompt and chat_provider_id != 'hermes-vision':
            from .native_agent import run_reply
            result = await asyncio.to_thread(run_reply,self.home,identity,prompt,system_prompt,max_tokens)
            return SimpleNamespace(completion_text=result)
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning
        if include_memory and chat_provider_id != 'hermes-vision':
            from .shared_memory import shared_context
            notes = shared_context(self.home)
            if notes:
                system_prompt = (system_prompt+'\n\n'+notes).strip()
        messages = []
        if system_prompt:
            messages.append({'role':'system','content':system_prompt})
        messages.append({'role':'user','content':prompt})
        def perform():
            from agent.secret_scope import build_profile_secret_scope,set_secret_scope,reset_secret_scope
            token=set_secret_scope(build_profile_secret_scope(self.home),profile_home=str(self.home))
            try:
                return call_llm(task='vision' if chat_provider_id=='hermes-vision' else None,
                    messages=messages,max_tokens=max_tokens,timeout=90,reasoning_config={'enabled':False})
            finally:
                reset_secret_scope(token)
        result=await asyncio.to_thread(perform)
        return SimpleNamespace(completion_text=extract_content_or_reasoning(result))

    async def send_message(self, target, chain):
        # Historical AstrBot UMO strings are not trusted QQ destinations. Only the configured owner.
        from hermes_cli.config import load_config_readonly
        from tools.send_message_tool import send_message_tool
        from .native_types import Plain, Image
        cfg=load_config_readonly()
        entries=(cfg.get('plugins') or {}).get('entries') or {}
        owner=((entries.get('qq-group-tools') or {}).get('settings') or {}).get('owner_id')
        owner=owner or ((entries.get('hermes-bilibili') or {}).get('settings') or {}).get('owner_qq')
        if not owner:
            raise RuntimeError('Configure QQ owner before enabling cross-platform notifications')
        text='\n'.join(str(x.text) if isinstance(x,Plain) else 'MEDIA:'+str(x.file)
            for x in getattr(chain,'chain',[]) if isinstance(x,(Plain,Image)))
        if not text:
            raise ValueError('This notification needs a native image/file sender; text was empty')
        return await asyncio.to_thread(send_message_tool, {'action':'send','target':'qqbot:'+str(owner),'message':text})
