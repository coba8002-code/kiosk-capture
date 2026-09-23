"""작동부 치수 — 별표5 1.b(버튼 간격) / 1.c(최소경계상자) / 1.i(배출물 돌출) / 9.a·9.b(높이).

모든 계산은 PlaneScale 을 통과한 mm 좌표 위에서 이뤄진다.
마커가 없으면 이 모듈은 아무것도 계산하지 않는다 — 픽셀 값을 그럴듯한 mm 로
바꿔치기하는 순간 리포트 전체의 신뢰가 무너지기 때문이다.

1.c 의 144mm² 와 150mm² — 원문 대조 결과 (2026-08-21 확정)
    두 수치는 같은 것을 재는 서로 다른 값이 아니라, **서로 다른 것을 재는 두 기준**이다.

      별표5 1.c        모든 작동부의 최소경계상자(MBR, 기기 축 정렬)
                       → 각 변 12mm 이상 AND 면적 144mm² 이상
      KS §5.6.2        버튼 및 키의 '손가락으로 누르는 부위'
                       → 면적 150mm² 이상 AND 한 변 12mm 이상
                       (참고: BS EN 1332-3:2008, 5.3.1)

    두 가지가 따라 나온다.

    ① 별표5의 144mm² 는 독립적인 제약이 아니다. 축 정렬 경계상자는 두 변이 모두
       12mm 이상이면 면적이 반드시 144mm²(=12×12) 이상이므로, 실질 구속 조건은
       12mm 변 하나뿐이다. 면적 조건은 확인 사살이다.
    ② KS 가 더 엄격하다. 누름 부위 면적은 경계상자 면적보다 작고, 원형·다각형이면
       훨씬 작다(KS 비고 3 이 형상 다양성을 명시). 지름 12mm 원형 버튼은
       MBR 144mm² 로 별표5를 통과하지만 누름 면적은 113mm² 라 KS 150 에 못 미친다.
       150mm² 를 원형으로 채우려면 지름 13.8mm 가 필요하다.

    따라서 엔진은 **두 값을 따로 계산한다** — 경계상자 면적(법적 판정)과
    누름 부위 실제 면적(설계 권고). 하나로 뭉뚱그리면 원형 버튼을 놓친다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .scale import PlaneScale

Point = tuple[float, float]

# 별표5 임계값 — 법적 판정
SIDE_MIN_MM = 12.0          # 1.c 경계상자 각 변
MBR_AREA_MIN_MM2 = 144.0    # 1.c 경계상자 면적 (= 12 × 12, 변 조건에서 자동 도출)

# KS X 9211:2025 §5.6.2 — 설계 권고. 누름 부위 실제 면적에 걸린다
PRESSABLE_AREA_ADVISORY_MM2 = 150.0
GAP_TOUCH_MIN_MM = 2.5      # 1.b 터치스크린
GAP_PHYSICAL_MIN_MM = 0.5   # 1.b 물리적 작동부
PROTRUSION_MIN_MM = 20.0    # 1.i 낱장 배출물
REACH_MIN_MM = 400.0        # 9.a
REACH_MAX_MM = 1220.0       # 9.a
VISUAL_MAX_MM = 1400.0      # 9.b

# 물리 간격 0.5mm 는 촬영 해상도·렌즈 왜곡의 오차와 겹친다.
# 이 배율만큼의 여유가 없으면 '적합'을 말하지 않는다(annex5 1.b ai_verdict_scope: fail_only).
PHYSICAL_GAP_SAFETY = 3.0


@dataclass
class Box:
    """작동부 하나. mm 좌표, 기기 축 정렬 경계상자 + 누름 부위 실제 면적."""

    id: str
    x: float
    y: float
    w: float
    h: float
    pressable_area: float | None = None   # 누름 부위 실제 면적(mm²). None 이면 미측정

    @property
    def area(self) -> float:
        """축 정렬 경계상자 면적 — 별표5 1.c 가 요구하는 값."""
        return self.w * self.h

    @property
    def min_side(self) -> float:
        return min(self.w, self.h)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class MbrResult:
    """1.c 판정 결과. 법적 판정과 설계 권고를 분리해 담는다."""

    boxes: list[Box]
    violations: list[dict] = field(default_factory=list)   # 별표5 부적합
    borderline: list[dict] = field(default_factory=list)   # 허용오차 안 — 실측 필요
    advisory: list[dict] = field(default_factory=list)     # KS 권고 미달
    unmeasured_pressable: int = 0                          # 누름 면적을 못 잰 개수
    tolerance_mm: float = 0.0
    passed: bool = True

    def as_measured(self) -> dict[str, object]:
        out: dict[str, object] = {
            "검사 작동부 수": len(self.boxes),
            "기준 미달 수": len(self.violations),
            "기준": f"경계상자 각 변 {SIDE_MIN_MM}mm 이상, 면적 {MBR_AREA_MIN_MM2}mm² 이상 (별표5 1.c)",
            "미달 목록": self.violations[:10],
        }
        if self.borderline:
            out["경계 근접"] = len(self.borderline)
            out["경계 근접 목록"] = self.borderline[:10]
            out["경계 근접 처리"] = (
                f"계측 허용오차 ±{self.tolerance_mm:.2f}mm 안에 걸쳐 있어 "
                f"적합이라 말하지 않습니다. 실측으로 확정하세요."
            )
        if self.unmeasured_pressable < len(self.boxes):
            out["설계 권고 미달"] = len(self.advisory)
            out["권고 기준"] = (
                f"누름 부위 면적 {PRESSABLE_AREA_ADVISORY_MM2}mm² 이상 "
                f"(KS X 9211:2025 §5.6.2 — 법적 판정 아님)"
            )
            out["권고 미달 목록"] = self.advisory[:10]
        if self.unmeasured_pressable:
            out["누름 면적 미측정"] = self.unmeasured_pressable
        return out


def boxes_from_pixels(
    quads_px: dict[str, Sequence[Point]],
    scale: PlaneScale,
) -> list[Box]:
    """이미지상의 작동부 윤곽을 mm 좌표로 옮긴다.

    두 가지를 함께 낸다.
      · 축 정렬 경계상자 — 별표5 1.c 는 '무인정보단말기 축에 정렬된 상태'를 요구한다
      · 누름 부위 실제 면적 — KS §5.6.2 권고용. 윤곽이 3점 이상일 때만 계산한다

    윤곽점이 많을수록(원형 버튼을 다각형으로 근사한 경우) 누름 면적이 정확해진다.
    """
    out: list[Box] = []
    for bid, quad in quads_px.items():
        pts = [scale.to_mm(p) for p in quad]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        pressable = scale.area_mm2(quad) if len(quad) >= 3 else None
        out.append(Box(
            id=bid,
            x=min(xs), y=min(ys),
            w=max(xs) - min(xs), h=max(ys) - min(ys),
            pressable_area=pressable,
        ))
    return out


def check_mbr(boxes: Sequence[Box], *, tolerance_mm: float = 0.0) -> MbrResult:
    """1.c 판정.

    법적 판정 — 별표5 1.c: 경계상자 각 변 12mm 이상 AND 면적 144mm² 이상.
    설계 권고 — KS §5.6.2: 누름 부위 실제 면적 150mm² 이상.

    두 조건을 한 숫자로 합치지 않는다. 경계상자 면적을 150 과 비교하면
    지름 12mm 원형 버튼(경계상자 144mm², 실제 누름 면적 113mm²)을 놓친다.

    Args:
        tolerance_mm: 계측 허용오차. 12mm 를 이만큼 넘겨야 적합으로 본다.
            사진 계측에는 잔차가 남으므로, 경계에 걸친 버튼을 통과시키면
            **실제 위반을 적합이라 말하게 된다.** 그 구간은 실측으로 넘긴다.
    """
    result = MbrResult(boxes=list(boxes), tolerance_mm=tolerance_mm)

    for b in boxes:
        # ── 법적 판정 (별표5) ────────────────────────────────────────────
        if b.min_side < SIDE_MIN_MM - tolerance_mm or b.area < MBR_AREA_MIN_MM2:
            result.violations.append({
                "작동부": b.id,
                "최소 변": round(b.min_side, 2),
                "경계상자 면적": round(b.area, 1),
            })
            continue   # 이미 부적합이면 권고는 따지지 않는다

        if b.min_side < SIDE_MIN_MM + tolerance_mm:
            result.borderline.append({
                "작동부": b.id,
                "최소 변": round(b.min_side, 2),
                "기준": SIDE_MIN_MM,
            })

        # ── 설계 권고 (KS §5.6.2) ────────────────────────────────────────
        if b.pressable_area is None:
            result.unmeasured_pressable += 1
        elif b.pressable_area < PRESSABLE_AREA_ADVISORY_MM2:
            result.advisory.append({
                "작동부": b.id,
                "누름 부위 면적": round(b.pressable_area, 1),
                "경계상자 면적": round(b.area, 1),
            })

    result.passed = not result.violations
    return result


@dataclass
class GapResult:
    """1.b 판정 결과."""

    surface: str                       # touch | physical
    pairs_checked: int
    min_gap_mm: float | None
    required_mm: float
    violations: list[dict] = field(default_factory=list)
    passed: bool = True
    can_confirm_pass: bool = True      # False 면 적합을 선언하지 않는다

    def as_measured(self) -> dict[str, object]:
        return {
            "대상": "터치스크린" if self.surface == "touch" else "물리적 작동부",
            "검사 쌍": self.pairs_checked,
            "최소 간격": None if self.min_gap_mm is None else round(self.min_gap_mm, 2),
            "기준": f"{self.required_mm}mm 이상",
            "미달 목록": self.violations[:10],
            "적합 확정 가능": self.can_confirm_pass,
        }


def _edge_gap(a: Box, b: Box) -> float:
    """두 축정렬 상자 사이의 최단 간격(mm). 겹치면 0."""
    dx = max(0.0, max(a.x, b.x) - min(a.x + a.w, b.x + b.w))
    dy = max(0.0, max(a.y, b.y) - min(a.y + a.h, b.y + b.h))
    if dx == 0.0 and dy == 0.0:
        return 0.0
    if dx == 0.0:
        return dy
    if dy == 0.0:
        return dx
    return (dx * dx + dy * dy) ** 0.5


def check_gaps(
    boxes: Sequence[Box],
    *,
    surface: str,
    neighbor_radius_mm: float = 60.0,
    measurement_error_mm: float = 0.0,
) -> GapResult:
    """1.b — 이웃한 버튼 사이 간격.

    Args:
        surface: 'touch'(2.5mm) 또는 'physical'(0.5mm)
        neighbor_radius_mm: 이 거리 안의 버튼만 '이웃'으로 본다.
        measurement_error_mm: 마커 환산의 예상 오차. 물리 표면의 적합 확정 판단에 쓴다.
    """
    required = GAP_TOUCH_MIN_MM if surface == "touch" else GAP_PHYSICAL_MIN_MM
    result = GapResult(
        surface=surface,
        pairs_checked=0,
        min_gap_mm=None,
        required_mm=required,
    )

    for i, a in enumerate(boxes):
        for b in list(boxes)[i + 1:]:
            center_dist = ((a.cx - b.cx) ** 2 + (a.cy - b.cy) ** 2) ** 0.5
            if center_dist > neighbor_radius_mm:
                continue
            gap = _edge_gap(a, b)
            result.pairs_checked += 1
            result.min_gap_mm = gap if result.min_gap_mm is None else min(result.min_gap_mm, gap)
            if gap < required:
                result.violations.append({
                    "버튼 쌍": f"{a.id} ↔ {b.id}",
                    "간격": round(gap, 2),
                })

    result.passed = not result.violations

    # 물리 간격 0.5mm 는 측정 오차와 겹치므로, 여유가 충분할 때만 적합을 확정한다.
    if surface == "physical":
        margin_needed = required + measurement_error_mm * PHYSICAL_GAP_SAFETY
        result.can_confirm_pass = (
            result.min_gap_mm is not None and result.min_gap_mm >= margin_needed
        )
    return result


@dataclass
class HeightResult:
    """9.a / 9.b 판정 결과."""

    label: str
    height_mm: float
    lo: float | None
    hi: float | None
    passed: bool
    margin_mm: float
    near_boundary: bool

    def as_measured(self) -> dict[str, object]:
        rng = (
            f"{self.lo:.0f}~{self.hi:.0f}mm" if self.lo is not None and self.hi is not None
            else f"{self.hi:.0f}mm 이하"
        )
        return {
            "대상": self.label,
            "추정 높이": round(self.height_mm, 1),
            "기준": rng,
            "경계 여유": round(self.margin_mm, 1),
            "경계 근접": self.near_boundary,
        }


def check_height(
    height_mm: float,
    *,
    label: str,
    lo: float | None = REACH_MIN_MM,
    hi: float = REACH_MAX_MM,
    boundary_band_mm: float = 30.0,
) -> HeightResult:
    """9.a(400~1,220mm) / 9.b(≤1,400mm) 판정.

    경계값 근처(기본 ±30mm)는 near_boundary 로 표시한다.
    이 플래그가 켜지면 엔진은 실측 없이 적합을 선언하지 않는다.
    """
    if lo is None:
        passed = height_mm <= hi
        margin = hi - height_mm
    else:
        passed = lo <= height_mm <= hi
        margin = min(height_mm - lo, hi - height_mm)

    return HeightResult(
        label=label,
        height_mm=height_mm,
        lo=lo,
        hi=hi,
        passed=passed,
        margin_mm=margin,
        near_boundary=abs(margin) <= boundary_band_mm,
    )


def check_protrusion(protrusion_mm: float) -> dict[str, object]:
    """1.i — 낱장 배출물이 20mm 이상 돌출되는가."""
    return {
        "돌출 길이": round(protrusion_mm, 1),
        "기준": f"{PROTRUSION_MIN_MM}mm 이상",
        "충족": protrusion_mm >= PROTRUSION_MIN_MM,
    }
