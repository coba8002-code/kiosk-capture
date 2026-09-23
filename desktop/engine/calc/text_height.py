"""문자 높이 — 별표5 3.g (7.25mm 이상).

이 항목은 겉보기와 달리 까다롭다. OCR 이 돌려주는 바운딩 박스는 '글자 상자'이지
'문자 높이'가 아니기 때문이다.

    · 라틴 문자: 박스 높이에는 어센더·디센더 여백이 포함된다. 'x' 만 있는 단어와
      'Hjq' 가 섞인 단어의 박스 높이가 다르게 나온다.
    · 한글: 네모틀 안에 꽉 차므로 라틴보다 박스 대비 실제 자소 높이가 크다.
    · 별표5는 '문자 높이'의 정의를 명시하지 않았고, KS 는 시력검사 란돌트 고리
      직경을 토대로 산출했다고만 밝힌다.

그래서 이 모듈은 **문자 종류별 보정 계수**를 명시적으로 둔다.
계수는 두 성분의 곱이고, 둘의 교정 상태가 다르므로 따로 관리한다.

    CAP_RATIO       폰트 메트릭 성분 — 잉크 실제 높이 ÷ 타이포그래픽 행 높이
                    tools/calibrate_text_height.py 로 **실측 교정 완료**(2026-08-22)
    OCR_BOX_FACTOR  OCR 엔진 상자 규약 성분 — 엔진마다 상자 기준이 달라
                    **아직 미교정**. 엔진 확정 후 현장 표본으로 교정한다.

'문자 높이'의 정의 — KS §5.2.3 비고 3 이 근거를 밝힌다.
    "문자 높이 기준은 시력 검사에 사용되는 란돌트 고리(Landolt ring)의 직경을
     토대로 ... 계산한 것이다."
란돌트 고리의 직경은 고리 자체의 크기다. 따라서 여기서 말하는 문자 높이는
**글자 잉크의 실제 높이**이지 여백을 포함한 상자 높이가 아니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .scale import PlaneScale

Point = tuple[float, float]

MIN_CHAR_HEIGHT_MM = 7.25

# ── 폰트 메트릭 성분 — 실측 교정 완료 ─────────────────────────────────────
#   교정: tools/calibrate_text_height.py, malgun.ttf(맑은 고딕), 200px 렌더, 2026-08-22
#   값은 자종별 **최솟값**을 채택했다. 3.g 는 ai_verdict_scope: fail_only 이므로
#   AI 가 적합을 확정하지 않는다. 계수가 크면 문자 높이를 과대평가해 위반을 놓치고
#   (false pass), 작으면 과소평가해 검토자에게 넘긴다(false flag).
#   놓침이 훨씬 나쁘므로 작은 쪽으로 간다.
CAP_RATIO: dict[str, float] = {
    "hangul":      0.708,   # 측정 평균 0.711, 최대 0.715, 산포 0.008
    "latin_caps":  0.539,   # 측정 평균 0.551, 최대 0.554, 산포 0.015
    "latin_mixed": 0.577,   # 측정 평균 0.707, 최대 0.760, 산포 0.184
    "digits":      0.554,   # 측정 평균 0.599, 최대 0.644, 산포 0.090
    "mixed":       0.682,   # 측정 평균 0.712, 최대 0.738, 산포 0.056
}

# 자종별 측정 산포 — 확신도를 깎는 데 쓴다. 산포가 큰 자종은 계수 하나로 대표되지 않는다.
CAP_RATIO_SPREAD: dict[str, float] = {
    "hangul": 0.008, "latin_caps": 0.015, "latin_mixed": 0.184,
    "digits": 0.090, "mixed": 0.056,
}

CALIBRATION = {
    "font": "malgun.ttf (맑은 고딕)",
    "render_px": 200,
    "date": "2026-08-22",
    "method": "잉크 바운딩 박스 높이 ÷ 타이포그래픽 행 높이(ascent+descent)",
    "policy": "자종별 최솟값 채택 (fail_only 항목 — 놓침 방지)",
}
METRIC_CALIBRATED = True

# ── OCR 엔진 상자 규약 성분 — 아직 미교정 ────────────────────────────────
#   엔진마다 상자 기준이 다르다(행 높이 기준 / 잉크 기준 / 그 중간).
#   엔진을 확정하고 현장 표본으로 교정할 때까지 1.0 을 두되,
#   확신도에 상한을 걸어 자동으로 검토자 큐로 보낸다.
OCR_BOX_FACTOR = 1.0
OCR_BOX_CALIBRATED = False
OCR_UNCALIBRATED_CONFIDENCE = 0.85

#: 엔진별 교정 결과가 쌓이는 파일. tools/calibrate_ocr.py 가 쓰고 여기서 읽는다.
#: 소스 상수를 고쳐 쓰지 않는 이유는, 실행 파일 안의 .py 는 읽기 전용이기 때문이다.
OCR_CALIBRATION_FILE = "ocr-box-calibration.json"


def load_ocr_calibration(engine: str) -> tuple[float, bool, dict]:
    """엔진 이름으로 교정된 상자 계수를 찾는다.

    Returns:
        (계수, 교정되었는가, 교정 메타). 교정 기록이 없으면 (1.0, False, {}).
    """
    if not engine:
        return OCR_BOX_FACTOR, False, {}
    import json
    from ..paths import output_dir, resource_dir
    # 설치 자리에서 새로 교정한 값을 먼저 쓰고, 없으면 배포본에 검증해 넣은
    # 기본 교정을 쓴다. 실행파일도 첫 실행부터 한글 OCR을 사용할 수 있게 한다.
    candidates = [output_dir() / OCR_CALIBRATION_FILE,
                  resource_dir() / "rules" / OCR_CALIBRATION_FILE]
    data = None
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError):
            continue
    if data is None:
        return OCR_BOX_FACTOR, False, {}
    rec = (data.get("engines") or {}).get(engine)
    if not rec or "factor" not in rec:
        return OCR_BOX_FACTOR, False, {}
    try:
        return float(rec["factor"]), True, rec
    except (TypeError, ValueError):
        return OCR_BOX_FACTOR, False, {}

_HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
_LATIN = re.compile(r"[A-Za-z]")
_LATIN_LOWER_TALL = re.compile(r"[bdfhkltgjpqy]")
_DIGIT = re.compile(r"[0-9]")


def classify_script(text: str) -> str:
    """문자열의 자종을 분류해 보정 계수 키를 돌려준다."""
    has_hangul = bool(_HANGUL.search(text))
    has_latin = bool(_LATIN.search(text))
    has_digit = bool(_DIGIT.search(text))

    if has_hangul and has_latin:
        return "mixed"
    if has_hangul:
        return "hangul"
    if has_latin:
        if _LATIN_LOWER_TALL.search(text) or text != text.upper():
            return "latin_mixed"
        return "latin_caps"
    if has_digit:
        return "digits"
    return "mixed"


@dataclass
class TextHeightResult:
    """문자 하나(또는 한 줄)의 높이 판정."""

    text: str
    script: str
    box_height_mm: float
    char_height_mm: float
    required_mm: float
    passed: bool
    confidence: float
    metric_calibrated: bool
    ocr_calibrated: bool
    is_caption: bool = False
    box_factor: float = 1.0

    def as_measured(self) -> dict[str, object]:
        return {
            "문구": self.text[:30],
            "자종": self.script,
            "박스 높이": round(self.box_height_mm, 2),
            "추정 문자 높이": round(self.char_height_mm, 2),
            "기준": f"{self.required_mm}mm 이상",
            "보정 계수": round(CAP_RATIO[self.script] * self.box_factor, 4),
            "계수 교정": "폰트 메트릭 완료 · OCR 상자 규약 미교정"
                        if self.metric_calibrated and not self.ocr_calibrated
                        else ("완료" if self.ocr_calibrated else "미교정"),
        }


def measure_line(
    text: str,
    box_px: Sequence[Point],
    scale: PlaneScale,
    *,
    is_caption: bool = False,
    box_factor: float | None = None,
    box_calibrated: bool | None = None,
) -> TextHeightResult:
    """OCR 한 줄의 문자 높이를 mm 로 추정한다.

    Args:
        text: OCR 인식 문자열 (자종 판별에 쓴다)
        box_px: 해당 줄의 바운딩 박스 4점 (이미지 좌표)
        scale: 화면 평면(display)의 PlaneScale
        is_caption: 비디오 캡션이면 True. 별표5 3.g 적용 대상에서 제외된다.
        box_factor: 이 엔진의 상자 계수. None 이면 미교정 기본값(1.0).
        box_calibrated: 그 계수가 실측 교정된 것인가. 확신도 상한이 여기 달려 있다.
    """
    pts = [scale.to_mm(p) for p in box_px]
    ys = [p[1] for p in pts]
    box_h = max(ys) - min(ys)

    factor = OCR_BOX_FACTOR if box_factor is None else float(box_factor)
    calibrated = OCR_BOX_CALIBRATED if box_calibrated is None else bool(box_calibrated)

    script = classify_script(text)
    ratio = CAP_RATIO[script] * factor
    char_h = box_h * ratio

    # 확신도 = 마커 확신도 × 폰트 메트릭 신뢰도 × OCR 상자 규약 신뢰도.
    #   폰트 메트릭: 교정 완료. 다만 자종별 측정 산포가 크면 계수 하나로 대표되지 않으므로 깎는다.
    #   OCR 상자 규약: 미교정. 전역 상한을 걸어 자동으로 검토자 큐에 들어가게 한다.
    spread = CAP_RATIO_SPREAD.get(script, 0.2)
    metric_confidence = 1.0 - min(0.4, spread) if METRIC_CALIBRATED else 0.7
    ocr_confidence = 1.0 if calibrated else OCR_UNCALIBRATED_CONFIDENCE
    confidence = round(scale.confidence * metric_confidence * ocr_confidence, 3)

    return TextHeightResult(
        text=text,
        script=script,
        box_height_mm=box_h,
        char_height_mm=char_h,
        required_mm=MIN_CHAR_HEIGHT_MM,
        passed=char_h >= MIN_CHAR_HEIGHT_MM,
        confidence=confidence,
        metric_calibrated=METRIC_CALIBRATED,
        ocr_calibrated=calibrated,
        is_caption=is_caption,
        box_factor=factor,
    )


@dataclass
class ScreenTextResult:
    """화면 한 장의 3.g 종합."""

    lines: list[TextHeightResult]
    violations: list[TextHeightResult]
    borderline: list[TextHeightResult]     # 허용오차 안 — 적합이라 말하지 않는다
    smallest_mm: float | None
    passed: bool
    confidence: float
    tolerance_mm: float = 0.0

    def as_measured(self) -> dict[str, object]:
        return {
            "검사 문구 수": len(self.lines),
            "기준 미달 수": len(self.violations),
            "최소 문자 높이": None if self.smallest_mm is None else round(self.smallest_mm, 2),
            "기준": f"{MIN_CHAR_HEIGHT_MM}mm 이상 (비디오 캡션 제외)",
            "미달 문구": [v.text[:20] for v in self.violations[:10]],
            **({"경계 근접": len(self.borderline),
                "경계 근접 문구": [b.text[:20] for b in self.borderline[:10]],
                "경계 근접 처리": (
                    f"계측 허용오차 ±{self.tolerance_mm:.2f}mm 안에 걸쳐 있어 "
                    "적합이라 말하지 않습니다. 실측으로 확정하세요.")}
               if self.borderline else {}),
            "계수 교정": {
                "폰트 메트릭": METRIC_CALIBRATED,
                "OCR 상자 규약": all(r.ocr_calibrated for r in self.lines) if self.lines
                                 else OCR_BOX_CALIBRATED,
                "교정 근거": CALIBRATION,
            },
        }


def evaluate_screen(results: Sequence[TextHeightResult], *,
                    tolerance_ratio: float = 0.0) -> ScreenTextResult:
    """화면 한 장의 모든 줄을 모아 3.g 를 판정한다. 캡션은 제외한다.

    Args:
        tolerance_ratio: 계측 잔차(교정 산포). 기준선을 이만큼 넘겨야 적합으로 본다.

    왜 허용오차가 필요한가
        같은 화면을 다시 찍으면 문자 높이가 −9%~+3% 범위로 흔들린다(실측).
        **과대평가 쪽이 위험하다** — 6.6mm 짜리 글자가 7.3mm 로 읽히면
        7.25mm 기준을 통과해 위반을 놓친다.
        그래서 기준선 언저리는 적합이라 말하지 않고 실측으로 넘긴다.
    """
    target = [r for r in results if not r.is_caption]
    tol = MIN_CHAR_HEIGHT_MM * max(0.0, tolerance_ratio)

    violations, borderline = [], []
    for r in target:
        if r.char_height_mm < MIN_CHAR_HEIGHT_MM - tol:
            violations.append(r)
        elif r.char_height_mm < MIN_CHAR_HEIGHT_MM + tol:
            borderline.append(r)

    smallest = min((r.char_height_mm for r in target), default=None)
    confidence = min((r.confidence for r in target), default=0.0)
    return ScreenTextResult(
        lines=list(target),
        violations=violations,
        borderline=borderline,
        smallest_mm=smallest,
        passed=not violations,
        confidence=confidence,
        tolerance_mm=tol,
    )
