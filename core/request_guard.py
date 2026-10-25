"""Persistent Bilibili pause and comment-reply dispatch limits."""
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit


class BilibiliPaused(RuntimeError):
    pass


class BilibiliNotDispatched(BilibiliPaused):
    """Rejected before the HTTP attempt; it is safe to report a known failure."""
    pass


def is_bilibili_url(url):
    host = (urlsplit(str(url)).hostname or '').lower()
    return any(host == domain or host.endswith('.'+domain) for domain in
               ('bilibili.com', 'biliapi.net', 'biliapi.com', 'bilivideo.com', 'hdslb.com', 'b23.tv'))


def data_directory(config):
    directory = getattr(config, 'data_dir', None)
    if directory is None:
        from .config import DATA_DIR
        directory = DATA_DIR
    return Path(directory)


@contextmanager
def _connect(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory/'bilibot.sqlite3', timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS request_guard (key TEXT PRIMARY KEY,value REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS reply_dispatch (id TEXT PRIMARY KEY,at REAL NOT NULL,day TEXT NOT NULL,state TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS reply_dispatch_at ON reply_dispatch(at)')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def check_pause(directory, *, at=None):
    at = time.time() if at is None else at
    with _connect(directory) as db:
        row = db.execute("SELECT value FROM request_guard WHERE key='paused_until'").fetchone()
    if row and row[0] > at:
        raise BilibiliNotDispatched(f'B站请求冷却中，还需等待{max(1, int(row[0]-at))}秒；不立即重试')


def pause_requests(directory, *, at=None):
    at = time.time() if at is None else at
    with _connect(directory) as db:
        db.execute("INSERT INTO request_guard VALUES ('paused_until',?) ON CONFLICT(key) DO UPDATE SET value=max(value,excluded.value)", (at+300,))
    return at+300


def _limit(config, key, default):
    try:
        return max(0, int(config.get(key, default)))
    except (ValueError, TypeError):
        return default


def reserve_reply(directory, config, *, at=None):
    """Reserve immediately before the one HTTP attempt; failures also consume pacing slots."""
    at = time.time() if at is None else float(at)
    day = datetime.fromtimestamp(at).strftime('%Y-%m-%d')
    with _connect(directory) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT value FROM request_guard WHERE key='paused_until'").fetchone()
        if row and row[0] > at:
            return None, 'B站请求正在冷却'
        last = db.execute('SELECT max(at) FROM reply_dispatch').fetchone()[0]
        gap = _limit(config, 'COMMENT_REPLY_MIN_INTERVAL', 0)
        if last is not None and at < last+gap:
            return None, '评论回复发送间隔未满'
        hourly = _limit(config, 'COMMENT_REPLY_HOURLY_LIMIT', 0)
        if hourly and db.execute('SELECT count(*) FROM reply_dispatch WHERE at>?', (at-3600,)).fetchone()[0] >= hourly:
            return None, '评论回复滚动一小时额度已满'
        daily = _limit(config, 'AUTONOMOUS_REPLY_DAILY_LIMIT', 20)
        if daily and db.execute('SELECT count(*) FROM reply_dispatch WHERE day=?', (day,)).fetchone()[0] >= daily:
            return None, '评论回复当天发送次数已满'
        token = uuid.uuid4().hex
        db.execute('INSERT INTO reply_dispatch VALUES (?,?,?,?)', (token, at, day, 'pending'))
    return token, ''


def finish_reply(directory, token, state):
    with _connect(directory) as db:
        db.execute('UPDATE reply_dispatch SET state=? WHERE id=?', (state, token))


def cancel_reply(directory, token):
    """No network attempt occurred; do not charge the reply pacing ledger."""
    with _connect(directory) as db:
        db.execute('DELETE FROM reply_dispatch WHERE id=?',(token,))


def guard_status(directory, config, *, at=None):
    at = time.time() if at is None else float(at)
    day = datetime.fromtimestamp(at).strftime('%Y-%m-%d')
    with _connect(directory) as db:
        pause = db.execute("SELECT value FROM request_guard WHERE key='paused_until'").fetchone()
        last = db.execute('SELECT max(at) FROM reply_dispatch').fetchone()[0]
        hour = db.execute('SELECT count(*) FROM reply_dispatch WHERE at>?', (at-3600,)).fetchone()[0]
        oldest = db.execute('SELECT min(at) FROM reply_dispatch WHERE at>?', (at-3600,)).fetchone()[0]
        today = db.execute('SELECT count(*) FROM reply_dispatch WHERE day=?', (day,)).fetchone()[0]
    ready = max(at, pause[0] if pause else 0, (last+_limit(config,'COMMENT_REPLY_MIN_INTERVAL',0)) if last is not None else at)
    cap_hour = _limit(config,'COMMENT_REPLY_HOURLY_LIMIT',0)
    cap_day = _limit(config,'AUTONOMOUS_REPLY_DAILY_LIMIT',20)
    if cap_hour and hour >= cap_hour:
        ready = max(ready, oldest+3600)
    if cap_day and today >= cap_day:
        ready = max(ready, (datetime.fromtimestamp(at).replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).timestamp())
    return {'next_reply_at':ready, 'paused_until': pause[0] if pause else 0, 'pause_remaining_seconds': max(0, (pause[0] if pause else 0)-at),
            'minimum_interval_seconds':_limit(config,'COMMENT_REPLY_MIN_INTERVAL',0),
            'hourly_limit':_limit(config,'COMMENT_REPLY_HOURLY_LIMIT',0), 'hourly_attempts':hour,
            'daily_limit':_limit(config,'AUTONOMOUS_REPLY_DAILY_LIMIT',20), 'daily_attempts':today,
            'last_attempt_at':last, 'note':'实际发起的回复请求计数，包含失败及结果未确认；拦截在发送前的请求不计入。'}
