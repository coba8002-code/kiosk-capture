"""작동부 검출 — 별표5 1.b(간격) / 1.c(최소경계상자)를 자동 판정하기 위한 전처리.

마커 환산은 이미 오차 2% 로 동작한다. 남은 문제는 하나였다 — **어디가 버튼인가.**
사람이 화면마다 버튼을 손으로 지정하면 진단이 자동화되지 않는다.

치수는 반드시 mm 평면에서 잰다
────────────────────────────────────────────────────────────────────
    처음엔 사진(픽셀) 공간의 축정렬 경계상자를 재고 마커 배율만 곱했다. 틀렸다.
    기울여 찍으면 버튼은 사진 위에서 사다리꼴이 되고, 그 사다리꼴의 축정렬
    경계상자는 실제 버튼보다 크다. 기울기 0.18 에서 16mm 버튼이 18.1mm 로 나왔고,
    **10mm 버튼이 12.9mm 로 읽혀 12mm 기준을 통과했다** — 위반을 놓친 것이다.

    별표5 1.c 가 요구하는 '축 정렬'은 사진의 축이 아니라 **기기의 축**이다.
    그래서 윤곽점을 먼저 mm 평면으로 옮기고, 거기서 경계상자를 잡는다.
    같은 조건에서 16.5mm / 10.5mm 로 바로잡혔다.

경계 편의(edge bias) — 실측 교정
────────────────────────────────────────────────────────────────────
    Canny 는 밝기 기울기의 마루에 선을 그으므로 윤곽이 물체보다 바깥으로 부푼다.
    합성 촬영(정답 기지)에서 기울기 0.05~0.25 로 잰 변당 편의는
    +0.39 ~ +1.93px, 평균 1.3px 였다.

    이 방향이 위험하다. 크기를 부풀리면 **작은 버튼이 기준을 통과해 버린다.**
    그래서 편의를 빼고, 남은 산포(±0.8px)는 허용오차로 남겨 두어
    경계에 걸친 버튼을 적합이라 말하지 않고 실측으로 넘긴다.

무엇을 보장하지 않는가
    화면의 **모든** 버튼을 빠짐없이 찾는다는 보장이 없다.
    그래서 1.b·1.c 는 둘 다 ai_verdict_scope: fail_only 다 —
    위반을 찾으면 검토자에게 올리지만, 못 찾았다고 적합을 선언하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..calc.geometry import Box
from ..calc.scale import PlaneScale

# 버튼일 만한 실제 크기 (mm). 이 범위 밖은 노이즈이거나 케이스다.
MIN_SIDE_MM = 4.0
MAX_SIDE_MM = 70.0
MIN_AREA_MM2 = 20.0
MAX_AREA_MM2 = 3500.0

# 형상 필터
MAX_ASPECT = 6.0            # 지나치게 길쭉하면 버튼이 아니라 슬롯·틈새다
MIN_RECTANGULARITY = 0.62   # 최소외접회전사각 대비 볼록껍질 면적. 글자·아이콘을 뺀다

# 경계 편의 — 합성 촬영 실측 (2026-08-22, 기울기 0.05~0.25, 7.3~7.9px/mm)
EDGE_BIAS_PX = 1.3          # 변당 바깥으로 부푸는 양
EDGE_BIAS_SPREAD_PX = 0.8   # 교정 후에도 남는 산포 → 허용오차로 쓴다
BIAS_CALIBRATED = True

# 중첩 제거 — 윤곽 검출은 같은 버튼의 안팎 테두리를 둘 다 낸다
CONTAINMENT_DEDUPE = 0.70

# 글자 덩어리 배제 — 화면 평면에서 문단이 버튼으로 잡히는 것을 막는다.
# 버튼은 자기 라벨보다 크므로 '문자 영역이 후보를 거의 다 덮으면 그건 글자다'.
TEXT_COINCIDENCE = 0.70

# 잘린 사진 한 장에 진단 전체가 죽지 않도록 하는 하한
MIN_IMAGE_PX = 16

# 격자 판정 — 키패드처럼 줄 맞춰 배열된 것은 버튼일 확률이 크다
GRID_ALIGN_TOL_MM = 2.5


@dataclass
class ControlCandidate:
    """작동부 후보 하나.

    치수는 전부 mm 평면(기기 축) 값이다. 사진 좌표는 근거 crop 을 잘라 보여줄
    때만 쓰므로 hull_px 로 따로 들고 있는다.
    """

    id: str
    hull_px: list[tuple[float, float]]
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float
    pressable_mm2: float
    in_grid: bool = False

    @property
    def min_side(self) -> float:
        return min(self.w_mm, self.h_mm)

    def to_box(self) -> Box:
        """geometry 계산기가 받는 형태. 이미 mm 이므로 변환이 없다."""
        return Box(id=self.id, x=self.x_mm, y=self.y_mm,
                   w=self.w_mm, h=self.h_mm, pressable_area=self.pressable_mm2)


@dataclass
class DetectionResult:
    candidates: list[ControlCandidate] = field(default_factory=list)
    raw_contours: int = 0
    grid_rows: int = 0
    text_dropped: int = 0
    tolerance_mm: float = 0.0
    note: str = ""

    def __len__(self) -> int:
        return len(self.candidates)

    def boxes(self) -> list[Box]:
        """geometry.check_mbr / check_gaps 에 바로 넣는다."""
        return [c.to_box() for c in self.candidates]

    def as_measured(self) -> dict[str, object]:
        return {
            "검출 작동부": len(self.candidates),
            "윤곽 후보": self.raw_contours,
            "격자 정렬 행": self.grid_rows,
            "글자 배제": self.text_dropped,
            "치수 허용오차": f"±{self.tolerance_mm:.2f}mm (경계 편의 교정 후 잔차)",
            "커버리지": (
                "윤곽 검출로 찾은 후보입니다. 모든 버튼을 빠짐없이 찾는다는 보장이 없어 "
                "위반 검출에만 쓰고 '적합' 확정 근거로는 쓰지 않습니다."
            ),
        }


def detect(image_bgr, scale: PlaneScale, *, exclude=None, text_boxes=None,
           max_items: int = 60) -> DetectionResult:
    """조작부(또는 화면) 사진에서 작동부 후보를 찾는다.

    Args:
        image_bgr: 마커와 같은 평면을 담은 사진 (BGR)
        scale: 그 평면의 PlaneScale — 크기 필터를 mm 로 걸기 위해 필수
        exclude: 제외 영역 [(x, y, w, h)] — 마커 카드를 반드시 넣는다
        text_boxes: 문자 영역 [(x, y, w, h)]. 화면 평면에서 꼭 넣는다 —
            넣지 않으면 본문 문단이 작동부로 잡혀 줄 간격이 1.b 위반으로
            무더기 보고된다. 버튼은 자기 라벨보다 크므로,
            문자 영역이 후보를 거의 다 덮을 때만 버린다.
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError("작동부 검출에는 opencv-python 이 필요합니다") from exc

    if image_bgr is None or image_bgr.size == 0:
        return DetectionResult(note="이미지 없음")
    if min(image_bgr.shape[:2]) < MIN_IMAGE_PX:
        return DetectionResult(
            note=f"이미지가 너무 작습니다 ({image_bgr.shape[1]}x{image_bgr.shape[0]}px)")
    if scale is None:
        # 마커 없이 픽셀을 mm 로 둔갑시키지 않는다. 조용히 넘어가지도 않는다.
        raise ValueError("작동부 검출에는 PlaneScale 이 필요합니다 (마커 미검출)")

    ppm = max(scale.px_per_mm, 1e-6)
    tol_mm = 2.0 * EDGE_BIAS_SPREAD_PX / ppm
    bias_mm = EDGE_BIAS_PX / ppm

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 60, 60)     # 경계는 살리고 노이즈만 줄인다

    # 조명이 고르지 않은 현장 사진을 위해 밝기 절대값이 아니라 기울기로 찾는다
    med = float(np.median(gray))
    edges = cv2.Canny(gray, int(max(0.0, 0.66 * med)), int(min(255.0, 1.33 * med)),
                      L2gradient=True)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))

    # RETR_EXTERNAL 은 쓸 수 없다 — 기기 외곽선 하나만 남고 안쪽 버튼이 전부 사라진다
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    cands: list[ControlCandidate] = []
    text_dropped = 0
    for i, cnt in enumerate(contours):
        if len(cnt) < 4:
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw < 5 or bh < 5:
            continue
        if exclude and any(_overlaps((bx, by, bw, bh), e) for e in exclude):
            continue

        hull = cv2.convexHull(cnt)
        rw, rh = cv2.minAreaRect(hull)[1]          # 형상 필터용 — 계측에는 쓰지 않는다
        if rw < 1.0 or rh < 1.0:
            continue
        if cv2.contourArea(hull) / (rw * rh) < MIN_RECTANGULARITY:
            continue

        if text_boxes and any(_covers((bx, by, bw, bh), t, TEXT_COINCIDENCE)
                              for t in text_boxes):
            text_dropped += 1
            continue

        hull_px = [(float(p[0]), float(p[1])) for p in hull.reshape(-1, 2)]
        try:
            box = _measure(hull_px, scale, bias_mm)
        except Exception:                                   # noqa: BLE001
            continue
        if box is None:
            continue
        x_mm, y_mm, w_mm, h_mm, pressable = box

        if not (MIN_SIDE_MM <= min(w_mm, h_mm) and max(w_mm, h_mm) <= MAX_SIDE_MM):
            continue
        if not (MIN_AREA_MM2 <= w_mm * h_mm <= MAX_AREA_MM2):
            continue
        if max(w_mm, h_mm) / max(1e-6, min(w_mm, h_mm)) > MAX_ASPECT:
            continue

        cands.append(ControlCandidate(
            id=f"btn{i}", hull_px=hull_px,
            x_mm=x_mm, y_mm=y_mm, w_mm=w_mm, h_mm=h_mm, pressable_mm2=pressable,
        ))

    cands = _dedupe(cands)
    cands.sort(key=lambda c: (c.y_mm, c.x_mm))
    for n, c in enumerate(cands, 1):
        c.id = f"작동부{n}"

    rows = _mark_grid(cands)

    return DetectionResult(
        candidates=cands[:max_items],
        raw_contours=len(contours),
        grid_rows=rows,
        text_dropped=text_dropped,
        tolerance_mm=tol_mm,
        note=("윤곽 검출 결과입니다. 놓친 버튼이 있을 수 있으므로 "
              "위반 검출에만 사용하고 적합 확정에는 쓰지 않습니다."),
    )


