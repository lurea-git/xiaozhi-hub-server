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


def test_split_sentences():
    assert photo_box.split_sentences("这是一杯咖啡。杯子是白色的！还有勺子") == ["这是一杯咖啡。", "杯子是白色的！", "还有勺子"]
    assert photo_box.split_sentences("") == []


class FakeTTSQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def make_conn():
    queue = FakeTTSQueue()
    log = SimpleNamespace(bind=lambda **kw: SimpleNamespace(info=lambda *a: None, error=lambda *a: None,
                                                            warning=lambda *a: None))
    conn = SimpleNamespace(
        tts=SimpleNamespace(tts_text_queue=queue), dialogue=SimpleNamespace(put=lambda m: conn.said.append(m)),
        said=[], replies=[], sentence_id=None, logger=log, turn_gen=1)
    conn._put_reply = lambda sid, text: conn.replies.append(text)
    return conn, queue


def run_ask(monkeypatch, vllm):
    from core.agent import chat_history
    from core.utils import reply_progress

    monkeypatch.setattr(chat_history, "sync", lambda conn: None)
    monkeypatch.setattr(chat_history, "maybe_summarize", lambda conn: None)
    monkeypatch.setattr(reply_progress, "note_turn", lambda *a: None)
    monkeypatch.setattr(reply_progress, "finish_turn", lambda *a: None)
    conn, queue = make_conn()
    photo = photo_box.Photo(JPEG, vllm, "智谱", time.monotonic())
    photo_box.ask(conn, "帮我解这道题", photo, 1)
    return conn, queue


def test_ask_speaks_answer_and_records_dialogue(monkeypatch):
    seen = {}

    class Vllm:
        def response(self, question, b64):
            seen["question"], seen["b64"] = question, b64
            return "答案是四。因为二加二等于四。"

    conn, queue = run_ask(monkeypatch, Vllm())
    assert seen["question"].startswith("帮我解这道题") and seen["b64"]
    spoken = [i.content_detail for i in queue.items if i.content_detail]
    assert spoken == ["答案是四。", "因为二加二等于四。"]
    assert conn.said[0].content == photo_box.PHOTO_MARK + "帮我解这道题"
    assert conn.replies == ["答案是四。因为二加二等于四。"]


def test_ask_failure_gives_short_reply(monkeypatch):
    class Broken:
        def response(self, question, b64):
            raise RuntimeError("401")

    conn, queue = run_ask(monkeypatch, Broken())
    assert [i.content_detail for i in queue.items if i.content_detail] == [photo_box.FAILED_REPLY]
    assert conn.replies == [photo_box.FAILED_REPLY]


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
