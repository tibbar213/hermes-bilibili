"""API acceptance and bounded read-back are distinct evidence, never a resend trigger."""
import json
import time

from .traffic_guard import connect
from .request_guard import data_directory


def _schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS comment_delivery (id TEXT PRIMARY KEY,at REAL,rpid TEXT,oid TEXT,type INTEGER,root TEXT,text TEXT,verification TEXT)')


def record_comment(directory,token,body,payload):
    if not hasattr(body,'get'):
        return
    data=payload.get('data') or {}
    rpid=data.get('rpid') or (data.get('reply') or {}).get('rpid') or ''
    with connect(directory) as db:
        _schema(db)
        db.execute('INSERT OR IGNORE INTO comment_delivery VALUES(?,?,?,?,?,?,?,?)',
            (token,time.time(),str(rpid),str(body.get('oid') or ''),int(body.get('type') or 1),str(body.get('root') or ''),str(body.get('message') or '')[:1000],json.dumps({'status':'api_accepted','visible':None})))


def _contains(value,rpid):
    if isinstance(value,dict):
        if str(value.get('rpid_str') or value.get('rpid') or '')==rpid:
            return True
        return any(_contains(item,rpid) for key,item in value.items() if key in {'replies','top_replies','upper','top','admin','data'})
    if isinstance(value,list):
        return any(_contains(item,rpid) for item in value)
    return False


async def verify_comment(plugin,query=''):
    directory=data_directory(plugin.config)
    with connect(directory) as db:
        _schema(db)
        row=db.execute('SELECT id,rpid,oid,type,root,verification FROM comment_delivery WHERE (?=\'\' OR id=? OR rpid=?) ORDER BY at DESC LIMIT 1',(query,query,query)).fetchone()
    if not row:
        return {'status':'no_receipt','note':'没有可核验的发送回执，历史记录不会凭空补出评论ID。'}
    token,rpid,oid,kind,root,old=row
    if not rpid or not oid:
        return {'receipt_id':token,'status':'api_accepted','visible':None,'note':'接口已受理，但没有评论ID，不能证明公开可见。'}
    from .config import USER_AGENT
    results={}
    for anonymous,label in [(False,'authenticated'),(True,'anonymous')]:
        found=False
        pages=0
        next_cursor=0
        try:
            for page in range(3):
                if root and root!='0':
                    endpoint='https://api.bilibili.com/x/v2/reply/reply'
                    params={'oid':oid,'type':kind,'root':root,'pn':page+1,'ps':20}
                else:
                    endpoint='https://api.bilibili.com/x/v2/reply/main'
                    params={'oid':oid,'type':kind,'mode':3,'next':next_cursor,'ps':20}
                headers={'User-Agent':USER_AGENT,'Referer':'https://www.bilibili.com/'} if anonymous else plugin._headers()
                payload,_=await plugin._http_get(endpoint,params=params,headers=headers,retries=0)
                pages+=1
                if not isinstance(payload,dict) or payload.get('code')!=0:
                    break
                data=payload.get('data') or {}
                if _contains(data,str(rpid)):
                    found=True
                    break
                cursor=data.get('cursor') or {}
                if cursor.get('is_end') or not (data.get('replies') or []):
                    break
                if not root and cursor.get('next') is not None:
                    # Main-list cursors are opaque; use exactly what the API returns.
                    if cursor['next']==next_cursor:
                        break
                    next_cursor=cursor['next']
                elif not root:
                    break
            results[label]={'found':found,'pages':pages,'status':'found' if found else 'unconfirmed'}
        except Exception as exc:
            from .request_guard import BilibiliPaused
            results[label]={'found':False,'pages':pages,'status':'paused' if isinstance(exc,BilibiliPaused) else 'read_failed'}
            if isinstance(exc,BilibiliPaused):
                break
    visible=True if results.get('anonymous',{}).get('found') else None
    report={'receipt_id':token,'rpid':rpid,'status':'publicly_observed' if visible else 'readback_observed' if results.get('authenticated',{}).get('found') else 'unconfirmed',
            'visible':visible,'checks':results,'checked_at':time.time(),
            'note':'查到才证明此次回读可见；有限分页未查到不等于被删或不可见，不自动重发。'}
    with connect(directory) as db:
        db.execute('UPDATE comment_delivery SET verification=? WHERE id=?',(json.dumps(report,ensure_ascii=False),token))
    return report
