"""깜박임 — 별표5 8.a (초당 3회 **미만**).

'미만'이므로 정확히 3.0Hz 는 부적합이다. 경계 처리를 틀리면 안 되는 항목.

측정 방식
    WCAG 의 광과민성 판정을 단순화해 적용한다. 프레임별 상대 휘도 평균을 구하고,
    유의미한 진폭의 밝기 반전 횟수를 세어 Hz 로 환산한다. 화면 전체가 아니라
    지정 영역(ROI)만 볼 수 있게 해 두어, 배너 하나가 깜박이는 경우도 잡는다.

프레임률 제약
    3Hz 를 판정하려면 나이퀴스트상 최소 6fps 로 충분해 보이지만, 실제로는 셔터와
    화면 리프레시의 비팅(beating) 때문에 여유가 필요하다. 촬영 프로토콜은 30fps
    이상을 요구하고, 그 미만이면 판정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

MAX_FLASHES_PER_SEC = 3.0     # 이 값 '미만'이어야 적합
MIN_FPS = 30.0                # capture-protocol.yaml S2.constraints.fps_min

# 밝기 반전으로 셀 최소 진폭 (상대 휘도 0~1 스케일).
# 너무 낮으면 노이즈를 깜박임으로 세고, 너무 높으면 저대비 점멸을 놓친다.
MIN_AMPLITUDE = 0.10


class FrameRateTooLow(RuntimeError):
    """프레임률이 낮아 판정이 성립하지 않는다. 재촬영을 요구한다."""


@dataclass
class FlashResult:
    """8.a 판정 결과."""

    fps: float
    duration_sec: float
    flashes: int
    peak_hz: float
    passed: bool
    roi: str
    confidence: float

    def as_measured(self) -> dict[str, object]:
        return {
            "구간": self.roi,
            "최고 깜박임": round(self.peak_hz, 2),
            "기준": f"{MAX_FLASHES_PER_SEC}회/초 미만",
            "검출 횟수": self.flashes,
            "영상 길이": round(self.duration_sec, 1),
            "프레임률": round(self.fps, 1),
        }


def count_flashes(
    luminances: Sequence[float],
    *,
    min_amplitude: float = MIN_AMPLITUDE,
) -> int:
    """휘도 시계열에서 '깜박임' 횟수를 센다.

    한 번의 깜박임은 **서로 반대 방향의 휘도 변화 한 쌍**(밝아졌다 어두워지거나,
    어두워졌다 밝아지는 한 주기)이다. 방향 전환 하나를 한 번으로 세면 실제의
    두 배가 나와 2Hz 점멸을 4회/초로 오판한다.

    히스테리시스로 노이즈를 거른다 — 마지막 극값에서 min_amplitude 이상
    반대 방향으로 움직였을 때만 방향 전환으로 인정한다.
    """
    if len(luminances) < 3:
        return 0

    reversals = 0
    last_extreme = luminances[0]
    direction = 0  # +1 상승, -1 하강, 0 미정

    for value in luminances[1:]:
        delta = value - last_extreme
        if abs(delta) < min_amplitude:
            continue
        new_direction = 1 if delta > 0 else -1
        if new_direction != direction:
            if direction != 0:
                reversals += 1
            direction = new_direction
        last_extreme = value

    return reversals // 2   # 반전 두 번이 깜박임 한 번


def evaluate(
    luminances: Sequence[float],
    fps: float,
    *,
    roi: str = "화면 전체",
    window_sec: float = 1.0,
    min_amplitude: float = MIN_AMPLITUDE,
) -> FlashResult:
    """휘도 시계열로 8.a 를 판정한다.

    1초 슬라이딩 창의 최댓값을 쓴다. 영상 전체 평균을 쓰면 3초짜리 점멸 구간이
    30초 영상 안에서 희석되어 위반을 놓친다.

    Raises:
        FrameRateTooLow: fps 가 MIN_FPS 미만.
    """
    if fps < MIN_FPS:
        raise FrameRateTooLow(
            f"프레임률 {fps:.1f}fps 는 8.a 판정 최소치({MIN_FPS:.0f}fps) 미만입니다"
        )
    if not luminances:
        raise ValueError("휘도 시계열이 비어 있습니다")

    window = max(2, int(round(fps * window_sec)))
    duration = len(luminances) / fps

    peak = 0.0
    total = count_flashes(luminances, min_amplitude=min_amplitude)

    if len(luminances) <= window:
        peak = total / max(duration, 1e-6)
    else:
        for start in range(0, len(luminances) - window + 1):
            chunk = luminances[start:start + window]
            hz = count_flashes(chunk, min_amplitude=min_amplitude) / window_sec
            peak = max(peak, hz)

    # 프레임률이 높을수록 신뢰도가 오른다. 30fps=0.85, 60fps 이상=1.0
    confidence = round(min(1.0, 0.85 + 0.15 * (fps - MIN_FPS) / MIN_FPS), 3)

    return FlashResult(
        fps=fps,
        duration_sec=duration,
        flashes=total,
        peak_hz=peak,
        passed=peak < MAX_FLASHES_PER_SEC,   # '미만' — 경계 포함하지 않는다
        roi=roi,
        confidence=confidence,
    )


def luminance_series_from_video(path: str, *, roi: tuple[int, int, int, int] | None = None):
    """영상에서 프레임별 상대 휘도 평균을 뽑는다 (OpenCV 필요).

    Args:
        roi: (x, y, w, h). None 이면 화면 전체.

    Returns:
        (luminances, fps)
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError("영상 분석에는 opencv-python 이 필요합니다") from exc

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"영상을 열 수 없습니다: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    series: list[float] = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if roi:
                x, y, w, h = roi
                frame = frame[y:y + h, x:x + w]
            # BGR → 상대 휘도 근사 (WCAG 계수, 선형화 생략은 상대 변화만 보므로 허용)
            b, g, r = frame[..., 0], frame[..., 1], frame[..., 2]
            lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
            series.append(float(np.mean(lum)))
    finally:
        cap.release()

    return series, fps
