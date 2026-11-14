"""[xiaozhi-hub] 回声识别过滤：回复播放期间和播完后几秒内，识别结果大部分是回复自己的字就丢掉。"""

import struct
from types import SimpleNamespace

from core.utils import reply_echo


def make_conn():
    return SimpleNamespace(sentence_id="s1", client_is_speaking=True)


def test_echo_of_reply_is_dropped_while_speaking():
    conn = make_conn()
    assert not reply_echo.during_reply(conn) and not reply_echo.is_echo(conn, "也是。")  # 还没有回复
    reply_echo.note_reply(conn, "我觉得也是，")
    reply_echo.note_reply(conn, "明天会下雨。")
    assert reply_echo.during_reply(conn)
    assert reply_echo.is_echo(conn, "也是。")
    assert reply_echo.is_echo(conn, "明天会下鱼")  # 认错一个字也算
    assert not reply_echo.is_echo(conn, "停一下，帮我查查股票")  # 用户自己的话
    assert not reply_echo.is_echo(conn, "。")


def test_window_after_reply_and_new_reply_resets(monkeypatch):
    conn = make_conn()
    now = [100.0]
    monkeypatch.setattr(reply_echo.time, "monotonic", lambda: now[0])
    reply_echo.note_reply(conn, "今天天气不错。")
    reply_echo.note_reply(conn, None)  # 这轮的音频发完了
    conn.client_is_speaking = False
    now[0] += reply_echo.WINDOW_AFTER - 0.5
    assert reply_echo.is_echo(conn, "天气不错")  # 设备还在播缓冲，回声还会来
    now[0] += 1
    assert not reply_echo.during_reply(conn) and not reply_echo.is_echo(conn, "天气不错")  # 过了窗口算用户说的
    # 新的一轮回复：上一轮的字不再算
    conn.sentence_id, conn.client_is_speaking = "s2", True
    reply_echo.note_reply(conn, "好的。")
    assert not reply_echo.is_echo(conn, "天气不错") and reply_echo.is_echo(conn, "好的")


def test_level_db():
    loud = struct.pack("<960h", *([16384] * 960))  # 半幅方波 ≈ -6 dBFS
    quiet = struct.pack("<960h", *([0] * 960))
    assert round(reply_echo.level_db([quiet, loud])) == -6
    assert reply_echo.level_db([]) == -120.0


# ---- [thinksmart] 回复播完以后才说完的话不当回声 ----

def finished_reply(monkeypatch, text, wall=1000.0):
    """一轮回复发完、设备（按估算）播完，墙上时间停在 wall。"""
    conn = make_conn()
    clock = {"wall": wall, "mono": 50.0}
    monkeypatch.setattr(reply_echo.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(reply_echo.time, "monotonic", lambda: clock["mono"])
    reply_echo.note_reply(conn, text)
    reply_echo.note_reply(conn, None)
    conn.client_is_speaking = False
    return conn, clock


def test_question_asked_after_the_reply_ended_is_kept(monkeypatch):
    # 2026-11-12 08:52 实际发生过：回复播完 3 秒后问"你是什么模型"，你/是/什/么都在回复里，被当回声丢掉
    conn, clock = finished_reply(monkeypatch, "你好呀！我是小麦，有什么可以帮你的吗？")
    clock["mono"] += 3.0
    conn.vad_last_voice_time = (clock["wall"] + 2.6) * 1000
    assert reply_echo.during_reply(conn)
    assert abs(reply_echo.seconds_after_reply(conn) - 2.6) < 1e-6
    assert not reply_echo.is_echo(conn, "你是什么模型")


def test_echo_tail_right_after_playback_is_still_dropped(monkeypatch):
    conn, clock = finished_reply(monkeypatch, "你好呀！我是小麦，有什么可以帮你的吗？")
    clock["mono"] += 1.5
    conn.vad_last_voice_time = (clock["wall"] + 0.5) * 1000    # 最后几个字的回声，播完后 0.5 秒到
    assert reply_echo.is_echo(conn, "可以帮你的吗")


def test_while_the_reply_plays_text_decides_as_before(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(reply_echo.time, "time", lambda: 1000.0)
    reply_echo.note_reply(conn, "我觉得也是，")
    conn.vad_last_voice_time = 1005.0 * 1000                  # 时间再晚，回复还没播完就不算"播完后"
    assert reply_echo.seconds_after_reply(conn) is None
    assert reply_echo.is_echo(conn, "也是。")


def test_new_sentence_in_the_same_turn_clears_done(monkeypatch):
    conn, clock = finished_reply(monkeypatch, "第一句。")
    reply_echo.note_reply(conn, "又来一句。")                   # 同一轮又发了一句（还在播）
    conn.vad_last_voice_time = (clock["wall"] + 5) * 1000
    assert reply_echo.seconds_after_reply(conn) is None


def test_unknown_voice_time_falls_back_to_text(monkeypatch):
    conn, clock = finished_reply(monkeypatch, "今天天气不错。")
    conn.vad_last_voice_time = 0.0
    assert reply_echo.is_echo(conn, "天气不错")