def _measure(hull_px, scale: PlaneScale, bias_mm: float):
    """윤곽을 mm 평면으로 옮겨 기기 축 정렬 경계상자와 누름 면적을 낸다.

    사진 공간에서 재면 안 된다. 별표5 1.c 의 '축 정렬'은 사진의 축이 아니라
    **기기의 축**이고, 기울여 찍은 사진에서 두 축은 다르다.
    사진 공간의 회전사각으로 재던 때는 기울기 0.25 에서 16mm 버튼이
    20.2mm 로 나왔다 — 원근 사각형을 회전사각이 과대포함하기 때문이다.

    Returns:
        (x_mm, y_mm, w_mm, h_mm, 누름면적_mm²) 또는 편의보다 작으면 None
    """
    pts = [scale.to_mm(p) for p in hull_px]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w_raw, h_raw = max(xs) - min(xs), max(ys) - min(ys)
    if w_raw <= 2 * bias_mm or h_raw <= 2 * bias_mm:
        return None

    w_mm, h_mm = w_raw - 2 * bias_mm, h_raw - 2 * bias_mm
    # 누름 면적(KS 권고)은 다각형 그대로 재고 경계상자와 같은 비율로 줄인다.
    # 원형 버튼을 사각으로 근사하면 지름 12mm 버튼의 113mm² 를 144mm² 로 읽어
    # KS 150mm² 권고 미달을 놓친다 — geometry 모듈 머리말이 지적하는 바로 그 함정.
    pressable = scale.area_mm2(hull_px) * (w_mm * h_mm) / (w_raw * h_raw)
    return min(xs) + bias_mm, min(ys) + bias_mm, w_mm, h_mm, pressable


