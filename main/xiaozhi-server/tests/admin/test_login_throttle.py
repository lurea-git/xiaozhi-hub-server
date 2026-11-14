"""[thinksmart] 后台登录限流：同一来源错 5 次暂停、同一时间只验证一个、密码长度上限。"""
import asyncio
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler, config_store
from core.admin.admin_handler import AdminHandler


@pytest.fixture
def cfg_path(tmp_path) -> Path:
    p = tmp_path / ".config.yaml"
    p.write_text("selected_module: {}\n", encoding="utf-8")
    return p


@pytest.fixture
async def client(cfg_path, monkeypatch):
    monkeypatch.setattr(admin_handler.asyncio, "sleep", _no_sleep)   # 失败后的 1 秒等待在测试里不等
    app = web.Application()
    handler = AdminHandler({"selected_module": {}}, config_path=cfg_path)
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        c.handler = handler
        r = await c.post("/admin/api/setup", json={"password": "pass-1234"})
        assert r.status == 200
        await c.post("/admin/api/logout", json={})
        yield c


_real_sleep = asyncio.sleep


async def _no_sleep(seconds, *a, **k):
    await _real_sleep(0)


async def login(c, password):
    return (await c.post("/admin/api/login", json={"password": password})).status


async def test_five_wrong_passwords_pause_that_source(client):
    for _ in range(admin_handler.LOGIN_MAX_FAILS):
        assert await login(client, "nope-nope") == 401
    r = await client.post("/admin/api/login", json={"password": "pass-1234"})
    assert r.status == 429 and "分钟后再试" in (await r.json())["error"]   # 对的密码也要等


async def test_pause_ends_after_the_window(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(admin_handler.time, "monotonic", lambda: now[0])
    for _ in range(admin_handler.LOGIN_MAX_FAILS):
        await login(client, "nope-nope")
    assert await login(client, "pass-1234") == 429
    now[0] += admin_handler.LOGIN_FAIL_WINDOW + 1
    assert await login(client, "pass-1234") == 200


async def test_success_clears_the_count(client):
    for _ in range(admin_handler.LOGIN_MAX_FAILS - 1):
        await login(client, "nope-nope")
    assert await login(client, "pass-1234") == 200
    await client.post("/admin/api/logout", json={})
    assert await login(client, "nope-nope") == 401          # 重新从 0 数
    assert await login(client, "pass-1234") == 200


async def test_one_verification_at_a_time(client):
    async with client.handler._login_lock:                  # 另一次验证正在进行
        r = await client.post("/admin/api/login", json={"password": "pass-1234"})
        assert r.status == 429
    assert await login(client, "pass-1234") == 200


async def test_overlong_password_is_refused_without_hashing(client, monkeypatch):
    monkeypatch.setattr(admin_handler.auth, "verify_password", lambda *a: pytest.fail("不该去算 scrypt"))
    assert await login(client, "x" * (admin_handler.MAX_PASSWORD_CHARS + 1)) == 401


async def test_setup_rejects_overlong_password(tmp_path):
    cfg = tmp_path / ".config.yaml"
    cfg.write_text("selected_module: {}\n", encoding="utf-8")
    app = web.Application()
    AdminHandler({"selected_module": {}}, config_path=cfg).register(app)
    async with TestClient(TestServer(app)) as c:
        r = await c.post("/admin/api/setup", json={"password": "x" * (admin_handler.MAX_PASSWORD_CHARS + 1)})
        assert r.status == 400
    assert not config_store.get_password_hash(cfg)
