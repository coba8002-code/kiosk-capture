"""명도 대비 — 별표5 3.i (4.5:1) / 3.j (7:1).

이 사업에서 신뢰도가 가장 높은 항목이다. 마커도 실측 장비도 필요 없고,
WCAG 상대 휘도 공식이 확정적이기 때문이다.

⚠ 상수 동기화
    형제 프로젝트(`BUILD_SPEC_베리어프리_접근성_플랫폼.md` §4.2)의 TypeScript
    `packages/core` 에 동일한 공식이 구현되어 있다. 두 구현이 갈라지면
    같은 화면에 대해 디자인 단계와 현장 진단 단계가 다른 값을 낸다.
    임계값·계수를 바꿀 때는 반드시 양쪽을 함께 고친다.

⚠ 촬영물 특유의 함정
    디자인 툴의 색은 확정값이지만, 사진의 색은 화이트밸런스·감마·디스플레이 응답에
    흔들린다. 그래서 이 모듈은 항상 색 표본의 산포(dispersion)를 함께 돌려주고,
    산포가 크면 확신도를 깎는다. 무보정 원본(S1-04)을 쓰는 것이 전제다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

RGB = tuple[int, int, int]

# WCAG 2.x 상대 휘도 계수 — 변경 금지
_R_COEF, _G_COEF, _B_COEF = 0.2126, 0.7152, 0.0722
_SRGB_KNEE = 0.03928
_SRGB_SLOPE = 12.92
_SRGB_OFFSET = 0.055
_SRGB_GAMMA = 2.4
_CONTRAST_OFFSET = 0.05

# 별표5 임계값
THRESHOLD_NORMAL = 4.5   # 3.i
THRESHOLD_HIGH = 7.0     # 3.j

# 사진 판정에서 이 산포를 넘으면 확신도를 깎는다 (0~255 스케일의 채널 표준편차)
_DISPERSION_WARN = 12.0
_DISPERSION_REJECT = 28.0

# 사진에서 뽑은 대비는 **항상 실제보다 낮게** 나온다.
#   안티에일리어싱이 획을 배경 쪽으로 끌어당기고, JPEG 이 경계를 뭉갠다.
#   두 효과 모두 전경과 배경을 서로 가깝게 만들어 대비를 줄인다.
#   실측: 검정/흰색 -15.4%, #333/흰색 -23.6%, #767676/흰색 -16.1%,
#         #9AA3AB/흰색 -9.5%, 흰색/#1B579B -15.3%, #E0E0E0/#333 -14.8%
#   방향이 일정하다는 것이 중요하다 — 위반을 놓치는 게 아니라 과잉 신고한다.
#   다만 기준값 바로 아래에서는 오탐이 되므로, 그 구간을 '경계'로 표시해 검토자에게 넘긴다.
PHOTO_UNDERESTIMATE_MAX = 0.25


def hex_to_rgb(value: str) -> RGB:
    """'#1B4D8F' / '1b4d8f' / '#abc' 를 (r, g, b) 로."""
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"hex 색상 형식이 아닙니다: {value!r}")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"hex 색상 형식이 아닙니다: {value!r}") from exc


def rgb_to_hex(rgb: Sequence[int]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def _linearize(channel_8bit: float) -> float:
    s = channel_8bit / 255.0
    if s <= _SRGB_KNEE:
        return s / _SRGB_SLOPE
    return ((s + _SRGB_OFFSET) / (1.0 + _SRGB_OFFSET)) ** _SRGB_GAMMA


def relative_luminance(rgb: Sequence[int]) -> float:
    """WCAG 상대 휘도. 0.0(검정) ~ 1.0(흰색)."""
    r, g, b = (_linearize(c) for c in rgb)
    return _R_COEF * r + _G_COEF * g + _B_COEF * b


def contrast_ratio(fg: Sequence[int] | str, bg: Sequence[int] | str) -> float:
    """명도 대비. 1.0 ~ 21.0."""
    f = hex_to_rgb(fg) if isinstance(fg, str) else fg
    b = hex_to_rgb(bg) if isinstance(bg, str) else bg
    l1, l2 = relative_luminance(f), relative_luminance(b)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + _CONTRAST_OFFSET) / (lo + _CONTRAST_OFFSET)


@dataclass
class ContrastResult:
    """대비 측정 1건."""

    ratio: float
    required: float
    passed: bool
    fg: str
    bg: str
    dispersion: float | None = None   # 표본 색 산포 (사진 판정에서만)
    confidence: float = 1.0
    reject: bool = False              # True 면 판정하지 않고 재촬영을 요구한다
    borderline: bool = False          # 사진 표본 편향을 감안하면 기준을 넘을 수도 있는 구간

    def as_measured(self) -> dict[str, object]:
        """Evidence.measured 에 그대로 들어갈 dict."""
        out: dict[str, object] = {
            "명도대비": round(self.ratio, 2),
            "기준": self.required,
            "전경색": self.fg,
            "배경색": self.bg,
            "계산식": "WCAG relative luminance (L1+0.05)/(L2+0.05)",
        }
        if self.dispersion is not None:
            out["색표본 산포"] = round(self.dispersion, 1)
        if self.borderline:
            out["경계"] = (
                f"사진 표본은 실제보다 최대 {PHOTO_UNDERESTIMATE_MAX:.0%} 낮게 나온다. "
                f"이 값은 편향을 감안하면 기준을 넘을 수 있어 확정하지 않는다."
            )
        return out


def evaluate(
    fg: Sequence[int] | str,
    bg: Sequence[int] | str,
    *,
    required: float = THRESHOLD_NORMAL,
    dispersion: float | None = None,
) -> ContrastResult:
    """대비를 계산하고 별표5 임계값과 비교한다.

    Args:
        dispersion: 사진에서 색을 뽑은 경우의 표본 산포. 지정하면 확신도가 조정된다.
                    디자인 파일 등 확정 색이면 None 을 넘긴다(확신도 1.0).
    """
    f = hex_to_rgb(fg) if isinstance(fg, str) else tuple(fg)
    b = hex_to_rgb(bg) if isinstance(bg, str) else tuple(bg)
    ratio = contrast_ratio(f, b)

    confidence, reject = 1.0, False
    if dispersion is not None:
        if dispersion >= _DISPERSION_REJECT:
            confidence, reject = 0.0, True
        elif dispersion >= _DISPERSION_WARN:
            # 산포가 클수록 선형으로 깎는다. 경계선 근처 판정을 검토자에게 넘기기 위함.
            span = _DISPERSION_REJECT - _DISPERSION_WARN
            confidence = round(1.0 - 0.35 * (dispersion - _DISPERSION_WARN) / span, 3)

    passed = ratio >= required
    # 사진 표본이고, 미달이지만 편향을 되돌리면 기준을 넘을 수 있는 구간인가
    borderline = (
        dispersion is not None and not passed
        and ratio / (1.0 - PHOTO_UNDERESTIMATE_MAX) >= required
    )
    if borderline:
        # 확정하지 않는다 — 확신도를 기준선 아래로 낮춰 검토자 큐로 보낸다
        confidence = min(confidence, 0.6)

    return ContrastResult(
        ratio=ratio,
        required=required,
        passed=passed,
        fg=rgb_to_hex(f),
        bg=rgb_to_hex(b),
        dispersion=dispersion,
        confidence=confidence,
        reject=reject,
        borderline=borderline,
    )


def sample_dispersion(samples: Iterable[Sequence[int]]) -> float:
    """색 표본들의 채널 표준편차 최댓값.

    글자 픽셀을 여러 점 뽑아 넣으면, 안티에일리어싱·JPEG 블록·조명 얼룩 때문에
    색이 얼마나 흔들리는지 나온다. 이 값이 곧 사진 판정의 불확실성이다.
    """
    pts = [tuple(s) for s in samples]
    if len(pts) < 2:
        return 0.0
    worst = 0.0
    for ch in range(3):
        vals = [p[ch] for p in pts]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        worst = max(worst, var ** 0.5)
    return worst


def sample_fg_bg(patch_bgr, *, min_fg_ratio: float = 0.01):
    """글자 영역 이미지 조각에서 전경색·배경색을 갈라낸다.

    왜 백분위로는 안 되는가 — 화면 문구는 얇다. 글자 획이 조각 전체에서
    차지하는 비율이 수 %에 불과해, '어두운 쪽 12%' 같은 백분위를 쓰면
    표본이 통째로 배경(흰색)에 들어간다. 실제로 이 실수를 하면
    대비 2.56:1 인 화면이 1.03:1 로 측정된다 — 값은 틀렸는데 판정은
    우연히 맞아 버려서 알아채기도 어렵다.

    그래서 **Otsu 이진화**로 두 무리를 가른다. 밝기 히스토그램의 두 봉우리를
    나누는 임계값을 찾는 방식이라, 글자가 아무리 얇아도 획과 배경이 갈린다.

    Args:
        patch_bgr: 글자가 포함된 BGR 이미지 조각 (OpenCV)
        min_fg_ratio: 전경으로 인정할 최소 픽셀 비율. 이보다 적으면 글자가
                      아니라 노이즈로 보고 None 을 돌려준다.

    Returns:
        (fg_rgb, bg_rgb, fg_dispersion) 또는 None
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError("표본 추출에는 opencv-python 이 필요합니다") from exc

    if patch_bgr is None or patch_bgr.size == 0:
        return None

    gray = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    dark = patch_bgr[mask == 0]     # 임계값 아래 = 어두운 무리
    light = patch_bgr[mask == 255]
    total = gray.size
    if len(dark) == 0 or len(light) == 0:
        return None

    # 글자는 소수여야 한다. 어느 쪽이 소수인지로 전경을 정한다.
    fg_px, bg_px = (dark, light) if len(dark) <= len(light) else (light, dark)
    if len(fg_px) / total < min_fg_ratio:
        return None

    fg = tuple(int(round(v)) for v in fg_px.mean(axis=0)[::-1])   # BGR → RGB
    bg = tuple(int(round(v)) for v in bg_px.mean(axis=0)[::-1])

    sample = fg_px[:: max(1, len(fg_px) // 300)]
    dispersion = sample_dispersion([tuple(int(v) for v in p[::-1]) for p in sample])
    return fg, bg, dispersion


def nearest_passing_color(
    fg: Sequence[int] | str,
    bg: Sequence[int] | str,
    *,
    target: float = THRESHOLD_NORMAL,
    steps: int = 40,
) -> str:
    """'가장 가까운 합격 색' — 개선 처방에 붙는 구체적 수정값.

    색상·채도 인상을 최대한 유지하기 위해 RGB 를 흑/백 방향으로만 선형 보간하며
    이분 탐색한다. 형제 프로젝트의 LCH 기반 구현보다 단순하지만, 현장 리포트가
    필요로 하는 것은 '이 색으로 바꾸면 통과한다'는 한 개의 제안값이므로 충분하다.

    Returns:
        hex 색상. 목표 대비를 만족하는 가장 적게 변경된 색.
    """
    f = list(hex_to_rgb(fg) if isinstance(fg, str) else fg)
    b = hex_to_rgb(bg) if isinstance(bg, str) else tuple(bg)

    if contrast_ratio(f, b) >= target:
        return rgb_to_hex(f)

    # 배경이 밝으면 전경을 어둡게, 어두우면 밝게.
    anchor: RGB = (0, 0, 0) if relative_luminance(b) > 0.5 else (255, 255, 255)

    lo, hi = 0.0, 1.0
    best = list(anchor)
    for _ in range(steps):
        mid = (lo + hi) / 2
        cand = [f[i] + (anchor[i] - f[i]) * mid for i in range(3)]
        if contrast_ratio(cand, b) >= target:
            best, hi = cand, mid      # 더 적게 움직여도 되는지 탐색
        else:
            lo = mid

    result = rgb_to_hex(best)
    # 자기검증 — 실패하면 앵커색으로 폴백한다. 통과하지 못하는 제안은 내보내지 않는다.
    if contrast_ratio(hex_to_rgb(result), b) < target:
        return rgb_to_hex(anchor)
    return result
