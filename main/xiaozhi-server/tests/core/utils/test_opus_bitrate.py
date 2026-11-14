"""[thinksmart] 下发给设备的语音 Opus 码率 64 kbps。"""
import struct

import numpy as np

from core.utils import util
from core.utils.opus_encoder_utils import OpusEncoderUtils


def test_tts_encoder_uses_64k():
    enc = OpusEncoderUtils(sample_rate=16000, channels=1, frame_size_ms=60)
    assert enc.bitrate == 64000 and enc.encoder.bitrate == 64000


def test_pcm_stream_encoder_uses_64k_and_packets_get_bigger():
    t = np.arange(16000) / 16000
    pcm = (np.sin(2 * np.pi * 440 * t) * 8000 + np.random.default_rng(0).normal(0, 2000, 16000)).astype("<i2").tobytes()
    packets = []
    util.pcm_to_data_stream(pcm, is_opus=True, callback=packets.append, sample_rate=16000)
    assert packets
    avg = sum(len(p) for p in packets) / len(packets)
    assert avg > 300     # 24 kbps 时每 60 ms 约 180 字节；64 kbps 约 480 字节
