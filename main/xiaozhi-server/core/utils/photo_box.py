"""设备拍的照片：暂存起来，等用户说出（或输入）问题，再连同问题一起交给视觉模型。

流程：
1. 设备客户端把照片 POST 到 /kiosk/photo，这里按设备暂存（put），同时建好视觉模型实例，
   没配好就当场报错，不用等用户问完才发现；
2. 用户的下一句话进入 startToChat，发现这台设备有暂存照片（take），就不问对话模型，
   改由 ask() 把"问题 + 照片"发给视觉模型，回答用语音念出来，并写进对话记录；
3. 超过 TTL 没人问，照片作废。
"""

import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from config.logger import setup_logging
from core.utils import reply_progress, turn_gen
from core.utils.dialogue import Message

TAG = __name__
logger = setup_logging()

TTL_SECONDS = 120
MAX_BYTES = 900 * 1024  # aiohttp 默认请求体上限 1 MB
PHOTO_MARK = "📷 "
VOICE_HINT = "（回答会用语音播报：口语化、简洁，不要用列表、星号、井号和表格符号）"
FAILED_REPLY = "识图没成功，请稍后再试"
EXPIRED_REPLY = "这张照片已经过期了，请重新拍一张"


@dataclass
class Photo:
    jpeg: bytes
    vllm: object  # 已建好的视觉模型实例
    label: str
    at: float


_lock = threading.Lock()
_box: dict = {}


def put(key: str, jpeg: bytes, vllm, label: str = "") -> None:
    with _lock:
        _box[key] = Photo(jpeg, vllm, label, time.monotonic())


def peek(key: str) -> Optional[Photo]:
    """看一眼有没有可用的照片（不取走）。"""
    with _lock:
        photo = _box.get(key)
        if photo and time.monotonic() - photo.at > TTL_SECONDS:
            del _box[key]
            return None
        return photo


def take(key: str) -> Optional[Photo]:
    """取走这台设备的照片，每张照片只回答一次。"""
    with _lock:
        photo = _box.pop(key, None)
    if photo and time.monotonic() - photo.at > TTL_SECONDS:
        return None
    return photo


def clear(key: str) -> bool:
    with _lock:
        return _box.pop(key, None) is not None


def split_sentences(text: str) -> list:
    """按句拆开，合成可以边出边念。"""
    parts = re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", text or "")
    return [p.strip() for p in parts if p.strip()]


def ask(conn, text: str, photo: Photo, gen: int) -> None:
    """在线程里执行：把问题和照片发给视觉模型，回答念出来并写进对话。"""
    import base64

    from core.agent import chat_history
    from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO

    sentence_id = uuid.uuid4().hex
    turn_gen.bind(conn, sentence_id, gen)
    conn.sentence_id = sentence_id
    put_tts = conn.tts.tts_text_queue.put

    def say(sentence_type, content_type, detail=None):
        put_tts(TTSMessageDTO(sentence_id=sentence_id, sentence_type=sentence_type,
                              content_type=content_type, content_detail=detail))

    try:
        user_msg = Message(role="user", content=PHOTO_MARK + text)
        conn.dialogue.put(user_msg)
        reply_progress.note_turn(conn, sentence_id, user_msg)
        say(SentenceType.FIRST, ContentType.ACTION)
        try:
            answer = photo.vllm.response(text + VOICE_HINT, base64.b64encode(photo.jpeg).decode("ascii"))
            answer = (answer or "").strip() or FAILED_REPLY
        except Exception as e:  # noqa: BLE001 —— 网络、Key、模型不支持图片等，一律给用户一句话
            logger.bind(tag=TAG).error(f"视觉模型回答失败: {e!r}")
            answer = FAILED_REPLY
        if turn_gen.stale(conn, sentence_id=sentence_id):
            logger.bind(tag=TAG).info("识图期间被打断，不再念")
            return
        logger.bind(tag=TAG).info(f"视觉模型回答: {answer[:80]}")
        for sentence in split_sentences(answer) or [answer]:
            say(SentenceType.MIDDLE, ContentType.TEXT, sentence)
        say(SentenceType.LAST, ContentType.ACTION)
        conn._put_reply(sentence_id, answer)
    finally:
        reply_progress.finish_turn(conn, sentence_id)
        chat_history.sync(conn)
        chat_history.maybe_summarize(conn)
