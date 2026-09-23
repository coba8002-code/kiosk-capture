"""기준 스케일 마커 → 픽셀·밀리미터 환산.

이 모듈이 없으면 별표5의 치수 항목(1.b·1.c·1.i·3.g·9.a·9.b)이 전부 '추정'에 머문다.
마커 카드 한 장이 그 항목들을 '측정'으로 옮긴다 — 이 사업에서 가장 값싼 결정.

원리
    마커의 네 꼭짓점은 실제 세계에서 한 평면 위의 정사각형이다. 그 네 점이 사진에서
    어디로 갔는지 알면 평면 전체의 사영 변환(호모그래피)이 결정된다. 역변환을 걸면
    사진 좌표를 밀리미터 좌표로 되돌릴 수 있다.

전제 (capture-protocol.yaml marker.usage)
    호모그래피는 **하나의 평면에 대해서만** 성립한다. 화면 평면과 물리 버튼 평면은
    깊이가 다르므로 마커를 각 평면에 따로 두고 각각 환산한다. 다른 평면의 마커로
    치수를 재면 깊이 차이만큼 그대로 오차가 된다.

의존성: numpy (선형 방정식 풀이). OpenCV 는 마커 '검출'에만 쓰고, 환산 자체는
        여기서 독립적으로 구현해 테스트 가능하게 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

Point = tuple[float, float]

# 마커 검출 품질 게이트 (capture-protocol.yaml marker.accuracy.reject_if)
MIN_MARKER_FRAME_RATIO = 0.03      # 마커 폭이 프레임 폭의 3% 미만이면 해상도 부족
MAX_SKEW_RATIO = 2.5               # 사영 왜곡 허용 한계 (변 길이 최대/최소 비)


class MarkerError(RuntimeError):
    """마커가 환산에 쓸 수 없는 상태. 계측을 시도하지 않고 재촬영을 요구한다."""


@dataclass
class PlaneScale:
    """한 평면에 대한 픽셀↔밀리미터 환산기."""

    plane: str                 # display | control_panel | dispenser | elevation
    h_inv: np.ndarray          # 이미지 좌표 → mm 좌표 변환 행렬 (3x3)
    px_per_mm: float           # 마커 근방의 대표 배율 (품질 지표·확신도 산출용)
    skew: float                # 사영 왜곡 정도. 1.0 이 정면, 클수록 기울어짐
    confidence: float

    def to_mm(self, pt: Point) -> Point:
        """이미지 픽셀 좌표를 평면상의 mm 좌표로."""
        v = self.h_inv @ np.array([pt[0], pt[1], 1.0])
        if abs(v[2]) < 1e-12:
            raise MarkerError(f"{self.plane}: 소실점 근처 좌표라 환산 불가")
        return (float(v[0] / v[2]), float(v[1] / v[2]))

    def length_mm(self, a: Point, b: Point) -> float:
        """두 이미지 좌표 사이의 실제 거리(mm)."""
        ax, ay = self.to_mm(a)
        bx, by = self.to_mm(b)
        return float(np.hypot(bx - ax, by - ay))

    def area_mm2(self, polygon: Sequence[Point]) -> float:
        """이미지 좌표 다각형의 실제 면적(mm²). 신발끈 공식."""
        pts = [self.to_mm(p) for p in polygon]
        if len(pts) < 3:
            return 0.0
        s = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2.0


def _homography(src: Sequence[Point], dst: Sequence[Point]) -> np.ndarray:
    """4점 대응으로 호모그래피 행렬을 푼다 (DLT, h33=1 고정).

    src: 이미지 좌표 4점, dst: 대응하는 mm 좌표 4점.
    """
    if len(src) != 4 or len(dst) != 4:
        raise MarkerError("호모그래피에는 정확히 4점이 필요합니다")

    a = np.zeros((8, 8), dtype=float)
    b = np.zeros(8, dtype=float)
    for i in range(4):
        x, y = src[i]
        u, v = dst[i]
        a[2 * i] = [x, y, 1, 0, 0, 0, -u * x, -u * y]
        a[2 * i + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]
        b[2 * i] = u
        b[2 * i + 1] = v

    try:
        h = np.linalg.solve(a, b)
    except np.linalg.LinAlgError as exc:
        raise MarkerError("마커 4점이 퇴화(공선/중복)되어 환산 불가") from exc

    return np.append(h, 1.0).reshape(3, 3)


def build_plane_scale(
    corners_px: Sequence[Point],
    *,
    plane: str,
    marker_side_mm: float = 40.0,
    frame_width_px: int | None = None,
) -> PlaneScale:
    """마커 네 꼭짓점으로 평면 환산기를 만든다.

    Args:
        corners_px: 마커 정사각형의 네 꼭짓점 (좌상 → 우상 → 우하 → 좌하 순서).
        plane: 이 마커가 놓인 평면 이름.
        marker_side_mm: 마커 내부 정사각 변 길이. BFK-MARK-A 는 40.0mm.
        frame_width_px: 프레임 가로 픽셀. 주면 해상도 게이트를 검사한다.

    Raises:
        MarkerError: 해상도 부족·과도한 왜곡·퇴화 등 환산 불가 상태.
    """
    pts = [tuple(map(float, p)) for p in corners_px]
    if len(pts) != 4:
        raise MarkerError(f"{plane}: 마커 4점 중 일부 미검출 ({len(pts)}점)")

    # 변 길이로 해상도·왜곡을 먼저 본다.
    sides = [
        float(np.hypot(pts[(i + 1) % 4][0] - pts[i][0], pts[(i + 1) % 4][1] - pts[i][1]))
        for i in range(4)
    ]
    if min(sides) <= 0:
        raise MarkerError(f"{plane}: 마커 변 길이가 0입니다")

    skew = max(sides) / min(sides)
    if skew > MAX_SKEW_RATIO:
        raise MarkerError(
            f"{plane}: 마커 평면과 촬영각 차이가 큽니다 (왜곡비 {skew:.2f} > {MAX_SKEW_RATIO})"
        )

    if frame_width_px:
        ratio = max(sides) / frame_width_px
        if ratio < MIN_MARKER_FRAME_RATIO:
            raise MarkerError(
                f"{plane}: 마커가 너무 작게 찍혔습니다 "
                f"(프레임 대비 {ratio:.1%} < {MIN_MARKER_FRAME_RATIO:.0%})"
            )

    s = marker_side_mm
    dst = [(0.0, 0.0), (s, 0.0), (s, s), (0.0, s)]
    h_inv = _homography(pts, dst)

    px_per_mm = (sum(sides) / 4.0) / s

    # 왜곡이 클수록 확신도를 깎는다. 1.0(정면) → 1.0, 임계(2.5) → 0.6
    confidence = round(max(0.0, 1.0 - 0.4 * (skew - 1.0) / (MAX_SKEW_RATIO - 1.0)), 3)

    return PlaneScale(
        plane=plane,
        h_inv=h_inv,
        px_per_mm=px_per_mm,
        skew=skew,
        confidence=confidence,
    )


# ArUco 검출기는 마커의 마지막 검은 '픽셀 인덱스'를 코너로 돌려준다.
# 실제 경계보다 1px 안쪽이므로, 사각형을 무게중심 기준으로 1px 만큼 넓혀 보정한다.
# tools/make_marker.py 의 자기검증으로 확인한 값이다 (39.967mm → 40.000mm).
DETECTOR_EDGE_BIAS_PX = 1.0


def correct_edge_bias(corners: Sequence[Point], bias_px: float = DETECTOR_EDGE_BIAS_PX):
    """검출 코너를 무게중심 기준으로 확대해 경계 규약 차이를 보정한다.

    변 길이가 정확히 bias_px 만큼 늘도록 배율을 잡는다.
    코너를 반경 방향으로 bias_px/2 씩 미는 방식은 틀린다 —
    정사각형에서 변이 bias_px 가 아니라 bias_px/√2 만 늘어난다.
    """
    pts = [tuple(map(float, p)) for p in corners]
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n

    sides = [
        ((pts[(i + 1) % n][0] - pts[i][0]) ** 2 + (pts[(i + 1) % n][1] - pts[i][1]) ** 2) ** 0.5
        for i in range(n)
    ]
    mean_side = sum(sides) / n
    if mean_side < 1e-9:
        return pts

    k = (mean_side + bias_px) / mean_side
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in pts]


def detect_marker_corners(
    image_bgr: "np.ndarray",
    *,
    marker_id: int | None = None,
    correct_bias: bool = True,
):
    """ArUco 마커 검출 (OpenCV 필요).

    환산 로직과 분리해 둔 이유는, 검출기는 교체될 수 있지만 환산은 고정되어야
    하기 때문이다. 검출기를 바꿔도 build_plane_scale 의 테스트는 그대로 유효하다.

    Returns:
        (corners, id) 또는 None
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise MarkerError("마커 검출에는 opencv-python 이 필요합니다") from exc

    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    detector = aruco.ArucoDetector(dictionary, aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(image_bgr)

    if ids is None or len(ids) == 0:
        return None

    for quad, mid in zip(corners, ids.flatten()):
        if marker_id is None or int(mid) == marker_id:
            pts = [tuple(map(float, pt)) for pt in quad.reshape(4, 2)]
            if correct_bias:
                pts = correct_edge_bias(pts)
            return pts, int(mid)
    return None
