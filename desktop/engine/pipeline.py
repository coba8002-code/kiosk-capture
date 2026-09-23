"""수집 번들 → 판정 — 진단 PC 가 실제로 진단하는 곳.

지금까지 없던 '배선'이 이 파일이다.
수집 앱이 만든 폴더를 읽어, 사진마다 마커를 찾고, 계측하고, 판정을 낸다.

    번들 폴더  →  manifest 읽기  →  사진별 마커·평면 환산
              →  항목별 분석기  →  apply_ai_scope  →  Assessment 목록
              →  report.build   →  리포트 A·B

무엇이 자동으로 판정되고 무엇이 안 되는가 — 정직하게
────────────────────────────────────────────────────────────────────
    자동     3.i·3.j  명도 대비   (문자 영역 검출 + Otsu + WCAG 공식)
             1.b·1.c  작동부 간격·크기 (마커 평면 + 윤곽 검출)
             3.g      문자 높이   (OCR + 자종별 보정 — 엔진이 설치된 경우)
             8.a      깜박임      (영상 휘도 시계열)
    입력     점주 응답 4항목, 실측값 7항목 — manifest 에 있으면 그대로 기록
    L2       judge 23항목 — 공급자를 켠 경우에만. 모델의 '적합'은 적합으로 치지 않는다
    미판정   나머지 — 실측이 붙기 전까지는 담당자에게 넘긴다

    자동 판정 항목이 적다는 사실을 숨기지 않는다. 리포트에 그대로 인쇄된다.

커버리지의 비대칭
    문자 영역 검출은 **화면의 모든 문구를 빠짐없이 찾는다고 보장하지 못한다.**
    그래서 위반을 찾으면 확실하지만, 못 찾았다고 적합인 것은 아니다.
    위반 없음일 때 확신도를 기준선 아래로 눌러 검토자에게 넘기는 이유다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .calc import contrast, flash_rate, geometry, scale, text_height
from .detect import controls, text_regions
from .rules import RuleSet
from .verdict import (
    Assessment, Evidence, Grade, Verdict, apply_ai_scope, confirm, record_external,
)

# 파이프라인이 실제로 읽는 촬영 세트 — 도구가 정규식으로 추측하지 않도록 선언한다.
# 여기 없는 세트는 '찍기는 하는데 아무도 안 읽는' 세트다. 그 사실을 숨기지 않는다.
# tests/test_engine.py 가 이 선언과 실제 동작이 어긋나지 않는지 검사한다.
CONSUMED_SETS = {
    "S1": "명도 대비(3.i·3.j) · 작동부 윤곽(1.b·1.c, 화면 평면)",
    "S2": "깜박임(8.a)",
    "S3": "작동부 윤곽(1.b·1.c, 조작부 평면)",
}

# L2 공급자를 켠 경우에만 읽는 세트. judge 항목의 evidence 를 따라간다.
# 꺼져 있으면 이 세트들은 '찍기는 하는데 아무도 안 읽는' 상태이고, 그렇게 보고한다.
L2_CONSUMED_SETS = {
    "S5": "음성 안내 판단(1.g·3.a·3.c·5.a·5.b·7.b·10.b) — 공급자를 켠 경우",
}

# 이 파이프라인이 계측만으로 판정을 만드는 항목.
# tools/gaps.py 가 이 선언을 읽는다 — 소스 텍스트를 정규식으로 훑으면
# 실행 파일 안에서는 .py 가 없어 전부 '미개발'로 보고된다.
# tests/test_engine.py 가 선언과 실제 동작이 어긋나지 않는지 검사한다.
AUTO_RULES = {
    "3.g": "문자 높이 (OCR 엔진이 있을 때만)",
    "3.i": "명도 대비 (일반)",
    "3.j": "명도 대비 (고대비 모드)",
    "1.b": "작동부 간격",
    "1.c": "작동부 최소경계상자",
    "8.a": "깜박임 주파수",
}

SCHEMA = "kfa.capture/1"
REVIEW_SCHEMA = "kfa.review/1"

# 검토 결정은 번들 옆 review.json 에 남는다.
# 번들과 함께 다니므로 같은 번들을 다시 진단해도 같은 결과가 나온다 — 재현 가능하다.
REVIEW_FILE = "review.json"

# 문자 영역 검출이 전수를 보장하지 못하므로, '위반 없음' 결론의 확신도 상한.
# 기본 confidence_floor(0.85) 아래라 자동으로 검토자 큐로 간다.
NO_VIOLATION_CONFIDENCE = 0.55

OWNER_ANSWER_MAP = {
    "yes": Verdict.PASS,
    "no": Verdict.FAIL,
    "na": Verdict.NOT_APPLICABLE,
}

# 리포트에 'no' 가 찍히면 읽는 사람이 무슨 뜻인지 모른다
OWNER_ANSWER_LABEL = {"yes": "예", "no": "아니오", "na": "해당 없음"}


class OcrEngineError(RuntimeError):
    """OCR 엔진이 실행 중에 깨졌다. 판정을 만들지 않고 이유를 남긴다."""


class BundleError(ValueError):
    """번들이 규격을 지키지 않았다. 진단을 시작하지 않는다."""


@dataclass
class Shot:
    set_id: str
    shot_id: str
    path: Path
    marker_plane: str | None = None
    masked: bool = False
    note: str = ""


@dataclass
class CaptureBundle:
    root: Path
    device_id: str
    location: str
    product_type: str
    scopes: set[str]
    exemptions: set[str]
    captured_at: str
    captured_by: str
    shots: list[Shot] = field(default_factory=list)
    owner_answers: dict[str, str] = field(default_factory=dict)
    measurements: dict[str, dict] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def review_path(self) -> Path:
        return self.root / REVIEW_FILE

    def save_review(self) -> Path:
        payload = {"schema": REVIEW_SCHEMA, **self.review}
        self.review_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.review_path

    def by_set(self, set_id: str) -> list[Shot]:
        return [s for s in self.shots if s.set_id == set_id]

    def by_shot(self, shot_id: str) -> list[Shot]:
        return [s for s in self.shots if s.shot_id == shot_id]


def _list_field(obj: dict, key: str, *, where: str = "manifest") -> None:
    """비어 있는 건 괜찮지만, 있으면 리스트여야 한다."""
    v = obj.get(key)
    if v is not None and not isinstance(v, list):
        raise BundleError(
            f"{where}.{key} 는 배열이어야 합니다 (지금은 {type(v).__name__})")


def _dict_field(obj: dict, key: str, *, where: str = "manifest") -> None:
    """비어 있는 건 괜찮지만, 있으면 객체여야 한다."""
    v = obj.get(key)
    if v is not None and not isinstance(v, dict):
        raise BundleError(
            f"{where}.{key} 는 객체여야 합니다 (지금은 {type(v).__name__}). "
            f'형식: {{"3.b": "no", ...}}')


def load_bundle(path: str | Path) -> CaptureBundle:
    """수집 앱이 만든 폴더(또는 manifest.json)를 읽는다."""
    p = Path(path)
    manifest_path = p / "manifest.json" if p.is_dir() else p
    if not manifest_path.exists():
        raise BundleError(f"manifest.json 이 없습니다: {manifest_path}")

    root = manifest_path.parent
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleError(f"manifest.json 을 읽을 수 없습니다: {exc}") from exc

    # 타입 확인이 먼저다. 배열이 들어오면 .get 부터 AttributeError 로 죽는다.
    if not isinstance(m, dict):
        raise BundleError(
            f"manifest.json 의 최상위는 객체여야 합니다 (지금은 {type(m).__name__})")

    if m.get("schema") != SCHEMA:
        raise BundleError(f"지원하지 않는 형식입니다: {m.get('schema')!r} (필요: {SCHEMA})")

    dev = m.get("device") or {}
    if not isinstance(dev, dict):
        raise BundleError("device 는 객체여야 합니다")
    if not dev.get("id"):
        raise BundleError("device.id 가 없습니다")
    if dev.get("product_type") not in ("중대형", "소형"):
        raise BundleError("device.product_type 은 '중대형' 또는 '소형' 이어야 합니다")

    # 형식이 어긋나면 여기서 잡는다.
    # 예전에는 shots 가 null 이면 TypeError 로 죽어 사용자가 무엇이 잘못됐는지
    # 알 수 없었다. 수집 앱이 만든 파일이라도 무결하다고 가정하지 않는다.
    _list_field(m, "shots")
    _dict_field(m, "owner_answers")
    _dict_field(m, "measurements")
    _list_field(dev, "scopes", where="device")
    _list_field(dev, "exemptions", where="device")

    shots: list[Shot] = []
    missing: list[str] = []
    for i, s in enumerate(m.get("shots") or []):
        if not isinstance(s, dict) or not s.get("file"):
            raise BundleError(f"shots[{i}] 에 file 이 없습니다")
        if not isinstance(s["file"], str):
            raise BundleError(f"shots[{i}].file 은 문자열이어야 합니다")
        f = (root / s["file"]).resolve()
        if not f.is_relative_to(root.resolve()):
            raise BundleError(f"shots[{i}].file 은 번들 안의 파일이어야 합니다")
        if f.exists() and not f.is_file():
            raise BundleError(f"shots[{i}].file 은 일반 파일이어야 합니다")
        if not f.exists():
            missing.append(s["file"])
            continue
        shots.append(Shot(
            set_id=s.get("set", ""), shot_id=s.get("shot", ""), path=f,
            marker_plane=s.get("marker_plane"), masked=bool(s.get("masked")),
            note=s.get("note", ""),
        ))
    if missing:
        raise BundleError(
            f"manifest 에 적힌 파일 {len(missing)}개가 없습니다: {', '.join(missing[:5])}"
        )

    review: dict[str, Any] = {}
    rp = root / REVIEW_FILE
    if rp.exists():
        try:
            raw_review = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BundleError(f"{REVIEW_FILE} 을 읽을 수 없습니다: {exc}") from exc
        if not isinstance(raw_review, dict):
            raise BundleError(f"{REVIEW_FILE} 의 최상위는 객체여야 합니다")
        if raw_review.get("schema") not in (REVIEW_SCHEMA, None):
            raise BundleError(f"{REVIEW_FILE} 형식이 다릅니다: {raw_review.get('schema')!r}")
        for k in ("decisions", "grades", "owner_answers", "measurements"):
            _dict_field(raw_review, k, where=REVIEW_FILE)
        _list_field(raw_review, "exemptions", where=REVIEW_FILE)
        review = {k: v for k, v in raw_review.items() if k != "schema"}

    return CaptureBundle(
        root=root,
        device_id=dev["id"],
        location=dev.get("location", ""),
        product_type=dev["product_type"],
        scopes=set(dev.get("scopes") or []),
        exemptions=set(dev.get("exemptions") or []) | set(review.get("exemptions") or []),
        captured_at=m.get("captured_at", ""),
        captured_by=m.get("captured_by", ""),
        shots=shots,
        owner_answers={**(m.get("owner_answers") or {}),
                       **(review.get("owner_answers") or {})},
        measurements={**(m.get("measurements") or {}),
                      **(review.get("measurements") or {})},
        review=review,
        raw=m,
    )


# =============================================================================
# 사진 한 장 분석
# =============================================================================

def _imread(path: Path):
    """한글 경로에서도 읽히도록 바이트로 읽어 디코드한다."""
    import cv2
    import numpy as np
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


@dataclass
class PhotoAnalysis:
    shot: Shot
    ok: bool
    scale: scale.PlaneScale | None = None
    marker_note: str = ""
    text_blocks: int = 0
    text_note: str = ""                    # 문구를 못 찾았으면 그 이유
    worst_contrast: contrast.ContrastResult | None = None
    contrast_samples: list[dict] = field(default_factory=list)
    controls: controls.DetectionResult | None = None
    image: object | None = None            # OCR 이 다시 디코드하지 않도록 들고 있는다
    exclude: list = field(default_factory=list)   # 마커 카드 영역 — 판정에서 빼야 한다


def analyze_photo(shot: Shot, *, want_contrast: bool = True,
                  want_controls: bool = False) -> PhotoAnalysis:
    """사진 한 장에서 마커 환산·대비 표본·작동부 후보를 뽑는다."""
    img = _imread(shot.path)
    if img is None:
        return PhotoAnalysis(shot, ok=False, marker_note="이미지를 읽을 수 없습니다")

    res = PhotoAnalysis(shot, ok=True)
    exclude: list[tuple[int, int, int, int]] = []

    # ── 마커 ────────────────────────────────────────────────────────────
    if shot.marker_plane:
        found = scale.detect_marker_corners(img)
        if not found:
            res.marker_note = "마커 미검출 — 치수 계측 불가"
        else:
            corners, _ = found
            exclude.append(text_regions.marker_exclusion(corners))
            try:
                res.scale = scale.build_plane_scale(
                    corners, plane=shot.marker_plane, frame_width_px=img.shape[1]
                )
                res.marker_note = (
                    f"{res.scale.px_per_mm:.2f}px/mm · 왜곡 {res.scale.skew:.2f}"
                )
            except scale.MarkerError as exc:
                res.marker_note = str(exc)

    # ── 문자 영역 + 대비 ────────────────────────────────────────────────
    text_boxes: list[tuple[int, int, int, int]] = []
    if want_contrast:
        det = text_regions.detect(img, exclude=exclude)
        res.text_blocks = len(det)
        text_boxes = [(b.x, b.y, b.w, b.h) for b in det.blocks]
        if not det.reliable:
            # 잡음이 심해 검출을 포기한 사진. 조용히 '문구 없음'으로 넘기면
            # 리포트에는 '위반 없음'처럼 보인다. 그 사실을 들고 간다.
            res.text_note = det.coverage_note
        for block in det.blocks:
            sampled = contrast.sample_fg_bg(text_regions.crop(img, block))
            if sampled is None:
                continue
            fg, bg, disp = sampled
            c = contrast.evaluate(fg, bg, required=contrast.THRESHOLD_NORMAL,
                                  dispersion=disp)
            if c.reject:
                continue
            res.contrast_samples.append({
                "영역": f"{block.w}x{block.h}@({block.x},{block.y})",
                "글자 수": block.glyphs,
                "대비": round(c.ratio, 2),
                "전경": c.fg, "배경": c.bg,
            })
            if res.worst_contrast is None or c.ratio < res.worst_contrast.ratio:
                res.worst_contrast = c

    res.image = img
    res.exclude = list(exclude)

    # ── 작동부 윤곽 ─────────────────────────────────────────────────────
    # 마커 평면이 잡혔을 때만 한다. 픽셀 치수를 mm 로 둔갑시키지 않기 위해서다.
    if want_controls and res.scale is not None:
        res.controls = controls.detect(img, res.scale, exclude=exclude,
                                       text_boxes=text_boxes or None)

    return res


# =============================================================================
# 항목별 분석기
# =============================================================================

def _contrast_rule(rs: RuleSet, bundle: CaptureBundle, rule_id: str,
                   required: float, photos: list[PhotoAnalysis]) -> Assessment | None:
    """3.i / 3.j — 검출된 문구 블록 중 최악을 기준과 비교한다."""
    rule = rs[rule_id]
    samples = [p for p in photos if p.worst_contrast is not None]
    if not samples:
        return None

    worst_photo = min(samples, key=lambda p: p.worst_contrast.ratio)
    base = worst_photo.worst_contrast
    c = contrast.evaluate(
        contrast.hex_to_rgb(base.fg), contrast.hex_to_rgb(base.bg),
        required=required, dispersion=base.dispersion,
    )

    violations = sum(
        1 for p in samples for s in p.contrast_samples if s["대비"] < required
    )
    total_blocks = sum(len(p.contrast_samples) for p in samples)

    measured = {
        **c.as_measured(),
        "검사 화면 수": len(samples),
        "검사 문구 블록": total_blocks,
        "기준 미달 블록": violations,
        "최악 화면": worst_photo.shot.path.name,
        "커버리지": (
            "문자 영역 자동 검출 — 화면의 모든 문구를 빠짐없이 찾는다는 보장은 없습니다"
        ),
    }
    if not c.passed:
        measured["권장 색"] = contrast.nearest_passing_color(c.fg, c.bg, target=required)

    confidence = c.confidence
    if c.passed:
        # 위반을 못 찾았다고 적합인 것은 아니다 — 커버리지가 보장되지 않는다
        confidence = min(confidence, NO_VIOLATION_CONFIDENCE)
        measured["주의"] = "위반이 검출되지 않았을 뿐, 전수 확인은 아닙니다"

    return apply_ai_scope(
        rule, Verdict.PASS if c.passed else Verdict.FAIL,
        evidence=Evidence(
            crop_ref=str(worst_photo.shot.path.relative_to(bundle.root)),
            measured=measured, criterion_id=rule_id, confidence=confidence,
        ),
    )

# 마커 평면 → 1.b 가 요구하는 표면 종류.
# 화면 위의 버튼은 터치(2.5mm), 기기 몸체의 버튼은 물리(0.5mm) 기준을 받는다.
PLANE_SURFACE = {"display": "touch", "control_panel": "physical"}

# 윤곽 계측의 확신도.
# 치수 자체는 정확하다 — 합성 촬영에서 기울기 0.25 까지 오차 0.3mm 였다.
# 남는 의심은 '이게 정말 작동부인가'이고, 그 판단은 근거 사진을 보는 검토자 몫이다.
# 그래서 여유가 허용오차의 3배를 넘으면 확신도 게이트(0.85)를 통과시켜
# '위반 의심'으로 올리고, 그 안쪽이면 게이트에 걸려 실측으로 넘어가게 둔다.
CONTROL_MARGIN_SIGMA = 3.0
CONTROL_CONFIDENT = 0.88
CONTROL_BORDERLINE = 0.60


def _margin_confidence(margin_mm: float, tolerance_mm: float) -> float:
    """기준선까지의 여유가 계측 허용오차에 비해 충분한가."""
    if margin_mm >= CONTROL_MARGIN_SIGMA * max(tolerance_mm, 1e-6):
        return CONTROL_CONFIDENT
    return CONTROL_BORDERLINE


def _controls_photos(photos: list[PhotoAnalysis]) -> list[PhotoAnalysis]:
    return [p for p in photos
            if p.controls is not None and p.controls.candidates and p.scale is not None]


def _gap_rule(rs: RuleSet, bundle: CaptureBundle,
              photos: list[PhotoAnalysis]) -> Assessment | None:
    """1.b — 이웃한 작동부 사이 간격.

    사진마다 표면 종류가 다르므로 사진별로 기준을 달리 적용하고,
    한 장이라도 미달이 있으면 위반으로 올린다.
    """
    usable = _controls_photos(photos)
    if not usable:
        return None

    results: list[tuple[PhotoAnalysis, geometry.GapResult]] = []
    for ph in usable:
        surface = PLANE_SURFACE.get(ph.shot.marker_plane or "", "physical")
        boxes = ph.controls.boxes()
        if len(boxes) < 2:
            continue
        r = geometry.check_gaps(
            boxes, surface=surface,
            # 윤곽 계측의 잔차(경계 편의 교정 후)를 그대로 얹는다
            measurement_error_mm=ph.controls.tolerance_mm,
        )
        results.append((ph, r))

    if not results:
        return None

    failed = [(ph, r) for ph, r in results if not r.passed]
    worst_ph, worst = (failed[0] if failed
                       else min(results, key=lambda t: t[1].min_gap_mm
                                if t[1].min_gap_mm is not None else 9e9))

    measured = {
        **worst.as_measured(),
        "검사 사진": len(results),
        "미달 사진": len(failed),
        "대상 사진": worst_ph.shot.path.name,
        **worst_ph.controls.as_measured(),
    }
    passed = not failed
    if passed:
        confidence = NO_VIOLATION_CONFIDENCE
        measured["주의"] = "검출되지 않은 작동부가 있을 수 있어 전수 확인이 아닙니다"
    else:
        confidence = _margin_confidence(
            worst.required_mm - (worst.min_gap_mm or 0.0),
            worst_ph.controls.tolerance_mm)

    return apply_ai_scope(
        rs["1.b"], Verdict.PASS if passed else Verdict.FAIL,
        evidence=Evidence(
            crop_ref=str(worst_ph.shot.path.relative_to(bundle.root)),
            measured=measured, criterion_id="1.b", confidence=confidence,
        ),
    )


def _mbr_rule(rs: RuleSet, bundle: CaptureBundle,
              photos: list[PhotoAnalysis]) -> Assessment | None:
    """1.c — 작동부 최소경계상자. 법적 판정(144mm²)과 설계 권고(150mm²)를 함께 낸다."""
    usable = _controls_photos(photos)
    if not usable:
        return None

    results: list[tuple[PhotoAnalysis, geometry.MbrResult]] = []
    for ph in usable:
        results.append((ph, geometry.check_mbr(
            ph.controls.boxes(), tolerance_mm=ph.controls.tolerance_mm)))

    failed = [(ph, r) for ph, r in results if not r.passed]
    worst_ph, worst = (max(failed, key=lambda t: len(t[1].violations)) if failed
                       else max(results, key=lambda t: len(t[1].boxes)))

    measured = {
        **worst.as_measured(),
        "검사 사진": len(results),
        "미달 사진": len(failed),
        "대상 사진": worst_ph.shot.path.name,
        **worst_ph.controls.as_measured(),
    }
    passed = not failed
    if passed:
        confidence = NO_VIOLATION_CONFIDENCE
        measured["주의"] = "검출되지 않은 작동부가 있을 수 있어 전수 확인이 아닙니다"
    else:
        worst_side = min(v["최소 변"] for v in worst.violations)
        confidence = _margin_confidence(geometry.SIDE_MIN_MM - worst_side,
                                        worst_ph.controls.tolerance_mm)

    return apply_ai_scope(
        rs["1.c"], Verdict.PASS if passed else Verdict.FAIL,
        evidence=Evidence(
            crop_ref=str(worst_ph.shot.path.relative_to(bundle.root)),
            measured=measured, criterion_id="1.c", confidence=confidence,
        ),
    )

def _in_exclusion(box_px, regions) -> bool:
    """이 문구 상자가 제외 영역(마커 카드) 안에 있는가."""
    if not regions:
        return False
    xs = [p[0] for p in box_px]
    ys = [p[1] for p in box_px]
    bx, by = min(xs), min(ys)
    bw, bh = max(xs) - bx, max(ys) - by
    if bw <= 0 or bh <= 0:
        return False
    for rx, ry, rw, rh in regions:
        x0, y0 = max(bx, rx), max(by, ry)
        x1, y1 = min(bx + bw, rx + rw), min(by + bh, ry + rh)
        if x1 > x0 and y1 > y0 and ((x1 - x0) * (y1 - y0)) / (bw * bh) >= 0.25:
            return True
    return False


def _text_height_rule(rs: RuleSet, bundle: CaptureBundle,
                      photos: list[PhotoAnalysis], provider) -> Assessment | None:
    """3.g — 화면 문구의 문자 높이.

    OCR 이 있어야 성립한다. 상자 높이를 문자 높이로 바꾸려면 자종을 알아야 하고
    (한글은 네모틀에 꽉 차고 라틴 소문자는 아니다), 자종은 글자를 읽어야 안다.
    엔진이 없으면 이 함수는 아무것도 만들지 않고 3.g 는 실측으로 넘어간다.
    """
    ok, why = provider.available()
    if not ok:
        return None

    usable = [p for p in photos
              if p.shot.set_id == "S1" and p.scale is not None and p.image is not None]
    if not usable:
        return None

    factor, calibrated, meta = text_height.load_ocr_calibration(
        getattr(provider, "calibration_key", provider.name))
    # 국내 키오스크 화면은 대부분 한글이다. 한글을 못 읽는 엔진이 낸 '위반 없음'은
    # '위반이 없다'가 아니라 '읽지를 못했다'는 뜻이므로, 그렇게 적는다.
    hangul_ok = bool(meta.get("hangul_ok")) if meta else False
    # 교정 산포를 그대로 허용오차로 쓴다. 같은 화면을 다시 찍어도
    # 이만큼은 흔들린다는 뜻이므로, 기준선 언저리를 적합이라 말하지 않는다.
    spread = float(meta.get("spread") or 0.0) if calibrated else 0.0

    screens: list[tuple[PhotoAnalysis, text_height.ScreenTextResult]] = []
    marker_dropped = 0
    failures: list[str] = []
    for ph in usable:
        try:
            read = provider.read(ph.image)
        except Exception as exc:                               # noqa: BLE001
            # 남이 만든 엔진이 터지는 것으로 진단 전체가 멈추면 안 된다
            failures.append(f"{ph.shot.path.name}: {type(exc).__name__}: {exc}")
            continue
        # 마커 카드에는 'BFK-MARK-A', '40mm' 같은 작은 글씨가 인쇄돼 있다.
        # 빼지 않으면 **우리가 붙인 카드 때문에 키오스크가 3.g 부적합**이 된다.
        # 명도 대비에서 이미 겪은 함정이라 여기서도 같은 영역을 적용한다.
        lines = [l for l in read.lines if not _in_exclusion(l.box_px, ph.exclude)]
        dropped = len(read.lines) - len(lines)
        if not lines:
            continue
        measured = [
            text_height.measure_line(
                line.text, line.box_px, ph.scale,
                box_factor=factor, box_calibrated=calibrated)
            for line in lines
        ]
        screens.append((ph, text_height.evaluate_screen(
            measured, tolerance_ratio=spread)))
        marker_dropped += dropped

    if not screens:
        if failures:
            raise OcrEngineError("; ".join(failures[:3]))
        return None

    failed = [t for t in screens if not t[1].passed]
    worst_ph, worst = (
        max(failed, key=lambda t: len(t[1].violations)) if failed
        else min(screens, key=lambda t: t[1].smallest_mm
                 if t[1].smallest_mm is not None else 9e9))

    measured = {
        **worst.as_measured(),
        "검사 화면 수": len(screens),
        "미달 화면": len(failed),
        "대상 화면": worst_ph.shot.path.name,
        "OCR 엔진": (f"{provider.name} — {provider.box_convention} · "
                   f"{'교정됨' if calibrated else '미교정'}"),
        "한글 인식 확인": (f"{meta.get('hangul_read')}/{meta.get('hangul_total')}"
                     if meta.get("hangul_total") else "미확인"),
        "마커 카드 제외": marker_dropped,
        "커버리지": (
            "OCR 이 읽어낸 문구만 검사합니다. 화면의 모든 문구를 빠짐없이 읽는다는 "
            "보장이 없어 위반 검출에만 쓰고 '적합' 확정 근거로는 쓰지 않습니다."
        ),
        "캡션 제외": (
            "비디오 캡션 자동 판별은 하지 않습니다. 별표5 3.g 는 캡션을 제외하므로 "
            "미달 문구가 캡션인지 검토자가 확인해야 합니다."
        ),
    }
    if not calibrated:
        measured["주의"] = (
            f"이 엔진({provider.name})의 상자 계수가 교정되지 않았습니다. "
            "KFA.exe calibrate-ocr 로 교정하면 확신도가 올라갑니다."
        )

    confidence = worst.confidence
    if worst.passed:
        confidence = min(confidence, NO_VIOLATION_CONFIDENCE)

    if calibrated and not hangul_ok:
        measured["한글 인식"] = (
            f"이 엔진({provider.name})은 한글을 읽지 못합니다"
            f"({meta.get('hangul_read', 0)}/{meta.get('hangul_total', 0)}). "
            "한글 문구는 검사되지 않았으므로 위반이 검출되지 않았다는 사실이 "
            "'문자 높이가 충분하다'를 뜻하지 않습니다. 실측으로 확인하세요."
        )
        # 한글을 못 읽는 채로 낸 '위반 없음'은 근거가 되지 못한다.
        # 확신도 게이트에 걸려 자동으로 실측·검토자에게 넘어가게 둔다.
        if worst.passed:
            confidence = min(confidence, 0.3)

    return apply_ai_scope(
        rs["3.g"], Verdict.PASS if worst.passed else Verdict.FAIL,
        evidence=Evidence(
            crop_ref=str(worst_ph.shot.path.relative_to(bundle.root)),
            measured=measured, criterion_id="3.g", confidence=confidence,
        ),
    )


def _flash_rule(rs: RuleSet, bundle: CaptureBundle) -> Assessment | None:
    """8.a — S2 영상에서 깜박임 주파수."""
    videos = [s for s in bundle.by_set("S2")
              if s.path.suffix.lower() in (".mp4", ".mov", ".avi", ".mkv", ".webm")]
    if not videos:
        return None

    worst = None
    worst_shot = None
    for v in videos:
        try:
            series, fps = flash_rate.luminance_series_from_video(str(v.path))
            r = flash_rate.evaluate(series, fps, roi=v.path.name)
        except (flash_rate.FrameRateTooLow, ValueError, FileNotFoundError, RuntimeError):
            continue
        if worst is None or r.peak_hz > worst.peak_hz:
            worst, worst_shot = r, v

    if worst is None:
        return None

    return apply_ai_scope(
        rs["8.a"], Verdict.PASS if worst.passed else Verdict.FAIL,
        evidence=Evidence(
            crop_ref=str(worst_shot.path.relative_to(bundle.root)),
            measured=worst.as_measured(), criterion_id="8.a",
            confidence=worst.confidence,
        ),
    )


def _owner_answers(rs: RuleSet, bundle: CaptureBundle) -> list[Assessment]:
    """점주 응답 — 사정거리 제한을 받지 않는다."""
    out: list[Assessment] = []
    for rule_id, answer in bundle.owner_answers.items():
        if rule_id not in rs.ids():
            continue
        verdict = OWNER_ANSWER_MAP.get(str(answer).lower())
        if verdict is None:
            continue
        out.append(record_external(
            rs[rule_id], verdict, source="OWNER",
            evidence=Evidence(criterion_id=rule_id, measured={
                "점주 응답": OWNER_ANSWER_LABEL.get(str(answer).lower(), answer),
                "질문": " ".join((rs[rule_id].get("owner_question") or "").split()),
            }),
        ))
    return out


def _measurements(rs: RuleSet, bundle: CaptureBundle) -> list[Assessment]:
    """실측값 — AI 추정을 '확인'하는 게 아니라 '대체'한다."""
    out: list[Assessment] = []
    for rule_id, m in bundle.measurements.items():
        if rule_id not in rs.ids():
            continue
        if not isinstance(m, dict):
            raise BundleError(f'{rule_id}: 실측 입력은 객체여야 합니다.')
        if m.get('method') == 'numeric/1':
            from .measurement import evaluate
            try:
                out.append(evaluate(rs[rule_id], m))
            except ValueError as exc:
                raise BundleError(f'{rule_id}: {exc}') from exc
            continue
        verdict = {"pass": Verdict.PASS, "fail": Verdict.FAIL,
                   "na": Verdict.NOT_APPLICABLE}.get(str(m.get("verdict", "")).lower())
        if verdict is None:
            continue
        grade = {"우수": Grade.EXCELLENT, "보통": Grade.NORMAL}.get(m.get("grade"))
        out.append(record_external(
            rs[rule_id], verdict, source=f"MEASURE:{m.get('by', '미상')}",
            grade=grade,
            evidence=Evidence(criterion_id=rule_id, measured={
                k: v for k, v in m.items() if k not in ("verdict", "grade")
            }),
        ))
    return out


# =============================================================================
# 진입점
# =============================================================================

@dataclass
class AnalysisRun:
    bundle: CaptureBundle
    assessments: list[Assessment]
    photos: list[PhotoAnalysis]
    skipped: list[str] = field(default_factory=list)
    used_sets: set[str] = field(default_factory=set)   # 이번 진단이 실제로 읽은 세트
    l2: object | None = None                           # L2Run — 켠 경우에만
    ocr: str = ""                                      # 쓰인 OCR 엔진 이름

    def marker_ok(self) -> int:
        return sum(1 for p in self.photos if p.scale is not None)

    def marker_expected(self) -> int:
        return sum(1 for p in self.photos if p.shot.marker_plane)


def analyze(bundle: CaptureBundle, rs: RuleSet, *, progress=None,
            l2_provider=None, ocr_provider=None) -> AnalysisRun:
    """번들 전체를 분석해 Assessment 목록을 만든다.

    Args:
        l2_provider: engine.l2 공급자. None 이면 judge 23항목은 손대지 않는다 —
            **기본이 꺼짐이다.** 현장 사진을 밖으로 보내는 일은 사용자가 켜야 한다.
        ocr_provider: engine.ocr 공급자. None 이면 설치된 엔진을 자동으로 찾는다 —
            OCR 은 전부 로컬에서 돌고 사진이 밖으로 나가지 않으므로 켜 두는 게 맞다.
    """
    if ocr_provider is None:
        from .ocr import resolve as _resolve_ocr
        ocr_provider = _resolve_ocr()
    def say(msg: str) -> None:
        if progress:
            progress(msg)

    photos: list[PhotoAnalysis] = []
    image_shots = [s for s in bundle.shots
                   if s.path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    for i, shot in enumerate(image_shots, 1):
        say(f"  [{i}/{len(image_shots)}] {shot.shot_id}  {shot.path.name}")
        photos.append(analyze_photo(
            shot,
            want_contrast=shot.set_id == "S1",
            want_controls=shot.marker_plane in PLANE_SURFACE,
        ))

    s1_photos = [p for p in photos if p.shot.set_id == "S1"]

    assessments: list[Assessment] = []
    skipped: list[str] = []

    for rule_id, required in (("3.i", contrast.THRESHOLD_NORMAL),
                              ("3.j", contrast.THRESHOLD_HIGH)):
        a = _contrast_rule(rs, bundle, rule_id, required, s1_photos)
        if a:
            assessments.append(a)
        else:
            skipped.append(f"{rule_id} — S1 화면에서 문구 블록을 찾지 못했습니다")

    try:
        a = _text_height_rule(rs, bundle, photos, ocr_provider)
    except OcrEngineError as exc:
        a = None
        skipped.append(f"3.g — OCR 엔진이 실행 중에 깨졌습니다: {exc}")
    if a:
        assessments.append(a)
    elif not any(m.startswith("3.g") for m in skipped):
        skipped.append(f"3.g — {ocr_provider.available()[1]}")

    for rule_id, fn in (("1.b", _gap_rule), ("1.c", _mbr_rule)):
        a = fn(rs, bundle, photos)
        if a:
            assessments.append(a)
        else:
            skipped.append(
                f"{rule_id} — 마커가 잡힌 조작부·화면 사진에서 작동부 윤곽을 찾지 못했습니다")

    a = _flash_rule(rs, bundle)
    if a:
        assessments.append(a)
    else:
        skipped.append("8.a — S2 영상이 없거나 프레임률이 낮습니다")

    used: set[str] = set()
    for a in assessments:
        ref = a.evidence.crop_ref if a.evidence else None
        if ref:
            used.add(str(ref).split("/")[0].split("\\")[0])

    assessments += _owner_answers(rs, bundle)
    measured = _measurements(rs, bundle)
    measured_ids = {a.rule_id for a in measured}
    assessments = [a for a in assessments if a.rule_id not in measured_ids] + measured

    # ── L2 (judge 23항목) — 켠 경우에만 ─────────────────────────────────
    l2_run = None
    if l2_provider is not None:
        from .l2 import judge as l2judge
        l2_run = l2judge.run(rs, bundle, l2_provider, progress=progress)
        if l2_run.reason and not l2_run.ran:
            skipped.append(f"L2 판정 — {l2_run.reason}")
        for msg in l2_run.failed:
            skipped.append(f"L2 호출 실패 — {msg}")
        # 계측·점주·실측이 이미 값을 낸 항목은 모델 의견으로 덮지 않는다
        assessments = l2judge.merge(assessments, l2_run.assessments)

    assessments = apply_review(rs, bundle, assessments)

    return AnalysisRun(bundle=bundle, assessments=assessments,
                       photos=photos, skipped=skipped, used_sets=used, l2=l2_run,
                       ocr=(ocr_provider.name if ocr_provider.available()[0] else ""))


def apply_review(rs: RuleSet, bundle: CaptureBundle,
                 assessments: list[Assessment]) -> list[Assessment]:
    """검토자 결정을 적용한다 — '위반 의심'이 '부적합'이 되는 유일한 경로.

    이 함수가 없으면 자동 판정은 영원히 '위반 의심'에 머문다.
    설계상 AI 는 부적합을 확정하지 못하도록 막혀 있으므로,
    사람의 승인을 되먹이는 경로가 반드시 있어야 리포트가 완성된다.
    """
    decisions = bundle.review.get("decisions") or {}
    grades = bundle.review.get("grades") or {}
    reviewer = bundle.review.get("reviewer", "검토자")
    if not decisions and not grades:
        return assessments

    out: list[Assessment] = []
    for a in assessments:
        d = decisions.get(a.rule_id)
        if d in ("approve", "reject") and a.verdict is Verdict.SUSPECTED_FAIL:
            author = (bundle.review.get('decision_reviewers') or {}).get(a.rule_id, reviewer)
            a = confirm(a, approved=(d == "approve"), reviewer=author)
        g = grades.get(a.rule_id)
        if g and a.verdict is Verdict.PASS:
            grade = {"우수": Grade.EXCELLENT, "보통": Grade.NORMAL}.get(g)
            if grade and (rs[a.rule_id].get("verdict") or {}).get("grade"):
                a = Assessment(rule_id=a.rule_id, verdict=a.verdict, grade=grade,
                               evidence=a.evidence, source=a.source,
                               next_owner=a.next_owner)
        out.append(a)
    return out


def pending_review(rs: RuleSet, assessments: list[Assessment]) -> list[Assessment]:
    """검토자가 손대야 하는 항목 — 확신도 낮은 순."""
    todo = [a for a in assessments if a.verdict is Verdict.SUSPECTED_FAIL]
    todo.sort(key=lambda a: (a.evidence.confidence if a.evidence
                             and a.evidence.confidence is not None else 1.0))
    return todo
