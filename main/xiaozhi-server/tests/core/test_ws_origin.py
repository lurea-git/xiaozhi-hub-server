"""[thinksmart] WebSocket 握手：别的网站发起的浏览器连接拒绝，设备和同主机页面放行。"""
import asyncio
from types import SimpleNamespace

import pytest
import websockets
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from core import websocket_server
from core.websocket_server import WebSocketServer, cross_site_origin


@pytest.mark.parametrize("headers, refused", [
    ({"Host": "192.168.9.157:16689"}, False),                                       # 设备、本机客户端：没有 Origin
    ({"Host": "192.168.9.157:16689", "Origin": "http://192.168.9.157:16688"}, False),  # 网关自己的页面
    ({"Host": "127.0.0.1:16689", "Origin": "http://127.0.0.1:8090"}, False),
    ({"Host": "localhost:16689", "Origin": "http://localhost:8080"}, False),
    ({"Host": "thinksmarter.local:16689", "Origin": "http://thinksmarter.local:16688"}, False),
    ({"Host": "192.168.9.157:16689", "Origin": "https://evil.example"}, True),      # 别的网站
    ({"Host": "192.168.9.157:16689", "Origin": "null"}, True),                      # file:// 页面
    ({"Host": "evil.example:16689", "Origin": "http://evil.example"}, True),        # DNS 重绑定
])
def test_cross_site_origin(headers, refused):
    assert (cross_site_origin(headers) is not None) is refused


def test_handshake_end_to_end(monkeypatch):
    log = SimpleNamespace(bind=lambda **k: SimpleNamespace(warning=lambda m: None))
    fake = SimpleNamespace(logger=log)

    async def echo(ws):
        await ws.send("hi")

    async def main():
        async with serve(echo, "127.0.0.1", 0,
                         process_request=lambda c, r: WebSocketServer._http_response(fake, c, r)) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}/"
            async with connect(url) as ws:                         # 不带 Origin：放行
                assert await ws.recv() == "hi"
            async with connect(url, origin=f"http://127.0.0.1:{port}") as ws:   # 同主机页面：放行
                assert await ws.recv() == "hi"
            with pytest.raises(websockets.exceptions.InvalidStatus) as e:
                async with connect(url, origin="https://evil.example"):
                    pass
            assert e.value.response.status_code == 403

    asyncio.run(main())
