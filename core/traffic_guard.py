"""Profile-wide, cross-process Bilibili request admission and delivery receipts.

Design informed by Alterssss/dsh-bili-whale (MIT); implemented for the existing
Hermes SQLite ledger. Counts actual attempts, never retries uncertain writes.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from urllib.parse import parse_qsl, urlsplit

from .request_guard import BilibiliPaused, check_pause, data_directory

DEFAULTS = {'BILI_REQUEST_GAP_MS':1500, 'BILI_READ_HOURLY':600,
            'BILI_READ_DAILY':3000, 'BILI_WRITE_HOURLY':12,
            'BILI_WRITE_DAILY':30, 'BILI_WRITE_GAP_SECONDS':120}
RISK_CODES = {-412, -352, -799, -509, -10403}


def limits(config):
    result = {}
    for key, default in DEFAULTS.items():
        try:
            value = int(config.get(key, default))
            result[key] = value if value > 0 else default
        except (TypeError, ValueError, OverflowError):
            result[key] = default
    result['BILI_REQUEST_GAP_MS'] = max(600, result['BILI_REQUEST_GAP_MS'])
    result['BILI_WRITE_GAP_SECONDS'] = max(120, result['BILI_WRITE_GAP_SECONDS'])
    return result


@contextmanager
def connect(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory/'bilibot.sqlite3', timeout=15)
    try:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS request_guard (key TEXT PRIMARY KEY,value REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS traffic_attempts (id TEXT PRIMARY KEY,at REAL,day TEXT,writing INTEGER,endpoint TEXT,operation TEXT,state TEXT,rpid TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS traffic_at ON traffic_attempts(at);
        CREATE TABLE IF NOT EXISTS traffic_lease (id INTEGER PRIMARY KEY CHECK(id=1),token TEXT,pid INTEGER,until REAL);
        CREATE TABLE IF NOT EXISTS traffic_risk (id INTEGER PRIMARY KEY CHECK(id=1),streak INTEGER,endpoint TEXT);
        ''')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def fingerprint(url, body):
    ignored = {'csrf','csrf_token','wts','w_rid','rnd','timestamp','msg[timestamp]','msg[dev_id]'}
    if hasattr(body, 'items'):
        values = sorted((str(k), str(v)) for k,v in body.items() if k not in ignored)
    elif isinstance(body, str):
        try:
            obj = json.loads(body)
            values = {k:v for k,v in obj.items() if k not in ignored} if isinstance(obj,dict) else obj
        except (ValueError, TypeError):
            values = sorted((k,v) for k,v in parse_qsl(body,keep_blank_values=True) if k not in ignored)
    elif body is None:
        values = None
    else:
        # Unknown/multipart body: pacing still applies; never guess equivalence.
        return ''
    target = urlsplit(str(url))
    raw = target.netloc+target.path+json.dumps(values,ensure_ascii=False,sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True


def claim(directory, config, url, method, body=None, *, at=None):
    """Return (token, wait_seconds). Only one process can claim a live slot."""
    at = time.time() if at is None else float(at)
    cfg = limits(config)
    check_pause(directory, at=at)
    writing = method.upper() not in {'GET','HEAD','OPTIONS'}
    operation = fingerprint(url,body) if writing else ''
    endpoint = urlsplit(str(url)).path
    with connect(directory) as db:
        db.execute('BEGIN IMMEDIATE')
        pause = db.execute("SELECT value FROM request_guard WHERE key='paused_until'").fetchone()
        if pause and pause[0]>at:
            raise BilibiliPaused('B站请求冷却中，不立即重试')
        lease = db.execute('SELECT token,pid,until FROM traffic_lease WHERE id=1').fetchone()
        if lease and lease[2]>at and _alive(lease[1]):
            return None, .05
        last = db.execute('SELECT max(at) FROM traffic_attempts').fetchone()[0]
        gap = cfg['BILI_REQUEST_GAP_MS']/1000
        if last is not None and last+gap>at:
            return None, last+gap-at
        day = datetime.fromtimestamp(at).strftime('%Y-%m-%d')
        prefix = 'BILI_WRITE_' if writing else 'BILI_READ_'
        hourly = db.execute('SELECT count(*) FROM traffic_attempts WHERE writing=? AND at>?',(int(writing),at-3600)).fetchone()[0]
        daily = db.execute('SELECT count(*) FROM traffic_attempts WHERE writing=? AND day=?',(int(writing),day)).fetchone()[0]
        if hourly>=cfg[prefix+'HOURLY'] or daily>=cfg[prefix+'DAILY']:
            raise BilibiliPaused('B站账号读取额度已满' if not writing else 'B站账号写入额度已满')
        if writing:
            previous = db.execute('SELECT max(at) FROM traffic_attempts WHERE writing=1').fetchone()[0]
            if previous is not None and previous+cfg['BILI_WRITE_GAP_SECONDS']>at:
                raise BilibiliPaused('B站账号写操作间隔未满120秒，不立即重试')
            if operation and db.execute("SELECT 1 FROM traffic_attempts WHERE operation=? AND (state IN ('pending','unknown') OR (state='accepted' AND at>?))",(operation,at-86400)).fetchone():
                raise BilibiliPaused('相同操作已受理或结果未确认，不重复发送')
        token = uuid.uuid4().hex
        db.execute('INSERT INTO traffic_attempts(id,at,day,writing,endpoint,operation,state) VALUES(?,?,?,?,?,?,?)',(token,at,day,int(writing),endpoint,operation,'pending'))
        db.execute('INSERT OR REPLACE INTO traffic_lease VALUES(1,?,?,?)',(token,os.getpid(),at+90))
        # Retain uncertain writes indefinitely; bounded ordinary request history.
        db.execute("DELETE FROM traffic_attempts WHERE at<? AND NOT (writing=1 AND state IN ('pending','unknown'))",(at-7*86400,))
        return token, 0


async def acquire(directory, config, url, method, body=None):
    deadline = time.monotonic()+30
    while True:
        token, wait = claim(directory,config,url,method,body)
        if token:
            return token
        if time.monotonic()+wait>deadline:
            raise BilibiliPaused('B站请求队列繁忙，本次停止')
        await asyncio.sleep(max(.01,wait))


def release(directory, token, state='unknown', rpid=''):
    with connect(directory) as db:
        db.execute('UPDATE traffic_attempts SET state=?,rpid=? WHERE id=?',(state,str(rpid or ''),token))
        db.execute('DELETE FROM traffic_lease WHERE id=1 AND token=?',(token,))


def risk_pause(directory, endpoint, retry_after=None, *, at=None):
    at = time.time() if at is None else float(at)
    delay = 0
    try:
        delay = max(0,float(retry_after or 0))
    except (ValueError, TypeError):
        try:
            delay = max(0,parsedate_to_datetime(retry_after).timestamp()-at)
        except (ValueError, TypeError, OverflowError):
            pass
    with connect(directory) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT streak FROM traffic_risk WHERE id=1').fetchone()
        streak = (row[0] if row else 0)+1
        until = at+max((300,600,1200,1800)[min(streak-1,3)],delay)
        db.execute('INSERT OR REPLACE INTO traffic_risk VALUES(1,?,?)',(streak,endpoint))
        db.execute("INSERT INTO request_guard VALUES('paused_until',?) ON CONFLICT(key) DO UPDATE SET value=max(value,excluded.value)",(until,))
    return until


def mark_success(directory, endpoint, *, at=None):
    at = time.time() if at is None else at
    with connect(directory) as db:
        pause = db.execute("SELECT value FROM request_guard WHERE key='paused_until'").fetchone()
        if not pause or pause[0]<=at:
            db.execute('DELETE FROM traffic_risk WHERE id=1 AND endpoint=?',(endpoint,))


def traffic_status(directory,config,*,at=None):
    at=time.time() if at is None else at
    day=datetime.fromtimestamp(at).strftime('%Y-%m-%d')
    with connect(directory) as db:
        counts={}
        for writing,name in [(0,'read'),(1,'write')]:
            counts[name]={'hourly':db.execute('SELECT count(*) FROM traffic_attempts WHERE writing=? AND at>?',(writing,at-3600)).fetchone()[0],
                          'daily':db.execute('SELECT count(*) FROM traffic_attempts WHERE writing=? AND day=?',(writing,day)).fetchone()[0]}
            prefix='BILI_WRITE_' if writing else 'BILI_READ_'
            cfg=limits(config)
            counts[name]['remaining_hourly']=max(0,cfg[prefix+'HOURLY']-counts[name]['hourly'])
            counts[name]['remaining_daily']=max(0,cfg[prefix+'DAILY']-counts[name]['daily'])
        row=db.execute('SELECT streak FROM traffic_risk WHERE id=1').fetchone()
    return {'limits':limits(config),'attempts':counts,'risk_streak':row[0] if row else 0,'single_concurrency':True}
