"""设备拍的照片：暂存起来，等用户说出（或输入）问题，再连同问题一起交给视觉模型。

流程：
1. 设备客户端把照片 POST 到 /kiosk/photo，这里按设备暂存（put），同时建好视觉模型实例，
   没配好就当场报错，不用等用户问完才发现；
2. 用户的下一句话进入 startToChat，发现这台设备有暂存照片（take），就由 ask() 先让视觉模型
   只看图写一段客观描述，再把"问题 + 描述"交给对话模型（DeepSeek 等）回答：人设、上下文、
   聊天记录都和普通提问一样；照片本身随这条提问存进聊天库；
3. 超过 TTL 没人问，照片作废。
"""

import threading
import time
from dataclasses import dataclass
from typing import Optional

from config.logger import setup_logging
from core.utils import turn_gen
from core.utils.dialogue import PHOTO_MARK

TAG = __name__
logger = setup_logging()

TTL_SECONDS = 120
DESCRIBE_PROMPT = (
    "只客观描述这张图片里有什么：场景、物品、文字（原样抄写）、人物动作、数字和题目内容。"
    "不要回答问题，不要评价，不要省略细节。"
)
MAX_BYTES = 900 * 1024  # aiohttp 默认请求体上限 1 MB


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


def describe(vllm, jpeg: bytes) -> str:
    """视觉模型只做"眼睛"：给出图片描述，不回答问题（问题交给对话模型）。"""
    import base64

    return (vllm.response(DESCRIBE_PROMPT, base64.b64encode(jpeg).decode("ascii")) or "").strip()


def ask(conn, text: str, photo: "Photo", gen: int) -> None:
    """在线程里执行：视觉模型看图写出描述，再把"问题 + 描述"交给这台设备的对话模型回答。

    回答照常走对话模型（DeepSeek 等），所以人设、上下文、聊天记录都和普通提问一样。
    """
    import base64

    conn.pending_photo = base64.b64encode(photo.jpeg).decode("ascii")
    try:
        described = ""
        try:
            described = describe(photo.vllm, photo.jpeg)
        except Exception as e:  # noqa: BLE001 —— 视觉模型挂了就退化成只有文字问题
            logger.bind(tag=TAG).error(f"视觉模型看图失败: {e!r}")
        if turn_gen.stale(conn, gen):
            logger.bind(tag=TAG).info("看图期间被打断，不再回答")
            return
        if described:
            logger.bind(tag=TAG).info(f"图片描述: {described[:100]}")
            query = f"{PHOTO_MARK}{text}\n\n（照片内容：{described}）"
        else:
            query = f"{PHOTO_MARK}{text}\n\n（用户刚拍了一张照片，但没能识别出内容）"
        # 照片在 conn.pending_photo 里，chat() 把这条提问写进对话时由 chat_history.sync 一起存进库
        conn.chat(query, turn=gen)
    except Exception as e:  # noqa: BLE001
        logger.bind(tag=TAG).error(f"看图回答失败: {e!r}")
    finally:
        conn.pending_photo = None
