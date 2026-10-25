"""Plugin-local proxy settings; never modify process-wide proxy variables."""
from contextlib import asynccontextmanager
import re
from urllib.parse import unquote, urlsplit, urlunsplit

import aiohttp
import asyncio
import sys


class _GuardedResponse:
    def __init__(self, response, context):
        self._response, self._context = response, context

    def __getattr__(self, name):
        return getattr(self._response, name)

    async def json(self, *args, **kwargs):
        payload = await self._response.json(*args, **kwargs)
        from .traffic_guard import RISK_CODES, risk_pause, mark_success
        from .request_guard import BilibiliPaused
        code = payload.get('code') if isinstance(payload, dict) else None
        endpoint = urlsplit(str(self._response.url)).path
        if code in RISK_CODES:
            risk_pause(self._context.directory, endpoint, self._response.headers.get('Retry-After'))
            self._context.state = 'failed'
            raise BilibiliPaused('B站返回风控码，请求已递增暂停，不立即重试')
        if self._response.status < 400 and (code is None or code == 0):
            self._context.state = 'accepted'
            data = payload.get('data') or {} if isinstance(payload,dict) else {}
            if isinstance(data,dict):
                self._context.rpid = data.get('rpid') or (data.get('reply') or {}).get('rpid') or ''
            mark_success(self._context.directory, endpoint)
            if self._context.method=='POST' and endpoint=='/x/v2/reply/add' and code==0:
                # Receipt failure must never turn an accepted write into a retry.
                try:
                    from .comment_delivery import record_comment
                    record_comment(self._context.directory,self._context.token,self._context.kwargs.get('data'),payload)
                except Exception:
                    import logging
                    logging.getLogger('hermes_bilibili').warning('评论已受理，但本地回执未保存；不要重发')
        else:
            self._context.state = 'failed'
        return payload


class _GuardedRequest:
    def __init__(self,session,config,method,url,kwargs):
        from .request_guard import data_directory
        self.session,self.config,self.method,self.url,self.kwargs=session,config,method,url,kwargs
        self.directory=data_directory(config)
        self.state,self.rpid,self.token='unknown','',None
        self.response=None

    async def __aenter__(self):
        from .traffic_guard import acquire,release,risk_pause
        from .request_guard import BilibiliPaused
        self.token=await acquire(self.directory,self.config,self.url,self.method,self.kwargs.get('data',self.kwargs.get('json')))
        try:
            # The persistent lease exceeds this complete request/body deadline.
            self.deadline=asyncio.timeout(45)
            await self.deadline.__aenter__()
            self.kwargs['allow_redirects']=False
            if self.method not in {'GET','HEAD','OPTIONS'}:
                from .action_deadline import mark_write_dispatch
                mark_write_dispatch()
            self.response=await self.session.request(self.method,self.url,**self.kwargs)
            if self.response.status in {412,429}:
                risk_pause(self.directory,urlsplit(str(self.url)).path,self.response.headers.get('Retry-After'))
                self.state='failed'
                raise BilibiliPaused('B站返回 HTTP 412/429，请求已递增暂停，不立即重试')
            return _GuardedResponse(self.response,self)
        except BaseException:
            error = sys.exc_info()
            if self.response is not None:
                self.response.release()
            release(self.directory,self.token,self.state,self.rpid)
            if hasattr(self,'deadline'):
                await self.deadline.__aexit__(*error)
            raise

    async def __aexit__(self,kind,value,tb):
        from .traffic_guard import release
        try:
            if self.response is not None:
                self.response.release()
            release(self.directory,self.token,self.state,self.rpid)
        finally:
            await self.deadline.__aexit__(kind,value,tb)
        return False


class _GuardedSession:
    def __init__(self,session,config,proxy=''):
        self.session,self.config,self.proxy=session,config,proxy

    def _request(self,method,url,kwargs):
        from .request_guard import is_bilibili_url
        if self.proxy:
            kwargs={**kwargs,'proxy':self.proxy}
        if is_bilibili_url(url):
            return _GuardedRequest(self.session,self.config,method,url,kwargs)
        return self.session.request(method,url,**kwargs)

    def get(self,url,**kwargs):
        return self._request('GET',url,kwargs)

    def post(self,url,**kwargs):
        return self._request('POST',url,kwargs)


