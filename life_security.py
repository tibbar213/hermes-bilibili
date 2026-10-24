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
    restricted_job = bool(life or (execution and getattr(execution,'job_name','') in {'fish-life','fish-home-maintenance'}))
    if not external and not restricted_job:
        return None
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
