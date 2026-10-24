from __future__ import annotations
import os, sys, io, time, asyncio, random, traceback, logging
from datetime import datetime
from .core.config import *
from .core import *
from .native_types import Image, Plain
from .shared_memory import NativeMemoryMixin, SharedMemory, EventHistoryMaintenance
from .life_security import NativeReplyMixin
logger = logging.getLogger("hermes_bilibili")
_ACTIVE_BILIBOT = None
_BILIBOT_MAIN_TASK_NAME = "hermes-bilibili.main-loop"

class BilibiliRuntime(NativeReplyMixin, NativeMemoryMixin, UtilsMixin, LLMMixin, VisionMixin, MemoryMixin, AffectionMixin, PersonalityMixin, BilibiliAPIMixin, BangumiMixin, WebSearchMixin, VideoMixin, ReplyMixin, ProactiveMixin, DynamicMixin, ScheduleMixin, WeeklySummaryMixin, ShareMixin, PrivateMessageMixin, LiveDanmakuMixin):
    async def _web_search(self, query):
        from tools.web_tools import web_search_tool
        return await asyncio.to_thread(web_search_tool, query, limit=3)


    def __init__(self, context: Context, config: AstrBotConfig):
        self.context = context
        self.config = config
        self.shared_memory = SharedMemory(config.home, context)
        self._ensure_data_dir()
        self._running = False
        self._task = None
        self._start_lock = asyncio.Lock()
        self._memory_write_lock = asyncio.Lock()
        self._proactive_task = None
        self._bangumi_task = None
        self._cross_platform_activity = {}
        self._last_cookie_check = 0
        self._login_qrcode_key = None
        self._first_poll = True
        self._replied_at = set(self._load_json(REPLIED_AT_FILE, []))
        self._affection = self._load_json(AFFECTION_FILE, {})
        owner_mid = str(self.config.get('OWNER_MID', '') or '').strip()
        if owner_mid:
            self._affection[owner_mid] = 100
            self._save_json(AFFECTION_FILE, self._affection)
        self._memory = [self._normalize_memory_entry(m) for m in self._load_json(MEMORY_FILE, []) if isinstance(m, dict)]
        self._embed_client = None
        self._video_vision_client = None
        self._image_vision_client = None
        self._web_search_client = None
        self._consecutive_llm_failures = 0
        self._llm_circuit_open_until = 0.0
        self._llm_circuit_half_open = False
        self._llm_circuit_last_skip_log = 0.0
        self._reply_cooldown_until = 0.0
        self._proactive_windows, self._proactive_times, self._proactive_triggered = ([], [], set())
        self._dynamic_task = None
        self._dynamic_times, self._dynamic_triggered = ([], set())
        self._bangumi_times, self._bangumi_triggered, self._bangumi_update_checked = ([], set(), False)
        self._special_follow_task = None
        self._dynamic_watch_task = None
        self._dynamic_watch_times, self._dynamic_watch_triggered = ([], set())
        self._bili_share_recent = {}
        self._pending_bili_shares = {}
        self._private_message_next_poll_at = 0.0
        self._private_message_backoff_seconds = 0
        self._private_message_success_streak = 0
        self._private_message_last_activity_at = 0.0
        self._private_message_last_warned_backoff = 0
        self.layered_runtime = LayeredRuntime(self.config, LAYERED_DB_FILE)
        try:
            action_timeout = float(self.config.get('BEHAVIOR_ACTION_TIMEOUT_SECONDS', 45) or 45)
        except (TypeError, ValueError):
            action_timeout = 45
        self.event_runtime = EventRuntime(observer=self.layered_runtime, action_timeout=action_timeout)
        self._init_live_danmaku_state()
        self._special_follow_times, self._special_follow_triggered = ([], set())
        self._log_environment_warnings()
        self._consolidation = EventHistoryMaintenance(self)
        self.memory_api = BiliBotMemoryAPI(self)
        global _ACTIVE_BILIBOT
        _ACTIVE_BILIBOT = self
        self._consolidation_task = None
        from .core.tools import create_tools
        llm_tools = create_tools(self)
        self.layered_runtime.register_tools(llm_tools)
        self.context.add_llm_tools(*llm_tools)
        tool_names = ', '.join((tool.name for tool in llm_tools))
        logger.info(f'[BiliBot] LLM工具已精简注册: {len(llm_tools)} 个 ({tool_names})')

    async def initialize(self):
        """Start background work only after AstrBot has formally activated the plugin."""
        try:
            await self.layered_runtime.open()
            logger.info('[BiliBot] 四层运行服务已连接')
        except Exception as exc:
            logger.error(f'[BiliBot] 四层运行服务启动失败，已回退兼容主链: {exc}')
        else:
            try:
                await self._initialize_unified_memory()
            except Exception as exc:
                self._mark_memory_sync_pending(exc)
                logger.error(f'[BiliBot] 统一记忆迁移失败，暂时继续使用 memory.json: {exc}')
            try:
                await self._initialize_seen_videos()
            except Exception as exc:
                logger.error(f'[BiliBot] 永久视频去重账本迁移失败，已保留旧记录兜底: {exc}')
        if not self._has_cookie():
            logger.warning('[BiliBot] Cookie未配置，后台任务未启动')
            return
        valid, msg = await self.check_cookie()
        if valid:
            await self._start_bot()
            logger.info('[BiliBot] 自动启动')
        elif '检查失败' in str(msg):
            logger.warning(f'[BiliBot] Cookie 检查暂时失败（{msg}），仍启动后台任务等待网络恢复')
            await self._start_bot()
        else:
            logger.warning('[BiliBot] Cookie无效')

    async def _start_bot(self):
        async with self._start_lock:
            if self._running and self._task and (not self._task.done()):
                return
            current = asyncio.current_task()
            stale_tasks = [task for task in asyncio.all_tasks() if task is not current and (not task.done()) and (task.get_name() == _BILIBOT_MAIN_TASK_NAME)]
            for task in stale_tasks:
                task.cancel()
            if stale_tasks:
                await asyncio.gather(*stale_tasks, return_exceptions=True)
                logger.warning(f'[BiliBot] 已清理 {len(stale_tasks)} 个热重载残留主循环')
            await self._ensure_buvid()
            if self.config.get('ENABLE_PRIVATE_MESSAGES', False):
                private_state = self._load_json(PRIVATE_MESSAGE_STATE_FILE, {})
                if isinstance(private_state, dict) and private_state.get('initialized'):
                    try:
                        startup_delay = max(60, min(3600, int(self.config.get('PRIVATE_MESSAGE_IDLE_POLL_INTERVAL', 180) or 180)))
                    except (TypeError, ValueError):
                        startup_delay = 180
                    self._private_message_next_poll_at = time.monotonic() + startup_delay
                    logger.info(f'[BiliBot] 私信监听将在 {startup_delay} 秒启动，避免重载后立即重试接口')
            self._running = True
            self._task = asyncio.create_task(self._main_loop(), name=_BILIBOT_MAIN_TASK_NAME, context=self._background_context.copy())
            if self.config.get('ENABLE_LIVE_DANMAKU_REPLY', False) and self._live_room_id():
                ok, message = await self._start_live_danmaku_listener()
                if not ok:
                    logger.warning(f'[BiliBot] 直播弹幕监听未启动: {message}')
            logger.info('[BiliBot] 启动')

    async def _stop_bot(self):
        await self._stop_live_danmaku_listener()
        async with self._start_lock:
            self._running = False
            task = self._task
            self._task = None
            if task and (not task.done()):
                task.cancel()
                if task is not asyncio.current_task():
                    await asyncio.gather(task, return_exceptions=True)
        if self._proactive_task and (not self._proactive_task.done()):
            self._proactive_task.cancel()
            self._proactive_task = None
        if self._dynamic_task and (not self._dynamic_task.done()):
            self._dynamic_task.cancel()
            self._dynamic_task = None
        if self._bangumi_task and (not self._bangumi_task.done()):
            self._bangumi_task.cancel()
            self._bangumi_task = None
        if self._special_follow_task and (not self._special_follow_task.done()):
            self._special_follow_task.cancel()
            self._special_follow_task = None
        if self._dynamic_watch_task and (not self._dynamic_watch_task.done()):
            self._dynamic_watch_task.cancel()
        self._dynamic_watch_task = None
        self._dynamic_watch_times, self._dynamic_watch_triggered = ([], set())
        self._bili_share_recent = {}
        self._pending_bili_shares = {}
        self._private_message_next_poll_at = 0.0
        self._private_message_backoff_seconds = 0
        self._private_message_success_streak = 0
        self._private_message_last_activity_at = 0.0
        self._private_message_last_warned_backoff = 0
        if self._consolidation_task and (not self._consolidation_task.done()):
            self._consolidation_task.cancel()
            self._consolidation_task = None
        logger.info('[BiliBot] 停止')

    async def _run_consolidation_safe(self):
        """安全执行日终清算，捕获所有异常。"""
        try:
            logger.info('[BiliBot] 🌙 开始日终清算...')
            summary = await self._consolidation.run_daily()
            logger.info(f'[BiliBot] 🌙 日终清算完成:\n{summary}')
        except Exception as e:
            logger.error(f'[BiliBot] 日终清算异常: {e}', exc_info=True)

    async def _main_loop(self):
        """Only transport upkeep and inbound polling; autonomous activities belong to Hermes cron."""
        logger.info('[BiliBot] 消息监听开始；自主活动由 Hermes 原生定时任务调度')
        while self._running:
            try:
                interval = float(self.config.get('COOKIE_CHECK_INTERVAL',6))*3600
                if time.time()-self._last_cookie_check > interval:
                    await self._check_and_refresh_cookie()
                    self._last_cookie_check=time.time()
                hour=datetime.now().hour
                start=int(self.config.get('SLEEP_START',2))
                end=int(self.config.get('SLEEP_END',8))
                sleeping=(start <= hour < end) if start<=end else (hour>=start or hour<end)
                if not sleeping:
                    if self.config.get('ENABLE_PRIVATE_MESSAGES',False):
                        await self._poll_private_messages()
                    if self.config.get('ENABLE_REPLY',True):
                        await self._poll_unified()
                await asyncio.sleep(max(10,float(self.config.get('POLL_INTERVAL',20))))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning('B站监听暂时失败 (%s)',type(exc).__name__)
                await asyncio.sleep(30)
        self._running=False

    async def _check_and_refresh_cookie(self):
        valid, info = await self.check_cookie()
        if valid:
            logger.info(f'[BiliBot] Cookie OK: {info}')
            return
        logger.warning(f'[BiliBot] Cookie 失效: {info}')
        if self.config.get('COOKIE_AUTO_REFRESH', True):
            ok, msg = await self.refresh_cookie()
            logger.info(f"[BiliBot] 刷新{('成功' if ok else '失败')}: {msg}")

    async def terminate(self):
        await self._stop_bot()
        extension_registry = getattr(self, '_bilibot_extension_registry', None)
        if extension_registry is not None:
            try:
                await extension_registry.close()
            except Exception as exc:
                logger.warning(f'[BiliBot] 扩展 Host 关闭异常: {exc}')
        from .core.network import close_plugin_clients
        await close_plugin_clients(self)
        try:
            await self.event_runtime.close()
        except Exception as exc:
            logger.warning(f'[BiliBot] 动作队列关闭异常: {exc}')
        try:
            await self.layered_runtime.close()
        except Exception as exc:
            logger.warning(f'[BiliBot] 四层运行服务关闭异常: {exc}')
        global _ACTIVE_BILIBOT
        if _ACTIVE_BILIBOT is self:
            _ACTIVE_BILIBOT = None
        self._cleanup_temp_files()
        logger.info('[BiliBot] 已停用')

    async def _tool_bili_watch_videos_result(self) -> str:
        if not self._has_cookie():
            return '未登录B站，无法执行主动看视频。请先使用 /bili登录 完成扫码登录。'
        if not self.config.get('ENABLE_PROACTIVE', False):
            return '主动看视频功能当前未开启。请先使用 /bili开关 主动 开启。'
        if self._proactive_task is not None and (not self._proactive_task.done()):
            return '已有主动看视频任务正在运行，无需重复触发。'
        self._proactive_task = asyncio.create_task(self._run_proactive(max_watch=1))
        trigger_log = self._load_json(PROACTIVE_TRIGGER_LOG_FILE, [])
        trigger_log.append({'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'type': 'llm_tool', 'scheduled': 'bili_watch_videos', 'status': 'triggered'})
        self._save_json(PROACTIVE_TRIGGER_LOG_FILE, trigger_log[-200:])
        return '已在后台触发一次主动看B站视频流程。稍后可用 /bili日志 视频 查看结果。'

    def _bili_toggle_items(self):
        return {'回复': 'ENABLE_REPLY', '查询': 'COMMENT_CONTEXT_ENABLED', '私信': 'ENABLE_PRIVATE_MESSAGES', '私信回复': 'PRIVATE_MESSAGE_AUTO_REPLY', '私信拉黑': 'PRIVATE_MESSAGE_AUTO_BLOCK', '直播回复': 'ENABLE_LIVE_DANMAKU_REPLY', '主动': 'ENABLE_PROACTIVE', '动态': 'ENABLE_DYNAMIC', '好感': 'ENABLE_AFFECTION', '心情': 'ENABLE_MOOD', '演化': 'ENABLE_PERSONALITY_EVOLUTION', '工具': 'ENABLE_LLM_TOOLS', '解析': 'ENABLE_BILI_SHARE_PARSE', '自动解析': 'BILI_SHARE_PARSE_AUTO_TRIGGER_ENABLED', '手动解析': 'BILI_SHARE_PARSE_MANUAL_TRIGGER_ENABLED', 'LLM解析': 'BILI_SHARE_PARSE_LLM_TRIGGER_ENABLED', '解析视频': 'BILI_SHARE_PARSE_SEND_VIDEO', '筛选': 'ENABLE_PROACTIVE_LLM_PREFILTER', '点赞': 'PROACTIVE_LIKE', '投币': 'PROACTIVE_COIN', '收藏': 'PROACTIVE_FAV', '关注': 'PROACTIVE_FOLLOW', '评论': 'PROACTIVE_COMMENT'}

    async def _tool_bili_search_memory_result(self, query: str, memory_type: str='', source: str='') -> str:
        type_alias = {'chat': {'chat'}, '交流': {'chat'}, '聊天': {'chat'}, '评论': {'chat'}, 'video': {'video'}, '视频': {'video'}, '观影': {'video'}, 'dynamic': {'dynamic'}, '动态': {'dynamic'}, 'live': {'live'}, '直播': {'live'}, 'user_summary': {'user_summary'}, 'summary': {'user_summary'}, '总结': {'user_summary'}, '压缩': {'user_summary'}}
        selected_types = type_alias.get(memory_type.strip(), None) if memory_type else None
        selected_source = source.strip() or None
        if selected_source == 'all':
            selected_source = None
        results = await self._search_memories(query, limit=5, source=selected_source, memory_types=selected_types)
        if not results:
            return f'没有找到与「{query}」相关的记忆。'
        return '\n'.join([f'{i}. {r}' for i, r in enumerate(results, 1)])

    def _render_video_log(self, target_date, date_given):
        wl = self._load_json(WATCH_LOG_FILE, [])
        today_watch = [l for l in wl if l.get('time', '').startswith(target_date)]
        pl = self._load_json(PROACTIVE_LOG_FILE, [])
        today_comment = [l for l in pl if l.get('time', '').startswith(target_date) and l.get('type') != 'bangumi']
        if not today_watch and (not today_comment):
            return f'📋 {target_date} 没有主动行为记录'
        lines = [f'📋 {target_date} 主动行为日志', '━━━━━━━━━━━━']
        if today_watch:
            lines.append(f'\n🎬 视频处理记录 {len(today_watch)} 条：')
            for i, w in enumerate(today_watch, 1):
                score = w.get('score', '?')
                actions = ' '.join(w.get('actions', [])) or '无互动'
                lines.append(f"  {i}. 「{w.get('title', '?')[:30]}」")
                lines.append(f"     🔗 bilibili.com/video/{w.get('bvid', '')}")
                failed = w.get('evaluation_status') == 'failed' or w.get('comment') == '评价失败'
                if failed:
                    detail = w.get('diagnostic') or {}
                    stage = {'content': '内容不足', 'model_request': '模型请求', 'evaluation_schema': '评价格式校验', 'evaluation_exception': '评价异常'}.get(detail.get('stage'), '评价')
                    lines.append(f"     UP:{w.get('up_name', '?')} | 未评分 | {stage}")
                    lines.append(f"     ⚠️ {detail.get('reason') or '旧记录未保存原因，更新后的新记录会显示详情'}")
                    for error in detail.get('media_errors', [])[-3:]:
                        lines.append(f"     ↳ {error.get('stage', '')}: {error.get('reason', '')}")
                    if detail.get('missing_fields'):
                        lines.append('     缺少字段: ' + ', '.join(detail['missing_fields']))
                    call = detail.get('model_call') or {}
                    if call.get('provider') or call.get('model'):
                        lines.append(f"     模型: {call.get('provider') or call.get('model')}")
                    if 'elapsed_seconds' in detail:
                        lines.append(f"     时间: {detail.get('time', w.get('time', ''))} | 评价耗时: {detail['elapsed_seconds']}秒")
                else:
                    lines.append(f"     UP:{w.get('up_name', '?')} | {score}分 | {w.get('mood', '?')}")
                if w.get('review'):
                    lines.append(f"     📝 {w['review'][:60]}")
                lines.append(f'     {actions}')
        if today_comment:
            lines.append(f'\n💬 发了 {len(today_comment)} 条评论：')
            for i, c in enumerate(today_comment, 1):
                lines.append(f"  {i}. 「{c.get('title', '?')[:30]}」")
                lines.append(f"     💬 {c.get('comment', '?')[:80]}")
        return '\n'.join(lines)

    def _render_bangumi_log(self, target_date, date_given):
        log = self._load_json(BANGUMI_WATCH_LOG_FILE, [])
        today_log = [l for l in log if l.get('time', '').startswith(target_date)]
        if not today_log:
            return f'🎬 {target_date} 没有看番记录'
        lines = [f'🎬 {target_date} 看番日志（共 {len(today_log)} 集）', '━━━━━━━━━━━━']
        for i, l in enumerate(today_log, 1):
            t = l.get('time', '?')
            time_part = t.split(' ', 1)[1] if ' ' in t else t
            lines.append(f"{i}. [{time_part}] 《{l.get('title', '?')}》第{l.get('ep_index', '?')}话")
            lines.append(f"   ⭐{l.get('score', '?')}/10 {l.get('mood', '')} {l.get('review', '')[:40]}")
            if l.get('comment'):
                lines.append(f"   💬 {l['comment'][:40]}")
        return '\n'.join(lines)

    def _render_dynamic_log(self, target_date, date_given):
        log = self._load_json(DYNAMIC_LOG_FILE, [])
        if date_given:
            log = [l for l in log if l.get('time', '').startswith(target_date)]
            if not log:
                return f'📝 {target_date} 没有动态记录'
            header = f'📝 {target_date} 动态记录'
        else:
            if not log:
                return '📝 还没有动态记录'
            header = '📝 最近动态记录'
        lines = [header, '━━━━━━━━━━━━']
        for i, l in enumerate(log[-10:], 1):
            img = '🖼️' if l.get('has_image') else '📄'
            lines.append(f"{i}. [{l.get('time', '')}] {img}")
            lines.append(f"   {l.get('text', '')[:60]}...")
        return '\n'.join(lines)

    def _render_reply_log(self, target_date, date_given):
        reply_log = self._load_json(REPLY_LOG_FILE, [])
        today_replies = [r for r in reply_log if r.get('time', '').startswith(target_date)]
        if not today_replies:
            memory = self._load_json(MEMORY_FILE, [])
            chats = [m for m in memory if m.get('memory_type') == 'chat' and m.get('time', '').startswith(target_date)]
            if not chats:
                return f'💬 {target_date} 没有回复记录'
            lines = [f'💬 {target_date} 回复日志（共 {len(chats)} 条，来自记忆）', '━━━━━━━━━━━━']
            for i, c in enumerate(chats, 1):
                username = c.get('username', '?')
                t = c.get('time', '?')
                time_part = t.split(' ', 1)[1] if ' ' in t else t
                text = c.get('text', '')
                user_msg = ''
                bot_reply = ''
                if '说：' in text and '| Bot回复：' in text:
                    after_said = text.split('说：', 1)[1]
                    parts2 = after_said.split(' | Bot回复：', 1)
                    user_msg = parts2[0].strip()[:60]
                    bot_reply = parts2[1].strip()[:60] if len(parts2) > 1 else ''
                else:
                    user_msg = text[:80]
                lines.append(f'{i}. [{time_part}] {username}')
                lines.append(f'   📨 {user_msg}')
                if bot_reply:
                    lines.append(f'   💬 {bot_reply}')
            return '\n'.join(lines)
        lines = [f'💬 {target_date} 回复日志（共 {len(today_replies)} 条）', '━━━━━━━━━━━━']
        for i, r in enumerate(today_replies, 1):
            t = r.get('time', '?')
            time_part = t.split(' ', 1)[1] if ' ' in t else t
            lines.append(f"{i}. [{time_part}] {r.get('username', '?')}")
            lines.append(f"   📨 {r.get('content', '?')[:60]}")
            lines.append(f"   💬 {r.get('reply', '?')[:60]}")
            lines.append(f"   💛 好感{('+' if r.get('score_delta', 0) >= 0 else '')}{r.get('score_delta', 0)}")
        return '\n'.join(lines)

    def _set_cross_platform_activity(self, kind, stage, **details):
        self._cross_platform_activity = {'kind': kind, 'stage': stage, 'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), **{key: str(value)[:160] for key, value in details.items() if value not in (None, '')}}

    def _clear_cross_platform_activity(self, kind=None):
        if not kind or self._cross_platform_activity.get('kind') == kind:
            self._cross_platform_activity = {}

    def _cross_platform_activity_context(self):
        item = self._cross_platform_activity
        if not item:
            return ''
        labels = {'proactive': '主动浏览', 'bangumi': '追番', 'dynamic': '发布动态', 'dynamic_watch': '巡视关注动态'}
        text = f"【当前活动】正在{labels.get(item.get('kind'), item.get('kind', '处理任务'))}：{item.get('stage', '进行中')}"
        if item.get('title'):
            text += f"；内容：《{item['title']}》"
        if item.get('episode'):
            text += f"；{item['episode']}"
        return text + '。仅在用户询问当前活动或自然相关时提及；不得猜测未记录的进度。'