def _covers(box, region, thresh: float) -> bool:
    """region 이 box 를 thresh 이상 덮는가."""
    return _overlaps(box, region, thresh)


def _overlaps(box, region, thresh: float = 0.25) -> bool:
    bx, by, bw, bh = box
    rx, ry, rw, rh = region
    x0, y0 = max(bx, rx), max(by, ry)
    x1, y1 = min(bx + bw, rx + rw), min(by + bh, ry + rh)
    if x1 <= x0 or y1 <= y0:
        return False
    return ((x1 - x0) * (y1 - y0)) / float(bw * bh) >= thresh


def _aabb(c: ControlCandidate) -> tuple[float, float, float, float]:
    return c.x_mm, c.y_mm, c.x_mm + c.w_mm, c.y_mm + c.h_mm


def _containment(a: ControlCandidate, b: ControlCandidate) -> float:
    """겹친 면적 / 둘 중 작은 면적."""
    ax0, ay0, ax1, ay1 = _aabb(a)
    bx0, by0, bx1, by1 = _aabb(b)
    x0, y0 = max(ax0, bx0), max(ay0, by0)
    x1, y1 = min(ax1, bx1), min(ay1, by1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    aa = (ax1 - ax0) * (ay1 - ay0)
    bb = (bx1 - bx0) * (by1 - by0)
    return inter / max(1e-9, min(aa, bb))


def _dedupe(cands: list[ControlCandidate]) -> list[ControlCandidate]:
    """같은 버튼의 안팎 테두리를 하나로 합친다.

    IoU 로는 안 된다 — 큰 윤곽 안에 작은 윤곽이 들어 있으면 IoU 가 면적비만큼
    작아져 둘 다 살아남고, 그 둘 사이 간격이 0mm 로 잡혀
    **1.b 가 있지도 않은 위반을 무더기로 만들어 낸다.** 포함율로 판단한다.
    """
    out: list[ControlCandidate] = []
    for c in sorted(cands, key=lambda c: -(c.w_mm * c.h_mm)):
        if all(_containment(c, o) < CONTAINMENT_DEDUPE for o in out):
            out.append(c)
    return out


def _mark_grid(cands: list[ControlCandidate]) -> int:
    """줄 맞춰 배열된 후보에 표시한다 — 키패드는 격자로 놓인다."""
    if len(cands) < 3:
        return 0
    centers = [c.y_mm + c.h_mm / 2 for c in cands]

    rows = 0
    used = [False] * len(cands)
    for i, cy in enumerate(centers):
        if used[i]:
            continue
        group = [j for j, other in enumerate(centers)
                 if not used[j] and abs(other - cy) <= GRID_ALIGN_TOL_MM]
        if len(group) >= 2:
            rows += 1
            for j in group:
                used[j] = True
                cands[j].in_grid = True
    return rows
