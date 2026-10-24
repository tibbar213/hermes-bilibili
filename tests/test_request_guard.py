"""Deterministic dispatch limits and local-only HTTP throttling fixtures."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from concurrent.futures import ThreadPoolExecutor
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('guard_test_plugin',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from guard_test_plugin.core.request_guard import reserve_reply,guard_status,pause_requests,check_pause,BilibiliPaused,is_bilibili_url

CFG={'COMMENT_REPLY_MIN_INTERVAL':120,'COMMENT_REPLY_HOURLY_LIMIT':12,'AUTONOMOUS_REPLY_DAILY_LIMIT':30}


def test_exact_interval_survives_new_connections(tmp_path):
    assert reserve_reply(tmp_path,CFG,at=10000)[0]
    assert not reserve_reply(tmp_path,CFG,at=10119.9)[0]
    assert reserve_reply(tmp_path,CFG,at=10120)[0]


def test_hour_limit_is_rolling_across_clock_boundary(tmp_path):
    start=36000+30*60
    for i in range(12):assert reserve_reply(tmp_path,CFG,at=start+i*120)[0]
    assert not reserve_reply(tmp_path,CFG,at=start+12*120)[0]
    assert reserve_reply(tmp_path,CFG,at=start+3600)[0]


def test_daily_thirtieth_allowed_thirty_first_denied(tmp_path):
    for i in range(30):assert reserve_reply(tmp_path,CFG,at=36000+i*300)[0]
    assert not reserve_reply(tmp_path,CFG,at=36000+30*300)[0]


def test_concurrent_dispatchers_cannot_claim_same_slot(tmp_path):
    guard_status(tmp_path,CFG,at=10000)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results=list(workers.map(lambda _:reserve_reply(tmp_path,CFG,at=10000)[0],range(8)))
    assert sum(bool(x) for x in results)==1


def test_pause_persists_and_expires_at_five_minutes(tmp_path):
    pause_requests(tmp_path,at=10000)
    with pytest.raises(BilibiliPaused):check_pause(tmp_path,at=10299.9)
    assert not reserve_reply(tmp_path,CFG,at=10100)[0]
    check_pause(tmp_path,at=10300)
    assert reserve_reply(tmp_path,CFG,at=10300)[0]


def test_domain_boundary_does_not_pause_model_provider():
    assert is_bilibili_url('https://api.bilibili.com/x/test')
    assert is_bilibili_url('https://aisubtitle.hdslb.com/subtitle.json')
    assert not is_bilibili_url('https://api.bilibili.com.evil.invalid/test')
    assert not is_bilibili_url('https://model-provider.invalid/v1')


def test_real_reply_sender_only_dispatches_once_and_disables_http_retries(tmp_path):
    from guard_test_plugin.core.bilibili import BilibiliAPIMixin
    class Config(dict):data_dir=tmp_path
    post=AsyncMock(return_value=({'code':0},None))
    native=SimpleNamespace(config=Config(CFG),_http_post=post)
    async def run():
        assert await BilibiliAPIMixin._send_reply(native,1,2,1,'正常回复')
        assert not await BilibiliAPIMixin._send_reply(native,1,3,1,'不能紧接发送')
    asyncio.run(run())
    assert post.await_count==1 and post.call_args.kwargs['retries']==0


def test_reply_timeout_is_unknown_not_automatically_retryable(tmp_path):
    from guard_test_plugin.core.bilibili import BilibiliAPIMixin
    class Config(dict):data_dir=tmp_path
    native=SimpleNamespace(config=Config(CFG),_http_post=AsyncMock(side_effect=asyncio.TimeoutError))
    with pytest.raises(TimeoutError):asyncio.run(BilibiliAPIMixin._send_reply(native,1,2,1,'正常回复'))
    import sqlite3
    with sqlite3.connect(tmp_path/'bilibot.sqlite3') as db:
        assert db.execute('SELECT state FROM reply_dispatch').fetchone()[0]=='unknown'
    assert native._http_post.await_count==1


@pytest.mark.parametrize('code',[412,429])
def test_real_http_response_pauses_other_bilibili_requests_without_retry(tmp_path,code):
    from guard_test_plugin.core.network import plugin_http_session
    from guard_test_plugin.core.utils import UtilsMixin
    class Config(dict):data_dir=tmp_path
    requests=[]
    async def serve(reader,writer):
        try:
            header=await reader.readuntil(b'\r\n\r\n');requests.append(header)
            writer.write(f'HTTP/1.1 {code} Throttled\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}'.encode())
            await writer.drain()
        finally:
            writer.close();await writer.wait_closed()
    async def run():
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        cfg=Config(PROXY_URL=f'http://127.0.0.1:{port}')
        native=SimpleNamespace(config=cfg,_headers=lambda:{})
        try:
            with pytest.raises(BilibiliPaused):await UtilsMixin._http_get(native,'http://api.bilibili.com/x/test')
            with pytest.raises(BilibiliPaused):
                async with plugin_http_session(cfg) as session:
                    async with session.post('http://api.bilibili.com/x/write'):pass
            assert len(requests)==1
            # Non-Bilibili traffic is independent of this account's cooldown.
            async with plugin_http_session(cfg) as session:
                async with session.get(f'http://127.0.0.1:{port}/provider') as response:assert response.status==code
            assert len(requests)==2
            snapshot=guard_status(tmp_path,cfg)
            assert 295 < snapshot['pause_remaining_seconds'] <= 300
        finally:
            server.close();await server.wait_closed()
    asyncio.run(run())