def normalize_proxy_url(value):
    """Validate administrator configuration without echoing credentials."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("插件代理地址必须是字符串")
    value = value.strip()
    if not value:
        return ""
    try:
        if len(value) > 2048 or any(ch.isspace() or ord(ch) < 32 for ch in value):
            raise ValueError
        url = urlsplit(value)
        if url.scheme.lower() not in {"http", "https", "socks5", "socks5h"}:
            raise ValueError
        if not url.hostname or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError
        # Both SOCKS clients below use proxy-side DNS; accept the familiar h alias.
        scheme = "socks5" if url.scheme.lower() == "socks5h" else url.scheme.lower()
        netloc = url.netloc
        if scheme == "socks5" and url.port is None:
            netloc += ":1080"
        return urlunsplit((scheme, netloc, "", "", ""))
    except (TypeError, ValueError):
        raise ValueError("插件代理地址无效：请填写 http://、https:// 或 socks5://主机:端口，不要填写路径或订阅链接") from None


def configured_proxy(config):
    return normalize_proxy_url(config.get("PROXY_URL", ""))


def redact_proxy_error(text, config):
    """Remove proxy URLs and decoded passwords before errors enter logs."""
    text = str(text or "")
    raw = str(config.get("PROXY_URL", "") or "").strip()
    if raw:
        text = text.replace(raw, "[插件代理]")
        try:
            text = text.replace(normalize_proxy_url(raw), "[插件代理]")
            password = urlsplit(raw).password
            if password:
                for secret in {password, unquote(password)}:
                    text = text.replace(secret, "[代理密码已隐藏]")
        except ValueError:
            pass
    return re.sub(r"(?i)((?:https?|socks5h?)://)[^/\s@]+@", r"\1[凭据已隐藏]@", text)


class _ProxySession:
    """Facade for aiohttp 3.9+, which lacks a session-level proxy option."""

    def __init__(self, session, proxy):
        self._session = session
        self._proxy = proxy

    def get(self, *args, **kwargs):
        return self._session.get(*args, **{**kwargs, "proxy": self._proxy})

    def post(self, *args, **kwargs):
        return self._session.post(*args, **{**kwargs, "proxy": self._proxy})


@asynccontextmanager
async def plugin_http_session(config):
    proxy = configured_proxy(config)
    options = {}
    if proxy.startswith("socks5://"):
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError:
            raise ValueError("SOCKS5 代理缺少依赖，请重装插件依赖 aiohttp-socks") from None
        options["connector"] = ProxyConnector.from_url(proxy, rdns=True)
    try:
        async with aiohttp.ClientSession(**options) as session:
            yield _GuardedSession(session,config,proxy if proxy.startswith(("http://", "https://")) else '')
    except (aiohttp.ClientError, OSError) as exc:
        if not proxy:
            raise
        # Never silently fall back to direct access when a configured proxy fails.
        raise aiohttp.ClientError(
            f"插件代理请求失败（{type(exc).__name__}）: {redact_proxy_error(exc, config)}"
        ) from None


def openai_proxy_kwargs(config):
    proxy = configured_proxy(config)
    if not proxy:
        return {}  # Preserve the SDK's original environment/default behavior.
    from openai import DefaultAsyncHttpxClient
    return {"http_client": DefaultAsyncHttpxClient(proxy=proxy, trust_env=False)}


def download_proxy_args(config):
    proxy = configured_proxy(config)
    return ["--proxy", proxy] if proxy else []


async def close_plugin_clients(plugin):
    """Close only SDK clients owned by this plugin, never AstrBot providers."""
    for name in ("_embed_client", "_video_vision_client", "_image_vision_client", "_web_search_client"):
        client = getattr(plugin, name, None)
        setattr(plugin, name, None)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
