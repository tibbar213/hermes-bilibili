"""Hermes-native Bilibili account operations and scheduled social behavior."""

def register(ctx):
    from .host import host,handler,manage,plugin_settings,schemas,is_gateway
    for tool in schemas():
        ctx.register_tool(name=tool['name'],toolset='bilibili',schema=tool,
            handler=handler(tool['name']),description=tool['description'],emoji='📺')
        if tool['name'] in {'watch_video','bili_watch_videos','bili_bangumi'}:
            from tools.registry import registry
            entry=registry.get_entry(tool['name'])
            if entry is not None:
                entry.execution_timeout=650
    from .life_security import guard
    ctx.register_hook('pre_tool_call',guard)
    from .native_life import register as register_life
    register_life(ctx)
    from .shared_memory import refresh_qq_memory
    ctx.register_hook('pre_llm_call',refresh_qq_memory)
    schema={'name':'bilibili_manage','description':'仅主人管理B站原生插件：status/check/start/stop/login/confirm/plan/dynamic/live/life_start/life_pause。live传room_id进入直播间，0退出。登录仅在主人私聊或终端操作；dynamic会真实发动态。',
        'parameters':{'type':'object','properties':{'action':{'type':'string','enum':['status','check','start','stop','login','confirm','plan','dynamic','live','life_start','life_pause']},'room_id':{'type':'integer','minimum':0,'description':'live操作必填，房间号；0表示不进入任何房间'}},'required':['action']}}
    ctx.register_tool(name='bilibili_manage',toolset='bilibili',schema=schema,handler=manage,emoji='📺')
    def setup(parser):
        parser.add_argument('--room-id',type=int)
        parser.add_argument('action',choices=schema['parameters']['properties']['action']['enum'],default='status',nargs='?')
    def cli(args):
        print(manage({'action':args.action,'room_id':args.room_id}))
    ctx.register_cli_command('bilibili','Manage the native Bilibili plugin',setup,cli)
    if plugin_settings().get('enabled') is True and is_gateway():
        h=host()
        # Register's owner scope is copied into the loop thread, including profile credentials.
        import asyncio
        future=asyncio.run_coroutine_threadsafe(h.open(start=True),h.loop)
        def started(done):
            import logging
            try:
                done.result()
                if h.runtime.config.get('NATIVE_LIFE_ENABLED',False):
                    from .native_life import install_jobs
                    install_jobs()
            except Exception as exc:
                logging.getLogger('hermes_bilibili').error('Background startup failed (%s); inspect local account status',type(exc).__name__)
        future.add_done_callback(started)
