"""拍照识图：照片暂存、过期、一次只回答一次；/kiosk/photo 接口的校验。"""

import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler as ah
from core.utils import photo_box

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


@pytest.fixture
async def client(tmp_path):
    cfg_path = tmp_path / ".config.yaml"
    cfg_path.write_text("selected_module:\n  VLLM: ChatGLMVLLM\n", encoding="utf-8")
    app = web.Application()
    handler = ah.AdminHandler({"selected_module": {"VLLM": "ChatGLMVLLM"}, "VLLM": {}}, ws_server=None,
                              config_path=cfg_path)
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.fixture(autouse=True)
def empty_box():
    photo_box._box.clear()
    yield
    photo_box._box.clear()


def test_take_returns_photo_once():
    photo_box.put("dev", JPEG, object(), "智谱")
    assert photo_box.peek("dev") is not None
    assert photo_box.take("dev").jpeg == JPEG
    assert photo_box.take("dev") is None  # 每张照片只回答一次


def test_photo_expires():
    photo_box.put("dev", JPEG, object(), "智谱")
    photo_box._box["dev"].at = time.monotonic() - photo_box.TTL_SECONDS - 1
    assert photo_box.peek("dev") is None
    photo_box.put("dev", JPEG, object(), "智谱")
    photo_box._box["dev"].at = time.monotonic() - photo_box.TTL_SECONDS - 1
    assert photo_box.take("dev") is None


def test_photos_are_per_device_and_clearable():
    photo_box.put("a", JPEG, object())
    photo_box.put("b", JPEG, object())
    assert photo_box.clear("a") is True and photo_box.clear("a") is False
    assert photo_box.peek("b") is not None


def make_conn(vllm, turn_gen=1):
    from types import SimpleNamespace
    asked = []

    conn = SimpleNamespace(turn_gen=turn_gen, pending_photo=None, asked=asked, photo_at_chat=None)

    def chat(query, turn=None):
        asked.append(query)
        conn.photo_at_chat = conn.pending_photo   # 写进对话的那一刻照片还在

    conn.chat = chat
    return conn


def test_ask_has_the_vision_model_describe_then_the_chat_model_answers(monkeypatch):
    """视觉模型只当眼睛：它收到的是描述提示词，问题交给对话模型。"""
    seen = {}

    class Vllm:
        def response(self, question, b64):
            seen['prompt'] = question
            return '桌上有一张纸，上面写着 3+4=8。'

    conn = make_conn(Vllm())
    photo_box.ask(conn, '这道题对不对', photo_box.Photo(JPEG, Vllm(), '智谱', time.monotonic()), 1)
    assert seen['prompt'] == photo_box.DESCRIBE_PROMPT
    assert conn.asked == ['📷 这道题对不对\n\n（照片内容：桌上有一张纸，上面写着 3+4=8。）']
    assert conn.photo_at_chat and conn.pending_photo is None   # 照片随这条提问存进记录，之后清掉


def test_ask_still_asks_the_question_when_the_vision_model_fails():
    class Broken:
        def response(self, question, b64):
            raise RuntimeError('429 余额不足')

    conn = make_conn(Broken())
    photo_box.ask(conn, '这是什么', photo_box.Photo(JPEG, Broken(), '智谱', time.monotonic()), 1)
    assert conn.asked == ['📷 这是什么\n\n（用户刚拍了一张照片，但没能识别出内容）']


def test_ask_drops_the_question_if_interrupted_while_describing():
    class Slow:
        def response(self, question, b64):
            conn.turn_gen += 1     # 用户打断，开了一轮新的
            return '描述'

    conn = make_conn(None)
    photo_box.ask(conn, '这是什么', photo_box.Photo(JPEG, Slow(), '智谱', time.monotonic()), 1)
    assert conn.asked == []


async def test_kiosk_photo_endpoint(client, monkeypatch):
    await client.post("/admin/api/setup", json={"password": "pass-1234"})
    r = await client.post("/kiosk/photo?device=dev", data=JPEG)
    body = await r.json()
    assert r.status == 409 and "视觉模型" in body["error"]  # 没配 Key
    r = await client.post("/kiosk/photo", data=JPEG)
    assert r.status == 400
    r = await client.post("/kiosk/photo?device=dev", data=b"not a jpeg")
    assert r.status == 400
    r = await client.post("/kiosk/photo?device=dev", data=b"\xff\xd8\xff" + b"0" * photo_box.MAX_BYTES)
    assert r.status == 400
    assert photo_box.peek("dev") is None
    r = await client.delete("/kiosk/photo?device=dev")
    assert (await r.json())["discarded"] is False
