"""[thinksmart] OTA / 视觉接口不再允许任意网站跨域读取。"""
import asyncio

from aiohttp import web

from core.api.base_handler import BaseHandler


def test_no_wildcard_cors_and_nosniff():
    resp = web.Response()
    BaseHandler({})._add_cors_headers(resp)
    assert "Access-Control-Allow-Origin" not in resp.headers
    assert "Access-Control-Allow-Credentials" not in resp.headers
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_preflight_does_not_grant_other_origins():
    resp = asyncio.run(BaseHandler({}).handle_options(None))
    assert "Access-Control-Allow-Origin" not in resp.headers
