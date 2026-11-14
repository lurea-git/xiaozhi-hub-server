"""[thinksmart] 视觉解释接口不再有 web_test_client 免认证后门。"""
from types import SimpleNamespace

from core.api.vision_handler import VisionHandler


def handler():
    return VisionHandler({"server": {"auth_key": "k" * 32}})


def req(**headers):
    return SimpleNamespace(headers=headers)


def test_test_client_header_no_longer_skips_auth():
    ok, device = handler()._verify_auth_token(req(**{"Client-Id": "web_test_client", "Device-Id": "x"}))
    assert ok is False and device is None


def test_missing_or_bad_token_is_refused():
    h = handler()
    assert h._verify_auth_token(req())[0] is False
    assert h._verify_auth_token(req(Authorization="Bearer not-a-token"))[0] is False


def test_a_real_token_still_works():
    h = handler()
    token = h.auth.generate_token("dev-1")
    assert h._verify_auth_token(req(Authorization=f"Bearer {token}")) == (True, "dev-1")
