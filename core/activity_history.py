"""Profile-local activity receipts, independent of curated long-term memory."""
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('hermes_bilibili')


def record_activity(data_dir, *, key, kind, status, bvid='', title='', text='',
                    analysis_basis='', from_cache=False, note='', rpid='', at=None):
    """Never retry a real send merely because writing its local receipt failed."""
    try:
        path = Path(data_dir) / 'bilibot.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.time() if at is None else float(at)
        day = datetime.fromtimestamp(stamp).strftime('%Y-%m-%d')
        body = json.dumps(dict(bvid=str(bvid), title=str(title)[:300], text=str(text)[:1000],
            analysis_basis=str(analysis_basis), from_cache=bool(from_cache), note=str(note)[:800], rpid=str(rpid)), ensure_ascii=False)
        with sqlite3.connect(path, timeout=15) as db:
            db.execute('CREATE TABLE IF NOT EXISTS native_activity (key TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,day TEXT NOT NULL,at REAL NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS native_activity_day ON native_activity(day,at)')
            db.execute('INSERT INTO native_activity VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET status=excluded.status,day=excluded.day,at=excluded.at,payload=excluded.payload WHERE native_activity.status NOT IN (\'sent\',\'already_sent\') AND excluded.status=\'sent\'',
                       (str(key), str(kind), str(status), day, stamp, body))
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        logger.warning('Activity receipt not saved (%s); do not repeat a possibly delivered action', type(exc).__name__)
        return False


def record_browse(data_dir, key, bvid, result, *, at=None):
    info = result.get('video_info') or {}
    status = result.get('status')
    if not status:
        if result.get('inferred') or result.get('analysis_basis') == 'metadata':
            status = 'metadata_only'
        elif result.get('skipped'):
            status = 'skipped'
        elif result.get('content_analyzed'):
            status = 'subtitles_read' if result.get('analysis_basis') == 'subtitles' else 'media_read'
        else:
            status = 'basis_unknown' if result.get('ok') else 'unavailable'
    return record_activity(data_dir, key=key, kind='browse', status=status, bvid=bvid,
        title=info.get('title', ''), analysis_basis=result.get('analysis_basis', 'unknown'),
        from_cache=result.get('from_cache', False), note=result.get('evidence_note') or result.get('reason') or '', at=at)


def list_activity(data_dir, date, *, kind='all', limit=30):
    datetime.strptime(date, '%Y-%m-%d')
    if not data_dir or not (Path(data_dir) / 'bilibot.sqlite3').exists():
        return []
    if kind not in {'all', 'browse', 'comment'}:
        raise ValueError('Unknown activity kind')
    with sqlite3.connect('file:' + str(Path(data_dir) / 'bilibot.sqlite3') + '?mode=ro', uri=True, timeout=15) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_activity'").fetchone():
            return []
        rows = db.execute('SELECT key,kind,status,at,payload FROM native_activity WHERE day=? AND (?=\'all\' OR kind=?) ORDER BY at DESC,key LIMIT ?',
                          (date, kind, kind, max(1, min(100, int(limit))))).fetchall()
    return [dict(json.loads(payload), key=key, kind=k, status=status,
                 time=datetime.fromtimestamp(at).strftime('%Y-%m-%d %H:%M:%S')) for key, k, status, at, payload in rows]


def daily_usage(data_dir, config, date):
    from types import SimpleNamespace
    from .behavior_budget import BehaviorBudget
    at = datetime.strptime(date, '%Y-%m-%d').replace(hour=12).timestamp()
    counts = {}
    path = Path(data_dir) / 'bilibot.sqlite3'
    if path.exists():
        with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=15) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='counters'").fetchone():
                counts = dict(db.execute('SELECT bucket,count FROM counters WHERE window_key=?', (date,)))
    budget = BehaviorBudget(config.get)
    report = {}
    for kind in ['proactive_watch', 'proactive_comment', 'comment_reply', 'private_reply', 'post_dynamic']:
        rules = budget.rules_for(SimpleNamespace(kind=kind, metadata={}), at)
        own = [(b, cap) for b, window, cap in rules if b == 'behavior:' + kind + ':day']
        used = counts.get('behavior:' + kind + ':day', 0)
        cap = own[0][1] if own else None
        report[kind] = {'used': used, 'limit': cap, 'remaining': max(0, cap-used) if cap is not None else None}
    report['global'] = {'used': counts.get('behavior:global:day', 0),
                        'limit': budget._int('BEHAVIOR_GLOBAL_DAILY_LIMIT', 200) if config.get('BEHAVIOR_BUDGET_ENABLED', True) else None}
    return {'date': date, 'usage': report, 'note': '计入预算的已确认及未确认操作；已退额的失败尝试不计入。浏览流水条数不等于已用看片额度。'}
