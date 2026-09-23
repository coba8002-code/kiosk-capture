"""S5 음성 파일의 수집 품질 검사.

두 종류의 입력을 받는다.

* 촬영 앱이 Web Audio API로 계산해 manifest에 넣은 지표
* PC에서 별도 투입된 PCM WAV 파일

결과는 무음·클리핑·지나치게 짧은 녹음을 찾아 재수집을 요구하기 위한 것이다.
dBFS는 디지털 최대값에 대한 상대 레벨이며 dBA가 아니다. 이 값을 별표5 5.c의
65 dBA 판정에 쓰면 안 된다.
"""

from __future__ import annotations

import array
import math
import sys
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

MIN_DURATION_S = 2.0
QUIET_RMS_DBFS = -45.0
MAX_CLIPPING_RATIO = 0.005
MAX_SILENCE_RATIO = 0.95


@dataclass
class AudioQuality:
    readable: bool
    source: str
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    silence_ratio: float | None = None
    clipping_ratio: float | None = None
    issues: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def status(self) -> str:
        if not self.readable:
            return "확인 불가"
        return "재수집 권장" if self.issues else "분석 가능"

    def to_dict(self) -> dict:
        return {**asdict(self), "status": self.status,
                "legal_measurement": False,
                "unit_note": "dBFS 품질 지표이며 별표5 5.c의 dBA 판정값이 아님"}


def _dbfs(value: float) -> float:
    return round(20.0 * math.log10(max(value, 1e-12)), 1)


def _issues(duration: float, rms: float, silence: float, clipping: float) -> list[str]:
    out: list[str] = []
    if duration < MIN_DURATION_S:
        out.append(f"녹음이 너무 짧음 ({duration:.1f}초)")
    if rms < QUIET_RMS_DBFS:
        out.append(f"신호가 너무 작음 ({rms:.1f} dBFS)")
    if silence > MAX_SILENCE_RATIO:
        out.append(f"대부분 무음 ({silence * 100:.0f}%)")
    if clipping > MAX_CLIPPING_RATIO:
        out.append(f"클리핑이 많음 ({clipping * 100:.2f}%)")
    return out


def from_manifest(data: object) -> AudioQuality:
    """촬영 앱이 저장한 audio_metrics를 검증해 품질 결과로 바꾼다."""
    if not isinstance(data, dict):
        return AudioQuality(False, "manifest", note="음질 지표가 없습니다")
    try:
        duration = float(data["duration_s"])
        rate = int(data["sample_rate"])
        channels = int(data["channels"])
        peak = float(data["peak_dbfs"])
        rms = float(data["rms_dbfs"])
        silence = float(data["silence_ratio"])
        clipping = float(data["clipping_ratio"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return AudioQuality(False, "manifest", note="음질 지표 형식이 올바르지 않습니다")
    values = (duration, peak, rms, silence, clipping)
    if not all(math.isfinite(v) for v in values) or duration < 0 or rate <= 0 or channels <= 0:
        return AudioQuality(False, "manifest", note="음질 지표 범위가 올바르지 않습니다")
    if not (0 <= silence <= 1 and 0 <= clipping <= 1):
        return AudioQuality(False, "manifest", note="음질 비율은 0~1이어야 합니다")
    return AudioQuality(
        True, "촬영 앱", round(duration, 2), rate, channels,
        round(peak, 1), round(rms, 1), round(silence, 4), round(clipping, 4),
        _issues(duration, rms, silence, clipping),
    )


def inspect_wav(path: str | Path) -> AudioQuality:
    """PCM WAV를 표준 라이브러리만으로 검사한다."""
    p = Path(path)
    try:
        with wave.open(str(p), "rb") as w:
            channels, width, rate, frames = (
                w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
            if width not in (1, 2, 3, 4) or rate <= 0 or channels <= 0:
                return AudioQuality(False, "WAV", note="지원하지 않는 WAV 형식입니다")
            raw = w.readframes(frames)
    except (OSError, EOFError, wave.Error) as exc:
        return AudioQuality(False, "WAV", note=f"WAV를 읽지 못했습니다: {exc}")
    if not raw or not frames:
        return AudioQuality(False, "WAV", note="빈 WAV 파일입니다")

    samples = _pcm_samples(raw, width)
    if not samples:
        return AudioQuality(False, "WAV", note="PCM 표본이 없습니다")
    max_int = float((1 << (width * 8 - 1)) - 1)
    stride = max(1, len(samples) // 500_000)
    picked = samples[::stride]
    norm = [min(1.0, abs(v) / max_int) for v in picked]
    peak = max(norm)
    rms_linear = math.sqrt(sum(v * v for v in norm) / len(norm))
    silence = sum(v < 10 ** (-50 / 20) for v in norm) / len(norm)
    clipping = sum(v >= 0.999 for v in norm) / len(norm)
    duration = frames / rate
    rms = _dbfs(rms_linear)
    return AudioQuality(
        True, "PC WAV", round(duration, 2), rate, channels, _dbfs(peak), rms,
        round(silence, 4), round(clipping, 4),
        _issues(duration, rms, silence, clipping),
    )


def _pcm_samples(raw: bytes, width: int) -> list[int]:
    if width == 1:
        return [v - 128 for v in raw]
    if width in (2, 4):
        code = "h" if width == 2 else "i"
        vals = array.array(code)
        vals.frombytes(raw)
        if sys.byteorder != "little":
            vals.byteswap()
        return list(vals)
    # 24-bit little-endian PCM
    out = []
    for i in range(0, len(raw) - 2, 3):
        v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
        if v & 0x800000:
            v -= 1 << 24
        out.append(v)
    return out
