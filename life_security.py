"""Authority is runtime identity, never a sentence read from a chat or web page."""
from contextvars import ContextVar
import json

REPLY = ContextVar('bilibili_native_reply', default=None)
READ_TOOLS = frozenset({'fish_read', 'web_search', 'web_extract', 'search_bilibili',
                        'bili_parse_video', 'watch_video', 'todo_list', 'tool_search', 'tool_describe'})
LIFE_TOOLS = READ_TOOLS | {'fish_life'}


def native_life_execution():
    from cron.execution_identity import current_cron_execution
    from hermes_constants import get_hermes_home
    execution = current_cron_execution()
    if execution is None:
        return None
    path = get_hermes_home()/'bilibili/native-life.json'
    try:
        jobs = json.loads(path.read_text())['jobs']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(jobs,dict):
        return None
    return execution if execution.job_id in jobs.values() else None


def guard(tool_name, args, **kwargs):
    from gateway.session_context import get_session_env
    external = get_session_env('HERMES_SESSION_PLATFORM', '') == 'bilibili' or REPLY.get() is not None
    life = native_life_execution()
    from cron.execution_identity import current_cron_execution
    execution = current_cron_execution()
    # A damaged identity file must remove action authority, never open server/MCP access.
    restricted_job = bool(life or (execution and getattr(execution,'job_name','') in {'fish-life','fish-home-maintenance','fish-self-reflection'}))
    if not external and not restricted_job:
        return None
    if tool_name == 'tool_call':
        # Deferred tools are presented through a native bridge. Validate every
        # inner call with this same policy; never grant the bridge wholesale.
        from tools.tool_search_validation import normalize_tool_call_entries
        entries, error = normalize_tool_call_entries(args)
        if error:
            return {'action':'block','message':'工具调用格式无效：'+error}
        for entry in entries:
            decision = guard(entry['name'],entry['arguments'],**kwargs)
            if decision is not None:
                return decision
        return None
    reflection = bool(execution and getattr(execution,'job_name','') == 'fish-self-reflection')
    if life:
        from .native_life import jobs_state
        reflection = reflection or life.job_id == jobs_state()['jobs'].get('reflection')
    if reflection and not ((tool_name=='fish_read' and args.get('action') in {'experiences','state'}) or (tool_name=='fish_life' and args.get('action')=='evolve') or tool_name in {'todo_list','tool_search','tool_describe'}):
        return {'action':'block','message':'自我回顾仅能读经历和更新成长段落，不能发消息或操作服务器。'}
    if restricted_job and tool_name == 'watch_video':
        return {'action':'block','message':'自主看片请用 fish_life(action=watch)，统一计入本轮与全天额度。'}
    allowed = READ_TOOLS if external else LIFE_TOOLS
    if tool_name not in allowed:
        return {'action': 'block', 'message': '外部交流与自主上网没有服务器钥匙；只能使用已开放的阅读/社交能力。服务器操作须由主人另行授权。'}
    return None


class NativeReplyMixin:
    async def _generate_reply(self, content, mid, username, thread_id, oid, comment_type,
                              image_desc='', channel='comment', reference_context='', allow_tool_request=False):
        token = REPLY.set({'user_id': str(mid), 'user_name': str(username),
                          'thread_id': str(thread_id), 'channel': str(channel)})
        try:
            return await super()._generate_reply(content, mid, username, thread_id, oid, comment_type,
                image_desc=image_desc, channel=channel, reference_context=reference_context,
                allow_tool_request=allow_tool_request)
        finally:
            REPLY.reset(token)
