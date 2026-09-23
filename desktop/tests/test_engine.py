"""엔진 핵심 동작 테스트.

가장 중요한 테스트는 test_ai_scope_* 다 — AI가 자기 사정거리를 넘지 못하는지 검사한다.
이 테스트가 깨지면 리포트가 거짓말을 하기 시작한다.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import rules as R
from engine.calc import contrast, flash_rate, geometry, scale, text_height
from engine.verdict import (
    Assessment, Evidence, Grade, ScopeViolation, Undetermined, Verdict,
    apply_ai_scope, confirm, record_external,
)


# =============================================================================
# 룰 DB
# =============================================================================

@pytest.fixture(scope="module")
def rs() -> R.RuleSet:
    return R.load()


def test_rules_load_and_validate(rs):
    assert len(rs) == 40
    assert rs.meta["total_items"] == 40
    assert R.check(rs) == []


def test_rules_cross_check_with_protocol(rs):
    protocol = R.load_protocol()
    assert R.cross_check(rs, protocol) == []


def test_graded_items_are_exactly_eight(rs):
    ids = sorted(r["id"] for r in rs.graded())
    assert ids == ["1.b", "1.f", "1.g", "3.a", "3.b", "3.d", "3.f", "3.h"]


def test_track_distribution(rs):
    assert len(rs.by_track("A_AI")) == 29
    assert len(rs.by_track("B_OWNER")) == 4
    assert len(rs.by_track("C_MEASURE")) == 7


def test_exemption_applies_to_six_items(rs):
    exempt = rs.exempted_by({"무인민원발급기_표준규격"})
    assert exempt == {"3.a", "3.b", "3.d", "3.h", "3.k", "9.a"}


def test_applicable_filters_conditional_scopes(rs):
    basic = rs.applicable(product_type="중대형")
    assert all(r["scope"] == "기본" for r in basic)
    assert "1.g" not in {r["id"] for r in basic}      # 스캐너 없는 기기

    with_scanner = rs.applicable(product_type="중대형", available_scopes={"스캐너"})
    assert "1.g" in {r["id"] for r in with_scanner}


def test_applicable_drops_exempted(rs):
    ids = {r["id"] for r in rs.applicable(
        product_type="중대형",
        exemption_ids={"무인민원발급기_표준규격"},
    )}
    assert "3.a" not in ids
    assert "3.i" in ids           # 면제 대상 아님


# =============================================================================
# AI 사정거리 — 이 파일의 핵심
# =============================================================================

def _ev(conf=1.0, **measured) -> Evidence:
    return Evidence(criterion_id="X", measured=measured or {"v": 1}, confidence=conf)


def test_ai_scope_both_allows_pass(rs):
    a = apply_ai_scope(rs["3.i"], Verdict.PASS, evidence=_ev())
    assert a.verdict is Verdict.PASS


def test_ai_scope_fail_only_downgrades_pass(rs):
    """AI는 위반을 잡을 수 있어도 '적합'을 증명하지는 못한다."""
    rule = rs["1.b"]
    assert rule["ai_verdict_scope"] == "fail_only"

    a = apply_ai_scope(rule, Verdict.PASS, evidence=_ev())
    assert a.verdict is Verdict.UNDETERMINED
    assert a.downgraded_from is Verdict.PASS
    assert a.reason is Undetermined.NEEDS_MEASURE
    assert a.next_owner == "전문 실측"


def test_ai_fail_never_becomes_final_fail(rs):
    """AI의 FAIL 은 언제나 '위반 의심'까지다."""
    a = apply_ai_scope(rs["3.i"], Verdict.FAIL, evidence=_ev())
    assert a.verdict is Verdict.SUSPECTED_FAIL
    assert a.downgraded_from is Verdict.FAIL
    assert a.next_owner == "검토자"


def test_ai_scope_none_is_rejected(rs):
    """1.d(조작력)는 AI가 관여하지 않는다."""
    a = apply_ai_scope(rs["1.d"], Verdict.PASS, evidence=_ev())
    assert a.verdict is Verdict.UNDETERMINED
    assert a.reason is Undetermined.NEEDS_MEASURE


def test_owner_track_routes_to_owner(rs):
    a = apply_ai_scope(rs["1.f"], Verdict.PASS, evidence=_ev())
    assert a.reason is Undetermined.NEEDS_OWNER
    assert a.next_owner == "점주"


def test_missing_evidence_kills_verdict(rs):
    a = apply_ai_scope(rs["3.i"], Verdict.PASS, evidence=None)
    assert a.verdict is Verdict.UNDETERMINED
    assert a.reason is Undetermined.NO_INPUT


def test_low_confidence_goes_to_reviewer(rs):
    a = apply_ai_scope(rs["3.i"], Verdict.PASS, evidence=_ev(conf=0.5))
    assert a.verdict is Verdict.UNDETERMINED
    assert a.reason is Undetermined.LOW_CONFIDENCE
    assert a.next_owner == "검토자"


def test_grade_on_ungraded_item_raises(rs):
    with pytest.raises(ScopeViolation):
        apply_ai_scope(rs["3.i"], Verdict.PASS, evidence=_ev(), grade=Grade.EXCELLENT)


def test_reviewer_confirms_suspected_fail(rs):
    a = apply_ai_scope(rs["3.i"], Verdict.FAIL, evidence=_ev())
    final = confirm(a, approved=True, reviewer="kim")
    assert final.verdict is Verdict.FAIL
    assert final.source.startswith("REVIEWER")


def test_reviewer_rejection_does_not_become_pass(rs):
    """반려했다고 적합이 되지는 않는다."""
    a = apply_ai_scope(rs["3.i"], Verdict.FAIL, evidence=_ev())
    final = confirm(a, approved=False, reviewer="kim")
    assert final.verdict is Verdict.UNDETERMINED


def test_external_verdict_bypasses_ai_scope(rs):
    """실측은 사정거리 제한을 받지 않는다."""
    a = record_external(
        rs["1.d"], Verdict.PASS,
        source="MEASURE:한국계측", evidence=_ev(force_n=18.4),
    )
    assert a.verdict is Verdict.PASS


# =============================================================================
# L1 계측 — 명도 대비
# =============================================================================

@pytest.mark.parametrize("fg,bg,expected", [
    ("#000000", "#FFFFFF", 21.0),
    ("#FFFFFF", "#FFFFFF", 1.0),
    ("#777777", "#FFFFFF", 4.478),      # WCAG 공식 검증값
    ("#767676", "#FFFFFF", 4.541),      # AA 경계 바로 위
])
def test_contrast_ratio_matches_wcag(fg, bg, expected):
    assert contrast.contrast_ratio(fg, bg) == pytest.approx(expected, abs=0.01)


def test_contrast_is_symmetric():
    a = contrast.contrast_ratio("#1B4D8F", "#F2F4F6")
    b = contrast.contrast_ratio("#F2F4F6", "#1B4D8F")
    assert a == pytest.approx(b)


def test_contrast_threshold_boundary():
    r = contrast.evaluate("#767676", "#FFFFFF", required=4.5)
    assert r.passed
    r = contrast.evaluate("#777777", "#FFFFFF", required=4.5)
    assert not r.passed


def test_high_dispersion_rejects_photo_measurement():
    r = contrast.evaluate("#333333", "#FFFFFF", dispersion=30.0)
    assert r.reject and r.confidence == 0.0


def test_moderate_dispersion_lowers_confidence():
    r = contrast.evaluate("#333333", "#FFFFFF", dispersion=20.0)
    assert 0.0 < r.confidence < 1.0


def test_nearest_passing_color_self_verifies():
    for fg, bg in [("#999999", "#FFFFFF"), ("#555555", "#000000"), ("#A0C8E8", "#FFFFFF")]:
        fixed = contrast.nearest_passing_color(fg, bg, target=4.5)
        assert contrast.contrast_ratio(fixed, bg) >= 4.5


def test_nearest_passing_color_leaves_passing_color_alone():
    assert contrast.nearest_passing_color("#000000", "#FFFFFF") == "#000000"


def test_sample_dispersion():
    assert contrast.sample_dispersion([(10, 10, 10)]) == 0.0
    assert contrast.sample_dispersion([(0, 0, 0), (100, 0, 0)]) > 50


# =============================================================================
# L1 계측 — 마커 환산
# =============================================================================

def _square(x, y, size):
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def test_scale_frontal_marker_is_exact():
    """정면 촬영 마커: 40mm 정사각이 400px 로 찍혔다면 10px/mm."""
    s = scale.build_plane_scale(_square(100, 100, 400), plane="display", frame_width_px=4000)
    assert s.px_per_mm == pytest.approx(10.0)
    assert s.skew == pytest.approx(1.0)
    assert s.confidence == 1.0
    assert s.length_mm((100, 100), (200, 100)) == pytest.approx(10.0)


def test_scale_area_conversion():
    s = scale.build_plane_scale(_square(0, 0, 400), plane="control_panel", frame_width_px=4000)
    # 120px x 120px = 12mm x 12mm = 144mm²  (별표5 1.c 경계값)
    area = s.area_mm2([(0, 0), (120, 0), (120, 120), (0, 120)])
    assert area == pytest.approx(144.0, abs=0.01)


def test_scale_rejects_tiny_marker():
    with pytest.raises(scale.MarkerError, match="너무 작게"):
        scale.build_plane_scale(_square(0, 0, 50), plane="display", frame_width_px=4000)


def test_scale_rejects_extreme_skew():
    quad = [(0, 0), (400, 0), (400, 100), (0, 400)]   # 심하게 기울어진 사각형
    with pytest.raises(scale.MarkerError, match="촬영각"):
        scale.build_plane_scale(quad, plane="display", frame_width_px=4000)


def test_scale_rejects_degenerate_marker():
    with pytest.raises(scale.MarkerError):
        scale.build_plane_scale([(0, 0), (0, 0), (0, 0), (0, 0)], plane="display")


def test_scale_perspective_recovers_true_size():
    """사영 왜곡이 있어도 mm 복원이 맞는지 — 사다리꼴 마커."""
    quad = [(100, 100), (500, 120), (490, 500), (110, 470)]
    s = scale.build_plane_scale(quad, plane="display", frame_width_px=4000)
    # 마커 자신의 변은 정의상 40mm 여야 한다
    assert s.length_mm(quad[0], quad[1]) == pytest.approx(40.0, abs=0.01)
    assert s.length_mm(quad[1], quad[2]) == pytest.approx(40.0, abs=0.01)


# =============================================================================
# L1 계측 — 작동부 치수
# =============================================================================

def test_mbr_boundary_144mm2_passes():
    """12x12 경계상자 = 144mm² — 별표5 1.c 경계값."""
    boxes = [geometry.Box("btn1", 0, 0, 12.0, 12.0, pressable_area=144.0)]
    r = geometry.check_mbr(boxes)
    assert r.passed and not r.violations


def test_mbr_below_side_minimum_fails():
    boxes = [geometry.Box("btn1", 0, 0, 11.9, 20.0)]
    r = geometry.check_mbr(boxes)
    assert not r.passed
    assert r.violations[0]["작동부"] == "btn1"


def test_mbr_area_is_implied_by_side_rule():
    """별표5의 144mm² 는 독립 제약이 아니다 — 두 변이 12mm 이상이면 자동 충족."""
    for w in (12.0, 12.5, 30.0):
        for h in (12.0, 18.0, 40.0):
            b = geometry.Box("x", 0, 0, w, h)
            assert b.area >= geometry.MBR_AREA_MIN_MM2


def test_ks_advisory_uses_pressable_area_not_bounding_box():
    """지름 12mm 원형 버튼: 별표5 통과, KS §5.6.2 권고 미달.

    경계상자 면적을 150 과 비교하면 이 케이스를 놓친다 — 원문 대조로 확정된 구분.
    """
    circle_area = math.pi * 6.0 ** 2          # 113.1mm²
    boxes = [geometry.Box("원형버튼", 0, 0, 12.0, 12.0, pressable_area=circle_area)]
    r = geometry.check_mbr(boxes)
    assert r.passed                            # 별표5 1.c 는 통과
    assert len(r.advisory) == 1                # KS 권고는 미달
    assert r.advisory[0]["누름 부위 면적"] == pytest.approx(113.1, abs=0.1)


def test_ks_advisory_needs_138mm_circle_to_pass():
    """150mm² 를 원형으로 채우려면 지름 13.8mm 가 필요하다."""
    d = 2 * (geometry.PRESSABLE_AREA_ADVISORY_MM2 / math.pi) ** 0.5
    assert d == pytest.approx(13.82, abs=0.01)
    boxes = [geometry.Box("원형버튼", 0, 0, d, d,
                          pressable_area=math.pi * (d / 2) ** 2)]
    r = geometry.check_mbr(boxes)
    assert r.passed and not r.advisory


def test_rectangular_button_pressable_equals_bbox():
    """직사각형 버튼은 누름 면적 = 경계상자 면적."""
    boxes = [geometry.Box("사각버튼", 0, 0, 13.0, 13.0, pressable_area=169.0)]
    r = geometry.check_mbr(boxes)
    assert r.passed and not r.advisory


def test_unmeasured_pressable_area_is_reported_not_guessed():
    """누름 면적을 못 쟀으면 경계상자로 대신 판단하지 않고 미측정으로 남긴다."""
    boxes = [geometry.Box("btn1", 0, 0, 12.0, 12.0, pressable_area=None)]
    r = geometry.check_mbr(boxes)
    assert r.passed
    assert r.advisory == []
    assert r.unmeasured_pressable == 1
    assert "누름 면적 미측정" in r.as_measured()


def test_boxes_from_pixels_computes_both_areas():
    """마커 환산에서 경계상자와 누름 면적이 함께 나오는지."""
    s = scale.build_plane_scale(_square(0, 0, 400), plane="control_panel", frame_width_px=4000)
    # 10px/mm. 팔각형으로 근사한 원형 버튼 (외접 사각 13x13mm = 130px)
    import math as _m
    R = 65.0                                    # px = 6.5mm 반지름
    poly = [(65 + R * _m.cos(a), 65 + R * _m.sin(a))
            for a in [i * 2 * _m.pi / 16 for i in range(16)]]
    boxes = geometry.boxes_from_pixels({"원형": poly}, s)
    b = boxes[0]
    assert b.w == pytest.approx(13.0, abs=0.05)                # 경계상자 13x13 = 169mm²
    assert b.pressable_area == pytest.approx(129.33, abs=0.5)  # 실제 누름 면적
    assert b.pressable_area < b.area

    # 별표5는 통과(경계상자 169mm²), KS 권고는 미달(누름 면적 129mm² < 150)
    r = geometry.check_mbr(boxes)
    assert r.passed
    assert len(r.advisory) == 1


def test_touch_gap_violation():
    boxes = [geometry.Box("a", 0, 0, 20, 20), geometry.Box("b", 22, 0, 20, 20)]
    r = geometry.check_gaps(boxes, surface="touch")
    assert not r.passed
    assert r.min_gap_mm == pytest.approx(2.0)


def test_touch_gap_pass():
    boxes = [geometry.Box("a", 0, 0, 20, 20), geometry.Box("b", 23, 0, 20, 20)]
    r = geometry.check_gaps(boxes, surface="touch")
    assert r.passed


def test_physical_gap_cannot_confirm_pass_with_measurement_error():
    """0.5mm 기준은 촬영 오차와 겹치므로 여유가 없으면 적합을 선언하지 않는다."""
    boxes = [geometry.Box("a", 0, 0, 10, 10), geometry.Box("b", 10.6, 0, 10, 10)]
    r = geometry.check_gaps(boxes, surface="physical", measurement_error_mm=0.3)
    assert r.passed                       # 기준(0.5mm)은 넘겼지만
    assert not r.can_confirm_pass         # 오차 여유가 없어 확정 불가


def test_physical_gap_can_confirm_with_wide_margin():
    boxes = [geometry.Box("a", 0, 0, 10, 10), geometry.Box("b", 13, 0, 10, 10)]
    r = geometry.check_gaps(boxes, surface="physical", measurement_error_mm=0.3)
    assert r.can_confirm_pass


def test_gap_ignores_distant_buttons():
    boxes = [geometry.Box("a", 0, 0, 10, 10), geometry.Box("b", 500, 0, 10, 10)]
    r = geometry.check_gaps(boxes, surface="touch", neighbor_radius_mm=60)
    assert r.pairs_checked == 0


def test_reach_height_boundary_flags():
    r = geometry.check_height(1215.0, label="결제 버튼")
    assert r.passed and r.near_boundary       # 1,220mm 경계 근처
    r = geometry.check_height(800.0, label="결제 버튼")
    assert r.passed and not r.near_boundary
    r = geometry.check_height(1250.0, label="결제 버튼")
    assert not r.passed


def test_visual_info_height_uses_upper_bound_only():
    r = geometry.check_height(1350.0, label="화면 상단", lo=None, hi=geometry.VISUAL_MAX_MM)
    assert r.passed


def test_protrusion():
    assert geometry.check_protrusion(25.0)["충족"] is True
    assert geometry.check_protrusion(15.0)["충족"] is False


# =============================================================================
# L1 계측 — 문자 높이
# =============================================================================

def test_script_classification():
    assert text_height.classify_script("주문하기") == "hangul"
    assert text_height.classify_script("ORDER") == "latin_caps"
    assert text_height.classify_script("Order now") == "latin_mixed"
    assert text_height.classify_script("주문 Order") == "mixed"
    assert text_height.classify_script("12345") == "digits"


def test_text_height_uses_calibrated_script_ratio():
    """실측 교정된 계수를 쓰는지 — 한글 0.708 (교정 전 추정값은 0.86 이었다)."""
    s = scale.build_plane_scale(_square(0, 0, 400), plane="display", frame_width_px=4000)
    box = [(0, 0), (200, 0), (200, 100), (0, 100)]      # 100px = 10mm
    r = text_height.measure_line("주문하기", box, s)
    assert r.box_height_mm == pytest.approx(10.0)
    assert r.char_height_mm == pytest.approx(7.08, abs=0.01)


def test_calibration_made_the_check_stricter():
    """교정으로 판정이 엄격해졌다 — 같은 박스가 이제 위반으로 잡힌다.

    교정 전 추정 계수(한글 0.86)는 10mm 박스를 8.6mm 로 봐서 7.25mm 를 통과시켰다.
    실측 계수(0.708)로는 7.08mm 라 미달이다. 추정이 위반을 놓치고 있었다는 뜻이다.
    """
    s = scale.build_plane_scale(_square(0, 0, 400), plane="display", frame_width_px=4000)
    box = [(0, 0), (200, 0), (200, 100), (0, 100)]
    r = text_height.measure_line("주문하기", box, s)
    assert not r.passed                                  # 교정 후: 미달
    assert 10.0 * 0.86 > text_height.MIN_CHAR_HEIGHT_MM   # 교정 전이었다면 통과했다


def test_text_height_passes_with_adequate_box():
    s = scale.build_plane_scale(_square(0, 0, 400), plane="display", frame_width_px=4000)
    box = [(0, 0), (200, 0), (200, 110), (0, 110)]      # 11mm 박스 → 7.79mm
    r = text_height.measure_line("주문하기", box, s)
    assert r.char_height_mm == pytest.approx(7.79, abs=0.01)
    assert r.passed


def test_metric_calibrated_but_ocr_box_is_not():
    """계수가 두 성분으로 쪼개져 있고, 교정 상태가 각각 관리되는지."""
    assert text_height.METRIC_CALIBRATED is True
    assert text_height.OCR_BOX_CALIBRATED is False


def test_confidence_still_capped_by_uncalibrated_ocr_factor():
    """폰트 메트릭은 교정됐지만 OCR 상자 규약이 미교정이라 확신도 상한이 남아 있다."""
    s = scale.build_plane_scale(_square(0, 0, 400), plane="display", frame_width_px=4000)
    box = [(0, 0), (200, 0), (200, 100), (0, 100)]
    r = text_height.measure_line("주문하기", box, s)
    assert r.confidence <= text_height.OCR_UNCALIBRATED_CONFIDENCE
    assert r.confidence < 0.85


def test_high_spread_script_gets_lower_confidence():
    """산포가 큰 자종(라틴 혼용)은 계수 하나로 대표되지 않으므로 확신도가 낮다."""
    s = scale.build_plane_scale(_square(0, 0, 400), plane="display", frame_width_px=4000)
    box = [(0, 0), (300, 0), (300, 100), (0, 100)]
    hangul = text_height.measure_line("주문하기", box, s)
    latin = text_height.measure_line("Tap to begin", box, s)
    assert latin.confidence < hangul.confidence


def test_caption_excluded_from_screen_verdict():
    s = scale.build_plane_scale(_square(0, 0, 400), plane="display", frame_width_px=4000)
    small = [(0, 0), (100, 0), (100, 30), (0, 30)]     # 3mm 박스 → 미달
    caption = text_height.measure_line("자막", small, s, is_caption=True)
    result = text_height.evaluate_screen([caption])
    assert result.passed               # 캡션은 3.g 대상 아님
    assert result.lines == []


# =============================================================================
# L1 계측 — 깜박임
# =============================================================================

def _blink(hz: float, fps: float, seconds: float):
    n = int(fps * seconds)
    return [0.5 + 0.4 * math.sin(2 * math.pi * hz * i / fps) for i in range(n)]


def test_flash_rejects_low_fps():
    with pytest.raises(flash_rate.FrameRateTooLow):
        flash_rate.evaluate(_blink(2, 24, 3), fps=24)


def test_flash_two_hz_passes():
    r = flash_rate.evaluate(_blink(2.0, 60, 4), fps=60)
    assert r.passed
    assert r.peak_hz < 3.0


def test_flash_five_hz_fails():
    r = flash_rate.evaluate(_blink(5.0, 60, 4), fps=60)
    assert not r.passed
    assert r.peak_hz >= 3.0


def test_flash_static_screen_passes():
    r = flash_rate.evaluate([0.5] * 120, fps=60)
    assert r.passed and r.flashes == 0


def test_flash_burst_not_diluted_by_long_video():
    """3초짜리 점멸이 30초 영상에 희석되어 묻히면 안 된다."""
    series = [0.5] * (60 * 20) + _blink(6.0, 60, 3) + [0.5] * (60 * 7)
    r = flash_rate.evaluate(series, fps=60)
    assert not r.passed


def test_flash_ignores_small_amplitude_noise():
    series = [0.5 + 0.02 * (i % 2) for i in range(240)]
    r = flash_rate.evaluate(series, fps=60)
    assert r.flashes == 0


# =============================================================================
# 마커 — 검출 경계 보정 및 실물 카드 왕복 검증
# =============================================================================

def test_edge_bias_expands_quad_by_one_pixel():
    q = [(100.0, 100.0), (1300.0, 100.0), (1300.0, 1300.0), (100.0, 1300.0)]
    c = scale.correct_edge_bias(q)
    before = 1200.0
    after = ((c[1][0]-c[0][0])**2 + (c[1][1]-c[0][1])**2) ** 0.5
    assert after == pytest.approx(before + 1.0, abs=0.02)


def test_edge_bias_keeps_centroid():
    q = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    c = scale.correct_edge_bias(q)
    cx = sum(p[0] for p in c) / 4
    cy = sum(p[1] for p in c) / 4
    assert (cx, cy) == pytest.approx((50.0, 50.0))


def test_generated_marker_card_round_trips_to_40mm():
    """tools/make_marker.py 가 만든 카드를 엔진이 읽어 40mm 로 복원하는지."""
    cv2 = pytest.importorskip("cv2")
    card = Path(__file__).resolve().parent.parent / "assets" / "BFK-MARK-A_id0.png"
    if not card.exists():
        pytest.skip("assets/BFK-MARK-A_id0.png 없음 — tools/make_marker.py 를 먼저 실행")

    # Windows 한글 경로에서는 cv2.imread 가 파일을 열지 못한다 — 바이트로 읽어 디코드한다
    import numpy as np
    img = cv2.imdecode(np.fromfile(str(card), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, "카드 이미지를 디코드하지 못했습니다"
    found = scale.detect_marker_corners(img, marker_id=0)
    assert found is not None
    corners, mid = found
    assert mid == 0

    s = scale.build_plane_scale(corners, plane='display', frame_width_px=img.shape[1])
    PPM = 30.0                      # 762dpi 카드
    assert s.px_per_mm == pytest.approx(PPM, rel=0.002)
    assert s.skew == pytest.approx(1.0, abs=0.01)
    # 카드의 20mm 눈금자 구간이 20mm 로 복원되어야 한다
    a = (corners[0][0], corners[0][1])
    b = (corners[1][0], corners[1][1])
    assert s.length_mm(a, b) == pytest.approx(40.0, abs=0.05)


# =============================================================================
# 음량 — 별표5 5.c + 측정 SOP (미결 #2 확정분)
# =============================================================================

from engine.calc import sound  # noqa: E402


def _reading(point, measured, background):
    return sound.VolumeReading.take(point, measured, background)


def test_background_correction_negligible_above_10db():
    v, note = sound.background_correction(70.0, 50.0)
    assert v == pytest.approx(70.0)
    assert "보정 불필요" in note


def test_background_correction_applied_between_3_and_10db():
    v, note = sound.background_correction(66.0, 60.0)
    # 10log10(10^6.6 - 10^6.0) = 64.7 근처
    assert v == pytest.approx(64.74, abs=0.05)
    assert v < 66.0
    assert "보정 적용" in note


def test_background_correction_invalid_below_3db():
    with pytest.raises(sound.MeasurementInvalid, match="재측정"):
        sound.background_correction(62.0, 60.0)


def test_volume_uses_lower_of_two_measurement_points():
    """두 측정점 중 낮은 값을 판정값으로 — 보수적."""
    r = sound.check_volume(
        [_reading("standing", 72.0, 45.0), _reading("wheelchair", 66.0, 45.0)],
        reset_dba=58.0, mute_available=True,
    )
    assert r.max_dba == pytest.approx(66.0)
    assert r.passed


def test_volume_below_65_fails():
    r = sound.check_volume(
        [_reading("standing", 63.0, 40.0)], reset_dba=55.0, mute_available=True
    )
    assert not r.passed
    assert any("최대 음량" in f for f in r.failures)


def test_volume_reset_at_65_fails_boundary():
    """5.c 후단은 '65dBA 미만'이므로 정확히 65.0 은 부적합."""
    r = sound.check_volume(
        [_reading("standing", 70.0, 40.0)], reset_dba=65.0, mute_available=True
    )
    assert not r.passed
    assert any("초기화" in f for f in r.failures)


def test_volume_missing_mute_fails():
    r = sound.check_volume(
        [_reading("standing", 70.0, 40.0)], reset_dba=55.0, mute_available=False
    )
    assert not r.passed
    assert "음소거 수단 없음" in r.failures


def test_volume_advisory_range_and_step_are_not_judgments():
    """18dB 범위·12dB 중간 단계는 EN 권고이지 판정이 아니다."""
    r = sound.check_volume(
        [_reading("standing", 70.0, 40.0)],
        reset_dba=55.0, mute_available=True,
        min_volume_dba=60.0,      # 범위 10dB → 권고 미달
        mid_step_dba=64.0,        # 중간 +4dB → 권고 미달
    )
    assert r.passed                       # 별표5 판정은 통과
    assert len(r.advisory) == 2           # 권고만 미달
    assert "EN 301 549" in str(r.as_measured()["권고 출처"])


def test_volume_measurement_conditions_are_printed():
    """측정 조건 없이 인쇄된 dBA 값은 재현되지 않는다."""
    r = sound.check_volume(
        [_reading("standing", 70.0, 40.0)], reset_dba=55.0, mute_available=True
    )
    m = r.as_measured()
    assert "300mm" in m["측정 조건"]
    assert "1500" in m["측정 조건"] and "1150" in m["측정 조건"]
    assert "A특성" in m["측정 조건"]


def test_sop_summary_declares_its_own_provenance():
    s = sound.sop_summary()
    assert "측정 거리 규정이 없어" in s["근거"]


def test_en_301_549_reference_level_is_consistent():
    """EN 의 -29 dBPaA 가 65 dB SPL(re 20µPa)과 맞는지 — 기준점 검산."""
    import math
    pa = 20e-6 * 10 ** (65.0 / 20)
    dbpa = 20 * math.log10(pa)
    assert dbpa == pytest.approx(-29.0, abs=0.05)


# =============================================================================
# 리포트 내보내기 (미결 #4 확정분)
# =============================================================================

from engine.report import export as rexport  # noqa: E402


@pytest.fixture(scope="module")
def bundle(rs):
    from engine.report import matrix
    from engine.verdict import Evidence, Verdict, apply_ai_scope, confirm
    a = apply_ai_scope(
        rs["3.i"], Verdict.FAIL,
        evidence=Evidence(criterion_id="3.i", measured={"명도대비": 2.56}, confidence=1.0),
    )
    a = confirm(a, approved=True, reviewer="테스트")
    return matrix.build(rs, [a], device_id="TEST-001", product_type="중대형")


def test_field_map_has_all_declared_fields(bundle):
    fm = rexport.to_field_map(bundle, inspect_date="2026-08-22", reviewer="박○○")
    for name in rexport.FIELD_ORDER:
        assert name in fm, f"필드 누락: {name}"
    assert all(isinstance(v, str) for v in fm.values())


def test_hwp_edits_binding(bundle):
    fm = rexport.to_field_map(bundle, inspect_date="2026-08-22")
    edits = rexport.to_hwp_edits(fm, {"기기_ID": "0:130", "적합": "0:214"})
    assert edits == {"0:130": "TEST-001", "0:214": fm["적합"]}


def test_hwp_edits_rejects_unknown_field(bundle):
    fm = rexport.to_field_map(bundle, inspect_date="2026-08-22")
    with pytest.raises(KeyError):
        rexport.to_hwp_edits(fm, {"없는필드": "0:1"})


def test_csv_has_bom_for_hangul(bundle):
    csv_text = rexport.to_csv(bundle)
    assert csv_text.startswith("\ufeff")      # 한글에서 인코딩이 깨지지 않게
    assert "3.i" in csv_text


def test_csv_row_count_matches_matrix(bundle):
    rows = rexport.to_csv(bundle).strip().split("\n")
    assert len(rows) == len(bundle.matrix) + 1     # 헤더 1줄


def test_undetermined_csv_lists_next_owner(bundle):
    t = rexport.undetermined_csv(bundle)
    assert "다음 담당" in t
    assert "전문 실측" in t


def test_json_export_round_trips(bundle):
    import json as _json
    data = _json.loads(rexport.to_json(bundle))
    assert data["device_id"] == "TEST-001"
    assert len(data["matrix"]) == 40
    assert "법정 적합성 인증이 아닙니다" in data["scope_note"]


# =============================================================================
# KS 저작권 가드 (미결 #5 확정분) — 가드가 실제로 잡는지 검증한다
# =============================================================================

def test_ks_copyright_guard_passes_on_clean_repo():
    import subprocess
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, str(root / "tools" / "check_ks_copyright.py")],
        capture_output=True, cwd=str(root),
        env={**__import__("os").environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")


def test_ks_copyright_guard_catches_planted_verbatim(tmp_path, monkeypatch):
    """항상 통과하는 가드는 가드가 아니다 — 위반을 심어 잡히는지 확인한다."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    import importlib
    guard = importlib.import_module("tools.check_ks_copyright")

    monkeypatch.setattr(guard, "KS_VERBATIM_MARKERS", ["SYNTHETIC_COPYRIGHT_TEST_MARKER"])
    planted = tmp_path / "leaked_report.md"
    planted.write_text(
        "# 리포트\nSYNTHETIC_COPYRIGHT_TEST_MARKER\n",
        encoding="utf-8",
    )
    problems = guard.scan_verbatim([planted])
    assert problems, "심어 둔 KS 본문을 가드가 잡지 못했습니다"
    assert "leaked_report.md" in problems[0]


def test_ks_ref_fields_contain_no_sentences():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    import importlib
    guard = importlib.import_module("tools.check_ks_copyright")
    assert guard.scan_ks_refs() == []


# =============================================================================
# 1.f 트랙 결정 (미결 #6 확정분)
# =============================================================================

def test_1f_stays_owner_track_with_optional_ai_screen(rs):
    rule = rs["1.f"]
    assert rule["track"] == "B_OWNER"
    assert rule["final_by"] == "OWNER"
    opt = rule.get("optional_ai_screen")
    assert opt is not None, "선택적 AI 스크리닝 경로가 정의되지 않았습니다"
    assert opt["shot"] == "S2-05"
    assert opt["ai_verdict_scope"] == "fail_only"   # 적합은 확정하지 못한다


def test_1f_optional_screen_does_not_change_final_owner(rs):
    """S2-05 가 있어도 최종 확정은 점주다 — 영상 한 컷으로 모든 매체를 확인할 수 없다."""
    a = apply_ai_scope(rs["1.f"], Verdict.PASS, evidence=_ev())
    assert a.verdict is Verdict.UNDETERMINED
    assert a.next_owner == "점주"


# =============================================================================
# 사진 표본 추출 — 실제 이미지에서 색을 뽑는 경로
# =============================================================================

def _render_text(fg_hex, bg_hex, *, thickness=2, jpeg=82):
    """글자를 렌더하고 JPEG 을 태워 '사진처럼' 만든다."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    r, g, b = contrast.hex_to_rgb(fg_hex); fg = (b, g, r)
    r, g, b = contrast.hex_to_rgb(bg_hex); bg = (b, g, r)
    img = np.full((90, 700, 3), bg, np.uint8)
    cv2.putText(img, "Select payment method", (12, 58),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, fg, thickness, cv2.LINE_AA)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR)


def test_otsu_sampling_finds_thin_text():
    """얇은 글자를 백분위로 뽑으면 배경만 잡힌다 — Otsu 는 잡아낸다."""
    patch = _render_text("#9AA3AB", "#FFFFFF")
    got = contrast.sample_fg_bg(patch)
    assert got is not None
    fg, bg, disp = got
    assert contrast.contrast_ratio(fg, bg) > 1.5      # 배경만 잡았다면 1.0 근처가 된다
    assert disp > 0


def test_otsu_sampling_rejects_blank_patch():
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    blank = np.full((60, 200, 3), 255, np.uint8)
    assert contrast.sample_fg_bg(blank) is None


@pytest.mark.parametrize("fg,bg", [
    ("#000000", "#FFFFFF"), ("#333333", "#FFFFFF"), ("#767676", "#FFFFFF"),
    ("#9AA3AB", "#FFFFFF"), ("#FFFFFF", "#1B579B"), ("#E0E0E0", "#333333"),
])
def test_photo_contrast_bias_is_always_underestimate(fg, bg):
    """사진 표본 편향의 **방향이 일정**한지 — 이게 안전성의 근거다.

    항상 낮게 나오면 위반을 놓치지 않는다(과잉 신고는 검토자가 거른다).
    한 케이스라도 과대로 뒤집히면 false pass 가 생기므로 설계를 다시 봐야 한다.
    """
    got = contrast.sample_fg_bg(_render_text(fg, bg))
    assert got is not None
    m_fg, m_bg, _ = got
    truth = contrast.contrast_ratio(fg, bg)
    measured = contrast.contrast_ratio(m_fg, m_bg)
    assert measured < truth, "사진 표본이 실제보다 높게 나왔다 — 놓침 위험"
    assert (truth - measured) / truth <= contrast.PHOTO_UNDERESTIMATE_MAX


def test_borderline_flagged_when_bias_could_flip_verdict():
    """기준 바로 아래 값은 확정하지 않고 검토자에게 넘긴다."""
    # 3.95:1 — 편향(최대 25%)을 되돌리면 5.27:1 이라 기준을 넘을 수 있다
    r = contrast.evaluate("#808080", "#FFFFFF", required=4.5, dispersion=10.0)
    assert not r.passed
    assert r.borderline
    assert r.confidence <= 0.6
    assert "경계" in r.as_measured()


def test_clear_violation_is_not_borderline():
    """편향을 되돌려도 기준에 한참 못 미치면 경계가 아니다."""
    r = contrast.evaluate("#C8CDD2", "#FFFFFF", required=4.5, dispersion=10.0)
    assert not r.passed and not r.borderline


def test_borderline_only_applies_to_photo_samples():
    """확정 색(디자인 파일 등)에는 편향이 없으므로 경계 처리도 없다."""
    r = contrast.evaluate("#808080", "#FFFFFF", required=4.5, dispersion=None)
    assert not r.passed and not r.borderline


def test_simulated_capture_chain_end_to_end():
    """합성 촬영 → 마커 검출 → 환산 → 계측 → 판정 전 구간."""
    pytest.importorskip("cv2")
    import subprocess, os
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, str(root / "tools" / "simulate_capture.py")],
        capture_output=True, cwd=str(root),
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")[-2000:]


# =============================================================================
# 파이프라인 — 수집 번들 → 판정 (앱과 엔진의 접점)
# =============================================================================

from engine import pipeline  # noqa: E402


def _mini_bundle(tmp_path, *, with_marker=True, fg="#9AA3AB"):
    """작은 번들을 만든다 — 실제 마커 카드를 붙일 수도, 뺄 수도 있다."""
    cv2 = pytest.importorskip("cv2")
    import json
    import numpy as np
    from tools import simulate_capture as sim

    panel, quads = sim.build_panel()
    tx0, ty0 = sim.mm(20.0), sim.mm(105.0)
    cv2.rectangle(panel, (tx0, ty0), (sim.mm(200.0), sim.mm(135.0)), (255, 255, 255), -1)
    cv2.putText(panel, "Select payment method", (tx0 + sim.mm(4), ty0 + sim.mm(20)),
                cv2.FONT_HERSHEY_SIMPLEX, sim.PPM / 8 * 1.1, sim.hex_bgr(fg),
                max(1, int(sim.PPM / 4)), cv2.LINE_AA)
    if with_marker:
        panel, _ = sim.attach_marker(panel, quads)
    photo, _ = sim.photograph(panel, tilt=0.12)

    (tmp_path / "S1").mkdir(parents=True, exist_ok=True)
    ok, enc = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    enc.tofile(str(tmp_path / "S1" / "S1-01_001.jpg"))

    manifest = {
        "schema": pipeline.SCHEMA,
        "device": {"id": "T-001", "location": "테스트", "product_type": "중대형",
                   "scopes": ["물건 투입"], "exemptions": []},
        "captured_at": "2026-08-22T00:00:00+09:00", "captured_by": "테스트",
        "shots": [{"set": "S1", "shot": "S1-01", "file": "S1/S1-01_001.jpg",
                   "marker_plane": "display" if with_marker else None, "masked": True}],
        "owner_answers": {"3.b": "no"},
        "measurements": {"9.a": {"verdict": "fail", "by": "테스트계측", "높이": 1285}},
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_bundle_rejects_wrong_schema(tmp_path):
    import json
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": "wrong/9"}), encoding="utf-8")
    with pytest.raises(pipeline.BundleError, match="지원하지 않는 형식"):
        pipeline.load_bundle(tmp_path)


def test_bundle_rejects_missing_file(tmp_path):
    import json
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": pipeline.SCHEMA,
        "device": {"id": "X", "product_type": "중대형"},
        "shots": [{"set": "S1", "shot": "S1-01", "file": "S1/없는파일.jpg"}],
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pipeline.BundleError, match="없습니다"):
        pipeline.load_bundle(tmp_path)


def test_bundle_rejects_bad_product_type(tmp_path):
    import json
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": pipeline.SCHEMA,
        "device": {"id": "X", "product_type": "대형"},
        "shots": [],
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pipeline.BundleError, match="product_type"):
        pipeline.load_bundle(tmp_path)


def test_pipeline_detects_contrast_violation(tmp_path, rs):
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs)
    by = {a.rule_id: a for a in run.assessments}
    assert "3.i" in by
    assert by["3.i"].verdict is Verdict.SUSPECTED_FAIL       # 2.56:1 → 위반
    assert by["3.i"].evidence.measured["명도대비"] < 4.5


def test_pipeline_marker_scale_recovered(tmp_path, rs):
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs)
    assert run.marker_expected() == 1
    assert run.marker_ok() == 1


def test_pipeline_reports_missing_marker_without_crashing(tmp_path, rs):
    b = pipeline.load_bundle(_mini_bundle(tmp_path, with_marker=False))
    run = pipeline.analyze(b, rs)
    assert run.marker_expected() == 0          # manifest 가 마커 없음을 밝혔다
    assert any(a.rule_id == "3.i" for a in run.assessments)   # 대비는 여전히 판정된다


def test_pipeline_no_violation_is_not_confirmed_pass(tmp_path, rs):
    """위반을 못 찾았다고 적합이 아니다 — 커버리지가 보장되지 않는다."""
    b = pipeline.load_bundle(_mini_bundle(tmp_path, fg="#1A1A1A"))   # 고대비
    run = pipeline.analyze(b, rs)
    by = {a.rule_id: a for a in run.assessments}
    a = by["3.i"]
    assert a.evidence.confidence <= pipeline.NO_VIOLATION_CONFIDENCE
    assert a.verdict is Verdict.UNDETERMINED           # 확신도 미달 → 검토자
    assert a.reason is Undetermined.LOW_CONFIDENCE


def test_pipeline_owner_answer_becomes_external(tmp_path, rs):
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs)
    by = {a.rule_id: a for a in run.assessments}
    assert by["3.b"].verdict is Verdict.FAIL
    assert by["3.b"].source == "OWNER"


def test_pipeline_measurement_bypasses_ai_scope(tmp_path, rs):
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs)
    by = {a.rule_id: a for a in run.assessments}
    assert by["9.a"].verdict is Verdict.FAIL
    assert by["9.a"].source.startswith("MEASURE")


def test_text_detection_excludes_marker_card(tmp_path):
    """마커 카드에 인쇄된 회색 글씨가 위반으로 잡히면 안 된다."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from engine.detect import text_regions as T

    path = _mini_bundle(tmp_path) / "S1" / "S1-01_001.jpg"
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    found = scale.detect_marker_corners(img)
    assert found is not None

    without = len(T.detect(img))
    with_excl = len(T.detect(img, exclude=[T.marker_exclusion(found[0])]))
    assert with_excl < without, "마커 카드 영역이 제외되지 않았습니다"


def test_app_protocol_matches_rules(rs):
    """앱이 읽는 protocol.json 이 룰 DB 와 어긋나지 않는지."""
    import json
    from engine.paths import resource_dir
    p = resource_dir() / "app" / "protocol.json"
    if not p.exists():
        pytest.skip("app/protocol.json 없음 — tools/build_app.py 를 먼저 실행")
    d = json.loads(p.read_text(encoding="utf-8"))

    assert d["schema"] == pipeline.SCHEMA
    assert d["rules_version"] == rs.meta["version"]
    assert d["total_items"] == len(rs)
    assert len(d["owner_questions"]) == len(rs.by_track("B_OWNER"))
    assert len(d["measure_items"]) == len(rs.by_track("C_MEASURE"))
    for q in d["owner_questions"]:
        assert q["id"] in rs.ids()
        assert q["question"], f"{q['id']} 질문이 비었습니다"


# =============================================================================
# 검토자 결정 · 인쇄본 (마지막으로 닫은 구멍들)
# =============================================================================

def test_review_turns_suspected_into_fail(tmp_path, rs):
    """'위반 의심'이 '부적합'이 되는 유일한 경로가 실제로 동작하는지."""
    import json
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs)
    todo = pipeline.pending_review(rs, run.assessments)
    assert todo, "승인 대기 항목이 없다"

    (tmp_path / pipeline.REVIEW_FILE).write_text(json.dumps({
        "schema": pipeline.REVIEW_SCHEMA, "reviewer": "테스트",
        "decisions": {todo[0].rule_id: "approve"},
    }, ensure_ascii=False), encoding="utf-8")

    b2 = pipeline.load_bundle(tmp_path)
    run2 = pipeline.analyze(b2, rs)
    got = {a.rule_id: a for a in run2.assessments}[todo[0].rule_id]
    assert got.verdict is Verdict.FAIL
    assert got.source.startswith("REVIEWER")


def test_review_reject_does_not_become_pass(tmp_path, rs):
    import json
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    todo = pipeline.pending_review(rs, pipeline.analyze(b, rs).assessments)
    (tmp_path / pipeline.REVIEW_FILE).write_text(json.dumps({
        "schema": pipeline.REVIEW_SCHEMA, "decisions": {todo[0].rule_id: "reject"},
    }, ensure_ascii=False), encoding="utf-8")
    run = pipeline.analyze(pipeline.load_bundle(tmp_path), rs)
    got = {a.rule_id: a for a in run.assessments}[todo[0].rule_id]
    assert got.verdict is Verdict.UNDETERMINED


def test_review_exemption_removes_items(tmp_path, rs):
    import json
    from engine.report import matrix
    (tmp_path / pipeline.REVIEW_FILE).write_text(json.dumps({
        "schema": pipeline.REVIEW_SCHEMA, "exemptions": ["무인민원발급기_표준규격"],
    }, ensure_ascii=False), encoding="utf-8")
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    assert "무인민원발급기_표준규격" in b.exemptions
    rep = matrix.build(rs, pipeline.analyze(b, rs).assessments,
                       device_id="T", product_type="중대형",
                       available_scopes=b.scopes, exemption_ids=b.exemptions)
    assert rep.summary["면제"] == 6


def test_review_rejects_wrong_schema(tmp_path):
    import json
    _mini_bundle(tmp_path)
    (tmp_path / pipeline.REVIEW_FILE).write_text(
        json.dumps({"schema": "bad/1"}), encoding="utf-8")
    with pytest.raises(pipeline.BundleError, match="형식이 다릅니다"):
        pipeline.load_bundle(tmp_path)


def test_printable_reports_are_self_contained(tmp_path, rs):
    """인쇄본은 외부 파일 없이 혼자 열려야 한다."""
    from engine.report import matrix, render
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    rep = matrix.build(rs, pipeline.analyze(b, rs).assessments,
                       device_id="T-001", product_type="중대형",
                       available_scopes=b.scopes)
    for html_doc in (render.render_owner(rep), render.render_authority(rep)):
        assert html_doc.startswith("<!doctype html>")
        assert "@page" in html_doc                    # 인쇄 규격
        assert "background: #fff" in html_doc         # 뷰어 테마에 안 휘둘린다
        assert "<link" not in html_doc                # 외부 자원 없음
        assert "<script" not in html_doc
        assert "법정 적합성 인증이 아닙니다" in html_doc


def test_report_evidence_skips_meta_fields():
    """'by: 구미계측' 같은 메타는 근거가 아니다."""
    from engine.report.render import _evidence_line
    assert _evidence_line({"by": "구미계측", "높이": 1285}) == "높이: 1285"
    assert _evidence_line({"명도대비": 2.13, "기준": 4.5}) == "명도대비: 2.13"
    assert _evidence_line({}) == ""


def test_owner_answer_is_korean_in_report(tmp_path, rs):
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs)
    a = {x.rule_id: x for x in run.assessments}["3.b"]
    assert a.evidence.measured["점주 응답"] == "아니오"


def test_gaps_tool_distinguishes_unbuilt_from_unconfigured():
    """'코드가 없다'와 '켜지 않았다'를 뭉뚱그리면 안 된다.

    뭉뚱그리는 순간, 다 만들어 놓은 경로가 영원히 '미개발'로 보고되고
    실제로 없는 것이 무엇인지 알 수 없게 된다.
    """
    from tools import gaps
    d = gaps.audit()
    assert len(d["items"]) == 40

    # L2 는 코드가 있고 파이프라인에 연결돼 있다
    assert d["l2"]["built"] and d["l2"]["wired"]
    # 그러니 judge·screen 항목은 '미개발'이 아니라 '미구성'으로 보고돼야 한다
    l2_states = {i["state"] for i in d["items"] if i["role"] in ("judge", "screen")
                 and i["track"] == "A_AI"}
    assert l2_states == {"L2 미구성"}, l2_states
    assert not any("미개발" in i["state"] for i in d["items"]), \
        [i["id"] for i in d["items"] if "미개발" in i["state"]]

    # 사람 손 경로는 모두 닫혔어야 한다
    assert all(p["impl"] for p in d["paths"]), \
        [p["name"] for p in d["paths"] if not p["impl"]]


# =============================================================================
# 작동부 검출 — 1.b / 1.c 자동화의 근거
# =============================================================================

def _panel_photo(tilt=0.18):
    """정답을 아는 조작부를 찍은 사진과 그 평면 환산을 만든다."""
    pytest.importorskip("cv2")
    from tools import simulate_capture as sim
    from engine.detect import text_regions
    panel, quads = sim.build_panel()
    panel, _ = sim.attach_marker(panel, quads)
    photo, _ = sim.photograph(panel, tilt=tilt)
    corners, _ = scale.detect_marker_corners(photo)
    ps = scale.build_plane_scale(corners, plane="control_panel",
                                 frame_width_px=photo.shape[1])
    return photo, ps, [text_regions.marker_exclusion(corners)]


@pytest.mark.parametrize("tilt", [0.05, 0.12, 0.18, 0.25])
def test_control_dimensions_measured_in_mm_plane(tilt):
    """치수는 mm 평면에서 재야 한다 — 사진 공간에서 재면 위반을 놓친다.

    사진(픽셀)의 축정렬 경계상자로 재던 때 기울기 0.18 에서
    10mm 버튼이 12.9mm 로 읽혀 12mm 기준을 **통과했다.**
    기울기가 커져도 정답을 되찾는지 본다.
    """
    from engine.detect import controls
    photo, ps, exclude = _panel_photo(tilt)
    det = controls.detect(photo, ps, exclude=exclude)

    assert len(det) == 4, [f"{c.w_mm:.1f}x{c.h_mm:.1f}" for c in det.candidates]
    got = sorted((round(c.w_mm, 1), round(c.h_mm, 1)) for c in det.candidates)
    truth = sorted([(10.0, 22.0), (14.0, 14.0), (16.0, 16.0), (16.0, 16.0)])
    for (gw, gh), (tw, th) in zip(got, truth):
        assert abs(gw - tw) <= 0.4, f"tilt={tilt} 가로 {gw} vs {tw}"
        assert abs(gh - th) <= 0.4, f"tilt={tilt} 세로 {gh} vs {th}"


@pytest.mark.parametrize("tilt", [0.05, 0.12, 0.18, 0.25])
def test_narrow_button_is_always_caught(tilt):
    """10mm 버튼은 어떤 기울기에서도 1.c 위반으로 잡혀야 한다."""
    from engine.detect import controls
    photo, ps, exclude = _panel_photo(tilt)
    det = controls.detect(photo, ps, exclude=exclude)
    m = geometry.check_mbr(det.boxes(), tolerance_mm=det.tolerance_mm)
    assert not m.passed
    assert len(m.violations) == 1, m.violations
    assert m.violations[0]["최소 변"] < geometry.SIDE_MIN_MM


def test_edge_bias_correction_shrinks_never_inflates():
    """경계 편의 교정은 반드시 줄이는 방향이어야 한다.

    부풀리면 작은 버튼이 기준을 통과해 위반을 놓친다.
    """
    from engine.detect import controls
    photo, ps, exclude = _panel_photo(0.12)
    det = controls.detect(photo, ps, exclude=exclude)
    assert controls.EDGE_BIAS_PX > 0
    assert controls.BIAS_CALIBRATED, "교정하지 않은 편의 상수를 쓰면 안 된다"
    # 어떤 후보도 정답보다 허용오차 이상 크게 나오면 안 된다
    truth_max = 22.0
    assert max(max(c.w_mm, c.h_mm) for c in det.candidates) <= truth_max + 0.4


def test_nested_contours_do_not_fake_zero_gaps():
    """중첩 윤곽을 IoU 로 지우면 안 된다 — 없는 1.b 위반이 무더기로 생긴다."""
    from engine.detect import controls
    photo, ps, exclude = _panel_photo(0.18)
    det = controls.detect(photo, ps, exclude=exclude)
    g = geometry.check_gaps(det.boxes(), surface="physical",
                            measurement_error_mm=det.tolerance_mm)
    assert g.min_gap_mm > 0.0, "0mm 간격은 같은 버튼을 두 번 센 것이다"
    assert g.passed, g.violations


def test_gap_recovers_true_spacing():
    """정답 2.0mm 간격을 허용오차 안으로 되찾는가."""
    from tools import simulate_capture as sim
    from engine.detect import controls
    photo, ps, exclude = _panel_photo(0.12)
    det = controls.detect(photo, ps, exclude=exclude)
    g = geometry.check_gaps(det.boxes(), surface="physical")
    assert abs(g.min_gap_mm - sim.GAP_TRUTH_MM) <= 0.3, g.min_gap_mm
    # 같은 간격이 터치스크린 기준(2.5mm)에서는 위반이다
    gt = geometry.check_gaps(det.boxes(), surface="touch")
    assert not gt.passed


def test_text_blocks_are_not_mistaken_for_controls():
    """화면 평면에서 문단이 작동부로 잡히면 줄 간격이 1.b 위반으로 쏟아진다."""
    from engine.detect import controls
    photo, ps, exclude = _panel_photo(0.12)
    full = controls.detect(photo, ps, exclude=exclude)
    # 검출된 후보를 전부 '문자 영역'이라고 알려 주면 하나도 남지 않아야 한다
    boxes = []
    for c in full.candidates:
        xs = [p[0] for p in c.hull_px]
        ys = [p[1] for p in c.hull_px]
        boxes.append((int(min(xs)) - 2, int(min(ys)) - 2,
                      int(max(xs) - min(xs)) + 4, int(max(ys) - min(ys)) + 4))
    filtered = controls.detect(photo, ps, exclude=exclude, text_boxes=boxes)
    assert len(filtered) == 0
    assert filtered.text_dropped > 0


def test_mbr_borderline_is_not_called_pass():
    """허용오차 안에 걸친 버튼을 적합이라 말하면 안 된다."""
    boxes = [geometry.Box(id="경계", x=0, y=0, w=12.1, h=20.0),
             geometry.Box(id="여유", x=40, y=0, w=20.0, h=20.0)]
    m = geometry.check_mbr(boxes, tolerance_mm=0.25)
    assert m.passed                      # 법적 위반은 아니고
    assert [b["작동부"] for b in m.borderline] == ["경계"]   # 실측으로 넘긴다
    assert "경계 근접 처리" in m.as_measured()


def test_pipeline_control_detection_reaches_suspected_fail(tmp_path, rs):
    """1.c 위반이 실제로 '위반 의심'까지 올라오는가.

    확신도 게이트(0.85)에 걸려 미판정으로 주저앉으면 기능이 없는 것과 같다.
    """
    cv2 = pytest.importorskip("cv2")
    import json
    from tools import simulate_capture as sim

    panel, quads = sim.build_panel()
    panel, _ = sim.attach_marker(panel, quads)
    photo, _ = sim.photograph(panel, tilt=0.18)
    (tmp_path / "S3").mkdir(parents=True)
    ok, enc = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    enc.tofile(str(tmp_path / "S3" / "S3-04_001.jpg"))

    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": pipeline.SCHEMA,
        "device": {"id": "C-001", "location": "테스트", "product_type": "중대형",
                   "scopes": [], "exemptions": []},
        "captured_at": "2026-08-22T00:00:00+09:00", "captured_by": "테스트",
        "shots": [{"set": "S3", "shot": "S3-04", "file": "S3/S3-04_001.jpg",
                   "marker_plane": "control_panel", "masked": True}],
    }, ensure_ascii=False), encoding="utf-8")

    run = pipeline.analyze(pipeline.load_bundle(tmp_path), rs)
    got = {a.rule_id: a for a in run.assessments}
    assert "1.c" in got, run.skipped
    assert got["1.c"].verdict is Verdict.SUSPECTED_FAIL
    assert got["1.c"].downgraded_from is Verdict.FAIL   # AI 는 부적합을 확정하지 않는다
    assert got["1.c"].evidence.measured["기준 미달 수"] >= 1


# =============================================================================
# L2 계층 — 모델 판단이 사정거리를 넘지 못하는지
#
# 이 절의 테스트가 깨지면 모델 의견이 법정 적합으로 인쇄되기 시작한다.
# =============================================================================

from engine import l2 as L2  # noqa: E402
from engine.l2 import judge as L2J, provider as L2P  # noqa: E402


def _judgment(verdict, conf=0.9):
    return L2P.Judgment(verdict=verdict, confidence=conf,
                        rationale="시험", cited=["a.jpg"], provider="test")


def test_l2_pass_never_becomes_compliant(rs):
    """모델의 '적합'은 적합이 아니다 — 룰이 both 라도 눌린다."""
    both = [rid for rid in rs.ids()
            if rs[rid]["ai_role"] == "judge" and rs[rid]["ai_verdict_scope"] == "both"]
    assert both, "both 인 judge 항목이 있어야 이 테스트가 의미를 가진다"
    for rid in both:
        a = L2J.to_assessment(rs[rid], _judgment(Verdict.PASS))
        assert a.verdict is Verdict.UNDETERMINED, f"{rid} 가 모델 판단으로 적합이 되었다"
        assert a.downgraded_from is Verdict.PASS
        assert a.next_owner == "검토자"


def test_l2_fail_stops_at_suspected(rs):
    """모델의 '위반'은 위반 의심까지다 — 부적합은 검토자만 만든다."""
    for rid in ("3.a", "10.b", "1.a", "1.h"):
        a = L2J.to_assessment(rs[rid], _judgment(Verdict.FAIL))
        assert a.verdict is Verdict.SUSPECTED_FAIL, rid
        assert a.downgraded_from is Verdict.FAIL


def test_l2_scope_ceiling_does_not_mutate_the_rule(rs):
    """사정거리를 누르는 것은 이번 판정뿐 — 룰 DB 는 그대로여야 한다."""
    rule = rs["3.a"]
    before = rule["ai_verdict_scope"]
    L2J.to_assessment(rule, _judgment(Verdict.PASS))
    assert rs["3.a"]["ai_verdict_scope"] == before


def test_l2_default_provider_makes_no_external_call(monkeypatch):
    """기본값은 '없음'이다 — 현장 사진이 저절로 밖으로 나가지 않는다."""
    monkeypatch.delenv("KFA_L2_PROVIDER", raising=False)
    p = L2.resolve()
    ok, why = p.available()
    assert not ok
    assert p.judge(L2P.JudgeRequest(rule_id="3.a", criterion="x", area="y")) is None
    assert not getattr(p, "sends_media_externally", False)


def test_l2_unavailable_provider_yields_no_assessments(rs, tmp_path):
    """공급자가 없으면 판정이 없다 — 빈 판정을 만들어 내지 않는다."""
    class Dead(L2P.Provider):
        name = "죽은공급자"

        def available(self):
            return False, "일부러 꺼 둠"

    run = L2J.run(rs, _stub_bundle(tmp_path), Dead())
    assert run.assessments == []
    assert run.judged == 0
    assert "일부러" in run.reason


def test_l2_call_failure_is_not_a_verdict(rs, tmp_path):
    """호출이 터지면 미판정이다. '판단 못 했음'이 '위반 없음'이 되면 안 된다."""
    class Exploding(L2P.Provider):
        name = "폭발"

        def available(self):
            return True, "ok"

        def judge(self, req):
            raise RuntimeError("네트워크 끊김")

    run = L2J.run(rs, _stub_bundle(tmp_path), Exploding())
    assert run.assessments == []
    assert run.failed and "네트워크 끊김" in run.failed[0]


def test_l2_declined_judgment_is_not_a_verdict(rs, tmp_path):
    """모델이 '판단불가'로 물러서면 판정을 만들지 않는다."""
    class Shy(L2P.Provider):
        name = "소극"

        def available(self):
            return True, "ok"

        def judge(self, req):
            return None

    run = L2J.run(rs, _stub_bundle(tmp_path), Shy())
    assert run.assessments == []
    assert run.declined > 0


def test_l2_low_confidence_judgment_is_discarded(rs, tmp_path):
    class Unsure(L2P.Provider):
        name = "확신없음"

        def available(self):
            return True, "ok"

        def judge(self, req):
            return _judgment(Verdict.FAIL, conf=0.2)

    run = L2J.run(rs, _stub_bundle(tmp_path), Unsure())
    assert run.assessments == []
    assert run.declined > 0


def test_l2_confidence_is_capped():
    """모델이 스스로 매긴 확신도는 계측값이 아니다."""
    j = _judgment(Verdict.FAIL, conf=0.999)
    assert j.confidence == L2P.CONFIDENCE_CEILING


@pytest.mark.parametrize("text", [
    "",
    "잘 모르겠습니다",
    '{"verdict": "적합"}',                      # confidence·rationale 없음
    '{"verdict": "아마도위반", "confidence": 0.9, "rationale": "x"}',
    '{"verdict": "판단불가", "confidence": 0.9, "rationale": "x"}',
    '{"verdict": "위반", "confidence": "높음", "rationale": "x"}',
])
def test_l2_response_parser_refuses_to_guess(text):
    """형식이 깨진 응답을 억지로 해석하면 그 순간 리포트가 거짓말을 시작한다."""
    from engine.l2.anthropic_provider import parse
    assert parse(text, provider="t", fallback_cited=[]) is None


def test_l2_response_parser_accepts_well_formed():
    from engine.l2.anthropic_provider import parse
    j = parse('{"verdict": "위반", "confidence": 0.8, "rationale": "초점 표시 없음",'
              ' "cited": ["S1-01.jpg"]}', provider="t", fallback_cited=["S1-01.jpg"])
    assert j is not None and j.verdict is Verdict.FAIL
    assert j.cited == ["S1-01.jpg"]


def test_l2_does_not_override_measured_values(rs):
    """계측값이 모델 의견보다 언제나 강하다."""
    measured = record_external(rs["3.a"], Verdict.PASS, source="MEASURE:테스트",
                               evidence=Evidence(criterion_id="3.a", measured={"x": 1}))
    opinion = L2J.to_assessment(rs["3.a"], _judgment(Verdict.FAIL))
    merged = L2J.merge([measured], [opinion])
    assert len(merged) == 1
    assert merged[0].source.startswith("MEASURE")


def test_l2_prompt_carries_no_ks_body_text(rs):
    """KS X 9211 은 절 번호만. 본문이 프롬프트에 실리면 안 된다."""
    from engine.l2 import prompt as P
    rule = rs["3.i"]
    req = P.request_from_rule(rule, "3. 시각", [Path("a.jpg")])
    text = P.SYSTEM + P.build(req)
    assert "KS" in text or not rule.get("ks_ref")
    for ref in rule.get("ks_ref") or []:
        assert ref in text                       # 절 번호는 있고
    assert "본문 미첨부" in text                  # 본문이 없다는 사실을 명시한다


def test_l2_targets_cover_every_judge_and_screen_item(rs, tmp_path):
    """judge·screen 항목 중 하나도 빠뜨리지 않는가."""
    expect = {rid for rid in rs.ids() if rs[rid]["ai_role"] in ("judge", "screen")}
    run = L2J.run(rs, _stub_bundle(tmp_path), L2.resolve("echo"))
    got = {a.rule_id for a in run.assessments}
    missing = expect - got
    # 촬영물이 없는 항목만 빠질 수 있다
    assert missing <= {"3.b"}, missing


def _stub_bundle(tmp_path):
    """모든 세트에 파일이 하나씩 있는 최소 번들."""
    import json
    shots = []
    for sid, ext in (("S1", "jpg"), ("S2", "mp4"), ("S3", "jpg"),
                     ("S4", "jpg"), ("S5", "m4a"), ("S6", "pdf")):
        (tmp_path / sid).mkdir(parents=True, exist_ok=True)
        f = tmp_path / sid / f"{sid}-01_001.{ext}"
        f.write_bytes(b"x")
        shots.append({"set": sid, "shot": f"{sid}-01", "file": f"{sid}/{f.name}",
                      "marker_plane": None, "masked": True})
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": pipeline.SCHEMA,
        "device": {"id": "L2-001", "location": "테스트", "product_type": "중대형",
                   "scopes": [], "exemptions": []},
        "captured_at": "2026-08-22T00:00:00+09:00", "captured_by": "테스트",
        "shots": shots,
    }, ensure_ascii=False), encoding="utf-8")
    return pipeline.load_bundle(tmp_path)


def test_consumed_sets_declaration_matches_reality(rs):
    """CONSUMED_SETS 선언이 실제 동작과 어긋나면 gaps 가 거짓말을 한다."""
    demo = Path(__file__).resolve().parent.parent / "assets" / "demo-bundle"
    if not (demo / "manifest.json").exists():
        pytest.skip("데모 번들이 없습니다 — make_demo_bundle 을 먼저 실행하세요")
    run = pipeline.analyze(pipeline.load_bundle(demo), rs)
    assert run.used_sets <= set(pipeline.CONSUMED_SETS), (
        f"선언에 없는 세트를 읽었다: {run.used_sets - set(pipeline.CONSUMED_SETS)}")
    assert set(pipeline.CONSUMED_SETS) <= run.used_sets | {"S4", "S6"}, (
        f"선언했는데 읽지 않은 세트: {set(pipeline.CONSUMED_SETS) - run.used_sets}")


def test_auto_rules_declaration_matches_reality(rs):
    """AUTO_RULES 선언이 실제 판정과 어긋나면 gaps 가 거짓말을 한다.

    실행 파일 안에는 .py 소스가 없다. 그래서 gaps 는 이 선언을 믿는다 —
    선언이 틀리면 진행 상황 보고 전체가 틀린다.
    """
    demo = Path(__file__).resolve().parent.parent / "assets" / "demo-bundle"
    if not (demo / "manifest.json").exists():
        pytest.skip("데모 번들이 없습니다 — make_demo_bundle 을 먼저 실행하세요")
    from engine import ocr as OCR
    # OCR 을 켜야 AUTO_RULES 전부(3.g 포함)가 실행된다.
    # 엔진이 없다는 이유로 선언이 검사되지 않으면 선언이 조용히 썩는다.
    bundle = pipeline.load_bundle(demo)
    bundle.measurements = {}  # 이 검사는 순수 AI 경로만 확인한다.
    run = pipeline.analyze(bundle, rs, ocr_provider=OCR.resolve("stub"))
    produced = {a.rule_id for a in run.assessments if a.source == "AI"}
    assert produced == set(pipeline.AUTO_RULES), (
        f"선언에만 있음: {set(pipeline.AUTO_RULES) - produced} / "
        f"실제에만 있음: {produced - set(pipeline.AUTO_RULES)}")


def test_gaps_tool_does_not_depend_on_reading_source_files(monkeypatch):
    """소스를 못 읽는 상황(실행 파일)에서도 같은 결론이 나와야 한다."""
    from tools import gaps
    before = gaps.audit()
    monkeypatch.setattr(gaps, "_read", lambda rel: "")   # 소스가 전혀 안 읽히는 척
    after = gaps.audit()

    assert [i["state"] for i in after["items"]] == [i["state"] for i in before["items"]]
    # app.js 를 못 읽으면 그 경로 하나만 빠지고 나머지는 그대로여야 한다
    lost = [p["name"] for p, q in zip(after["paths"], before["paths"])
            if p["impl"] != q["impl"]]
    assert lost == ["  └ 앱에서도 지정 가능"], lost


def test_report_discloses_l2_use(rs, tmp_path):
    """L2 를 쓴 진단은 리포트가 그 사실을 스스로 밝혀야 한다.

    어떤 판정이 계측에서 나왔고 어떤 것이 모델 판단에서 나왔는지 읽는 사람이
    구분하지 못하면, 리포트는 있는 그대로를 말하지 않는 것이 된다.
    """
    from engine.report import matrix as M, render
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs, l2_provider=L2.resolve("echo"))
    assert run.l2 is not None and run.l2.ran

    without = M.build(rs, run.assessments, device_id="T", product_type="중대형")
    with_l2 = M.build(rs, run.assessments, device_id="T", product_type="중대형",
                      l2_provider=run.l2.provider)
    assert "자동 선별" not in without.scope_note
    assert "자동 선별" in with_l2.scope_note
    assert run.l2.provider in with_l2.scope_note
    assert "계측값이 아니며" in with_l2.scope_note
    # 인쇄본에도 실린다
    assert "자동 선별" in render.render_authority(with_l2)


def test_l2_items_carry_provider_in_evidence(rs, tmp_path):
    """항목별 근거에 공급자가 남아야 검토자가 출처를 되짚을 수 있다."""
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs, l2_provider=L2.resolve("echo"))
    l2_items = [a for a in run.assessments
                if a.evidence and "공급자" in (a.evidence.measured or {})]
    assert l2_items
    for a in l2_items:
        assert a.evidence.measured["공급자"]
        assert "계측값이 아니" in a.evidence.measured["주의"]


# =============================================================================
# OCR 계층 — 3.g 문자 높이
# =============================================================================

from engine import ocr as OCR  # noqa: E402
from engine.detect import text_regions  # noqa: E402


def test_ocr_default_is_null_when_no_engine_installed(monkeypatch):
    """엔진이 없으면 없다고 말한다 — 빈 결과를 '문구 없음'으로 바꾸지 않는다."""
    monkeypatch.delenv("KFA_OCR_PROVIDER", raising=False)
    monkeypatch.setattr(OCR.provider, "_detect", lambda: "")
    p = OCR.resolve()
    ok, why = p.available()
    assert not ok
    assert "pip install" in why                   # 무엇을 하면 되는지 알려준다
    assert len(p.read(None)) == 0


def test_text_height_rule_absent_without_ocr(rs, tmp_path):
    """OCR 이 없으면 3.g 를 만들지 않고, 그 이유를 남긴다."""
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs, ocr_provider=OCR.NullProvider())
    assert not any(a.rule_id == "3.g" and a.source == "AI" for a in run.assessments)
    assert any(m.startswith("3.g —") for m in run.skipped), run.skipped


def test_text_height_rule_runs_with_ocr(rs, tmp_path):
    """OCR 이 있으면 3.g 가 실제로 판정까지 온다."""
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs, ocr_provider=OCR.resolve("stub"))
    got = [a for a in run.assessments if a.rule_id == "3.g" and a.source == "AI"]
    assert got, run.skipped
    m = got[0].evidence.measured
    assert m["검사 문구 수"] > 0
    assert "OCR 엔진" in m
    assert "캡션 제외" in m                        # 무엇을 안 했는지도 밝힌다


def test_uncalibrated_ocr_never_reaches_confident_verdict(rs, tmp_path):
    """상자 계수가 미교정이면 확신도 게이트에 걸려 검토자에게 가야 한다.

    엔진마다 상자 규약이 달라 교정 없이 믿으면 문자 높이가 통째로 틀어지고,
    7mm 짜리 글자가 7.25mm 기준을 통과해 **위반을 놓친다.**
    """
    b = pipeline.load_bundle(_mini_bundle(tmp_path))
    run = pipeline.analyze(b, rs, ocr_provider=OCR.resolve("stub"))
    a = next(x for x in run.assessments if x.rule_id == "3.g" and x.source == "AI")
    assert a.verdict is not Verdict.PASS
    assert a.evidence.confidence < 0.85
    assert "교정되지 않았습니다" in a.evidence.measured["주의"]


def test_box_factor_changes_measured_height():
    """상자 계수는 실제로 문자 높이를 바꾼다 — 장식용 인자가 아니다."""
    import numpy as np
    ps = scale.PlaneScale(
        plane="display", h_inv=np.eye(3) / 10.0,      # 10px = 1mm
        px_per_mm=10.0, skew=1.0, confidence=1.0)
    box = [(0, 0), (100, 0), (100, 100), (0, 100)]     # 10mm 상자
    a = text_height.measure_line("주문하기", box, ps, box_factor=1.0, box_calibrated=True)
    b = text_height.measure_line("주문하기", box, ps, box_factor=0.5, box_calibrated=True)
    assert abs(a.char_height_mm - 2 * b.char_height_mm) < 1e-6
    assert a.ocr_calibrated and b.ocr_calibrated


def test_ocr_calibration_round_trip(tmp_path, monkeypatch):
    """교정 도구가 쓴 값을 판정기가 그대로 읽는가."""
    from tools import calibrate_ocr
    # 쓰는 쪽과 읽는 쪽이 같은 폴더를 봐야 한다.
    # calibrate_ocr 은 output_dir 을 모듈 최상단에서 가져오므로 따로 갈아끼운다.
    monkeypatch.setattr("engine.paths.output_dir", lambda: tmp_path)
    monkeypatch.setattr(calibrate_ocr, "output_dir", lambda: tmp_path)

    rec = {"ok": True, "factor": 0.77, "spread": 0.02, "n": 12}
    path = calibrate_ocr.save("테스트엔진", rec)
    assert path.exists()

    factor, calibrated, meta = text_height.load_ocr_calibration("테스트엔진")
    assert calibrated and abs(factor - 0.77) < 1e-9 and meta["n"] == 12

    # 기록이 없는 엔진은 교정된 척하지 않는다
    factor2, calibrated2, _ = text_height.load_ocr_calibration("모르는엔진")
    assert not calibrated2 and factor2 == text_height.OCR_BOX_FACTOR


def test_ocr_calibration_ignores_corrupt_file(tmp_path, monkeypatch):
    """교정 파일이 깨져 있으면 미교정으로 되돌아간다 — 터지지 않는다."""
    monkeypatch.setattr("engine.paths.output_dir", lambda: tmp_path)
    (tmp_path / text_height.OCR_CALIBRATION_FILE).write_text("{망가짐", encoding="utf-8")
    factor, calibrated, _ = text_height.load_ocr_calibration("아무엔진")
    assert not calibrated and factor == text_height.OCR_BOX_FACTOR


def test_ocr_calibration_refuses_with_too_few_samples():
    """표본이 모자라면 계수를 만들어 내지 않는다."""
    from tools import calibrate_ocr

    class Blind(OCR.Provider):
        name = "blind"

        def available(self):
            return True, "ok"

        def read(self, image_bgr):
            return OCR.OcrResult(engine="blind")

    font = __import__("tools.calibrate_text_height", fromlist=["find_font"]).find_font()
    if font is None:
        pytest.skip("한글 폰트가 없습니다")
    rec = calibrate_ocr.calibrate(Blind(), font)
    assert not rec["ok"] and rec["n"] == 0


def test_unknown_ocr_provider_is_rejected():
    with pytest.raises(ValueError):
        OCR.resolve("무슨엔진")


# =============================================================================
# 망가진 입력 — 진단 전체가 죽지 않아야 한다
#
# 현장에서 오는 번들이 무결하다고 가정하지 않는다. 사진 한 장이 잘려 있거나
# manifest 한 줄이 틀렸다고 진단이 통째로 멈추면, 그날 현장을 다시 나가야 한다.
# =============================================================================

def _write_manifest(tmp_path, manifest):
    import json
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return tmp_path


_BASE_MANIFEST = {
    "schema": "kfa.capture/1",
    "device": {"id": "X", "product_type": "중대형"},
}


@pytest.mark.parametrize("manifest,fragment", [
    ([], "객체"),                                                  # 최상위가 배열
    ({**_BASE_MANIFEST, "shots": "S1"}, "배열"),
    ({**_BASE_MANIFEST, "shots": [{"set": "S1"}]}, "file"),        # file 누락
    ({**_BASE_MANIFEST, "owner_answers": ["3.b"]}, "객체"),
    ({**_BASE_MANIFEST, "measurements": [1, 2]}, "객체"),
    ({**_BASE_MANIFEST, "device": {"id": "X", "product_type": "중대형",
                                   "scopes": "물건 투입"}}, "배열"),
    ({**_BASE_MANIFEST, "device": "문자열"}, "객체"),
])
def test_malformed_manifest_gives_readable_error(tmp_path, manifest, fragment):
    """형식이 틀리면 TypeError 가 아니라 무엇이 틀렸는지 말해야 한다."""
    with pytest.raises(pipeline.BundleError) as exc:
        pipeline.load_bundle(_write_manifest(tmp_path, manifest))
    assert fragment in str(exc.value), str(exc.value)


def test_null_shots_is_read_as_no_shots(tmp_path):
    """비어 있는 것과 잘못된 것은 다르다 — null 은 '없음'으로 읽는다."""
    b = pipeline.load_bundle(_write_manifest(tmp_path, {**_BASE_MANIFEST, "shots": None}))
    assert b.shots == []


def test_malformed_review_gives_readable_error(tmp_path):
    _write_manifest(tmp_path, _BASE_MANIFEST)
    (tmp_path / "review.json").write_text(
        '{"schema": "kfa.review/1", "decisions": [1, 2]}', encoding="utf-8")
    with pytest.raises(pipeline.BundleError) as exc:
        pipeline.load_bundle(tmp_path)
    assert "객체" in str(exc.value)


@pytest.mark.parametrize("size", [1, 2, 3])
def test_tiny_image_does_not_crash_detection(size):
    """잘린 사진 한 장에 진단 전체가 죽으면 안 된다 (MSER 은 3x3 미만에서 예외)."""
    pytest.importorskip("cv2")
    import numpy as np
    from engine.detect import controls as C
    img = np.full((size, size, 3), 128, np.uint8)
    assert len(text_regions.detect(img)) == 0
    assert len(C.detect(img, None)) == 0


def test_l2_provider_returning_wrong_type_is_rejected(rs, tmp_path):
    """공급자는 남이 만들 수 있다. 규약 위반이 판정으로 새어들면 안 된다."""
    class Garbage(L2.Provider):
        name = "쓰레기"

        def available(self):
            return True, "ok"

        def judge(self, req):
            return "Judgment 가 아닌 문자열"

    run = L2J.run(rs, _stub_bundle(tmp_path), Garbage())
    assert run.assessments == []
    assert run.failed and "Judgment" in run.failed[0]


def test_l2_provider_returning_impossible_verdict_is_rejected(rs, tmp_path):
    """공급자가 '위반 의심'을 직접 내려 하면 막는다 — 그건 엔진이 정하는 것이다."""
    class Sneaky(L2.Provider):
        name = "월권"

        def available(self):
            return True, "ok"

        def judge(self, req):
            return L2P.Judgment(verdict=Verdict.SUSPECTED_FAIL, confidence=0.9,
                                rationale="x")

    run = L2J.run(rs, _stub_bundle(tmp_path), Sneaky())
    assert run.assessments == []
    assert run.failed


def test_noisy_photo_is_refused_not_ground_through():
    """잡음이 심한 사진은 갈아 넣지 말고 못 읽었다고 말한다.

    겹침 제거가 O(n²) 이던 때, 잡음 600x800 사진 한 장이 글자 후보 4만 개를 만들어
    **108초** 를 잡아먹었다. 현장 사진은 12MP 라 진단 한 건이 사실상 멈췄다.
    지금은 후보 상한을 넘기면 즉시 물러난다.
    """
    pytest.importorskip("cv2")
    import time
    import numpy as np
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)

    t0 = time.time()
    det = text_regions.detect(img)
    elapsed = time.time() - t0

    assert elapsed < 10.0, f"{elapsed:.1f}초 — 겹침 제거가 다시 O(n²) 가 됐다"
    assert not det.reliable
    assert det.blocks == []                       # 신뢰 못 하면 블록을 내지 않는다
    assert det.glyph_candidates > text_regions.MAX_GLYPH_CANDIDATES
    assert "다시 촬영" in det.coverage_note        # 무엇을 하면 되는지 알려준다


def test_dedupe_is_not_quadratic():
    """겹치지 않는 상자가 많아도 선형에 가깝게 끝나야 한다."""
    import time
    def boxes(n):
        return [(i % 100 * 12, i // 100 * 12, 10, 10) for i in range(n)]
    t1 = time.time(); text_regions._dedupe(boxes(1000)); d1 = time.time() - t1
    t2 = time.time(); text_regions._dedupe(boxes(4000)); d2 = time.time() - t2
    # 4배 늘렸을 때 제곱이면 16배가 된다. 넉넉히 잡아도 8배를 넘으면 안 된다.
    assert d2 < max(0.5, d1 * 8), f"1000개 {d1:.3f}초 → 4000개 {d2:.3f}초"


def test_unreliable_photo_note_reaches_the_operator(tmp_path, rs):
    """못 읽은 사진이 있으면 '위반 없음'처럼 보이게 두지 않는다."""
    cv2 = pytest.importorskip("cv2")
    import json
    import numpy as np
    rng = np.random.default_rng(1)
    (tmp_path / "S1").mkdir()
    noisy = rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    enc.tofile(str(tmp_path / "S1" / "S1-01_001.jpg"))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": pipeline.SCHEMA,
        "device": {"id": "N-1", "product_type": "중대형"},
        "shots": [{"set": "S1", "shot": "S1-01", "file": "S1/S1-01_001.jpg",
                   "marker_plane": "display", "masked": True}],
    }, ensure_ascii=False), encoding="utf-8")

    run = pipeline.analyze(pipeline.load_bundle(tmp_path), rs)
    notes = [p.text_note for p in run.photos if p.text_note]
    assert notes and "신뢰성 있게" in notes[0]


def test_ocr_marker_card_text_is_not_a_violation(rs):
    """마커 카드의 인쇄 글씨가 3.g 위반으로 잡히면 안 된다.

    카드에는 'BFK-MARK-A', '40mm' 같은 작은 글씨가 있다. 빼지 않으면
    **우리가 붙인 카드 때문에 키오스크가 부적합**이 된다.
    명도 대비에서 한 번 겪은 함정이라 OCR 경로에도 같은 제외가 걸려야 한다.
    """
    demo = Path(__file__).resolve().parent.parent / "assets" / "demo-bundle"
    if not (demo / "manifest.json").exists():
        pytest.skip("데모 번들이 없습니다")
    b = pipeline.load_bundle(demo)
    b.measurements.pop("3.g", None)          # 실측이 있으면 AI 판정을 덮는다
    run = pipeline.analyze(b, rs, ocr_provider=OCR.resolve("stub"))
    got = [a for a in run.assessments if a.rule_id == "3.g" and a.source == "AI"]
    if not got:
        pytest.skip("이 환경에서는 3.g 가 생성되지 않았습니다")
    m = got[0].evidence.measured
    assert "마커 카드 제외" in m
    for text in m.get("미달 문구", []):
        assert "MARK" not in text.upper(), f"마커 카드 글씨가 위반으로 잡혔다: {text}"


def test_exclusion_filter_drops_boxes_inside_marker_card():
    """제외 영역 판정 자체의 동작."""
    from engine.pipeline import _in_exclusion
    card = [(100, 100, 200, 120)]                       # x,y,w,h
    inside = [(120, 110), (180, 110), (180, 150), (120, 150)]
    outside = [(400, 400), (460, 400), (460, 430), (400, 430)]
    assert _in_exclusion(inside, card)
    assert not _in_exclusion(outside, card)
    assert not _in_exclusion(inside, [])                # 제외 영역이 없으면 통과


def test_korean_probe_reports_engines_that_cannot_read_hangul():
    """한글을 못 읽는 엔진을 '문구가 없다'로 착각하면 안 된다.

    국내 키오스크는 대부분 한글이다. 못 읽는 엔진이 낸 '위반 없음'은
    '위반이 없다'가 아니라 '읽지를 못했다'는 뜻이다.
    """
    p = OCR.resolve("stub")
    read, total = p.probe_korean()
    assert total > 0
    # 시험용 공급자는 글자를 읽지 않으므로 한글도 못 읽는다 — 그렇게 보고돼야 한다
    assert read == 0


def test_calibration_discards_partial_reads():
    """부분 인식 표본을 쓰면 계수가 튄다 — 실측에서 산포가 0.34 까지 벌어졌다."""
    from tools.calibrate_ocr import _same_text
    assert _same_text("ORDER", "ORDER")
    assert _same_text("order now", "Order  now")        # 공백·대소문자는 무시
    assert not _same_text("now", "Order now")           # 부분 인식은 버린다
    assert not _same_text("", "PAY")


# =============================================================================
# 한글 OCR — 국내 키오스크의 실제 조건
# =============================================================================

def _korean_engine():
    """한글을 읽는 엔진. 없으면 테스트를 건너뛴다."""
    pytest.importorskip("rapidocr")
    p = OCR.resolve("rapidocr")
    ok, why = p.available()
    if not ok:
        pytest.skip(f"rapidocr 를 쓸 수 없습니다: {why}")
    return p


def test_detect_prefers_engine_that_reads_korean(monkeypatch):
    """자동 탐지는 한글을 읽는 엔진부터 골라야 한다.

    가벼운 것부터 고르면 한글을 못 읽는 구 엔진이 먼저 잡히고,
    국내 키오스크에서 3.g 가 통째로 무의미해진다.
    """
    import importlib.util as u
    real = u.find_spec

    def only(*names):
        return lambda n: real(n) if n in names else None

    monkeypatch.setattr(u, "find_spec", only("rapidocr", "rapidocr_onnxruntime"))
    assert OCR.provider._detect() == "rapidocr"          # 둘 다 있으면 한글 되는 쪽

    monkeypatch.setattr(u, "find_spec", only("rapidocr_onnxruntime"))
    assert OCR.provider._detect() == "rapidocr-legacy"   # 구 엔진뿐이면 그것


def test_calibration_key_includes_model_and_language():
    """모델이 바뀌면 교정 키도 바뀌어야 한다.

    실제로 겪은 일이다. 구 엔진의 계수는 1.4764(상자가 잉크보다 작다),
    새 엔진은 0.8252(상자가 잉크보다 크다) — 방향이 정반대다.
    이름만으로 키를 삼았다면 새 엔진이 구 계수를 물려받아
    문자 높이를 79% 과대평가하고 **실제 위반을 놓쳤을 것이다.**
    """
    pytest.importorskip("rapidocr")
    a = OCR.resolve("rapidocr")
    legacy = OCR.resolve("rapidocr-legacy")
    assert a.calibration_key != legacy.calibration_key
    assert a.REC_VERSION in a.calibration_key
    assert a.lang in a.calibration_key
    # 언어를 바꾸면 키도 달라진다
    from engine.ocr.engines import RapidOcrProvider
    assert RapidOcrProvider(lang="en").calibration_key != a.calibration_key


def test_korean_engine_actually_reads_hangul():
    """설치된 엔진이 정말로 한글을 읽는가 — 이름이 아니라 결과로 확인한다."""
    p = _korean_engine()
    read, total = p.probe_korean()
    assert total > 0
    assert read == total, f"한글 {read}/{total} — 이 엔진으로는 3.g 가 무의미하다"


def test_korean_text_height_end_to_end(rs, tmp_path):
    """한글 화면에서 3.g 가 실제 위반을 잡아내는가.

    합성 화면에 문자 높이를 알고 그려 넣고, 마커를 붙여 비스듬히 찍은 뒤
    엔진이 기준 미달 문구만 골라내는지 본다.
    측정 오차는 **과소평가 방향**이어야 한다 — 과대평가하면 위반을 놓친다.
    """
    cv2 = pytest.importorskip("cv2")
    import json
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from tools import simulate_capture as sim

    p = _korean_engine()
    font_path = Path(r"C:\Windows\Fonts\malgun.ttf")
    if not font_path.exists():
        pytest.skip("맑은 고딕이 없습니다")

    # 폰트 크기별로 '진짜 잉크 높이'를 먼저 재 둔다 — 추정한 정답은 정답이 아니다
    lines = [("주문하기", 135), ("영수증 출력", 56)]
    truth = {}
    W, H = sim.mm(sim.PANEL_W_MM), sim.mm(sim.PANEL_H_MM)
    img = Image.new("RGB", (W, H), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    y = sim.mm(10)
    for text, size in lines:
        f = ImageFont.truetype(str(font_path), size)
        probe = Image.new("L", (size * (len(text) + 2), size * 3), 255)
        ImageDraw.Draw(probe).text((size // 2, size // 2), text, font=f, fill=0)
        bb = probe.point(lambda v: 255 if v < 128 else 0).getbbox()
        truth[text.replace(" ", "")] = (bb[3] - bb[1]) / sim.PPM
        draw.text((sim.mm(10), y), text, font=f, fill=(20, 20, 20))
        y += int(size * 1.45)

    panel = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    panel, _ = sim.attach_marker(panel, {})
    photo, _ = sim.photograph(panel, tilt=0.06)

    (tmp_path / "S1").mkdir()
    ok, enc = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    enc.tofile(str(tmp_path / "S1" / "S1-01_001.jpg"))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": pipeline.SCHEMA,
        "device": {"id": "KOR-1", "product_type": "중대형"},
        "shots": [{"set": "S1", "shot": "S1-01", "file": "S1/S1-01_001.jpg",
                   "marker_plane": "display", "masked": True}],
    }, ensure_ascii=False), encoding="utf-8")

    run = pipeline.analyze(pipeline.load_bundle(tmp_path), rs, ocr_provider=p)
    got = [a for a in run.assessments if a.rule_id == "3.g" and a.source == "AI"]
    assert got, run.skipped
    m = got[0].evidence.measured

    assert m["검사 문구 수"] >= 2, m

    # 6.6mm 짜리 '영수증 출력' 은 7.25mm 기준 미달이다.
    # 계측이 흔들려도(실측 −9%~+3%) **조용히 통과시키면 안 된다** —
    # 위반으로 올리거나, 최소한 '경계 근접'으로 실측에 넘겨야 한다.
    flagged = list(m.get("미달 문구", [])) + list(m.get("경계 근접 문구", []))
    assert any("영수증" in t for t in flagged), (
        f"기준 미달 문구가 조용히 통과했다: 미달={m.get('미달 문구')} "
        f"경계={m.get('경계 근접 문구')}")

    # 15.9mm 짜리 '주문하기' 를 위반으로 올리면 안 된다
    assert not any("주문하기" in t for t in m.get("미달 문구", [])), m["미달 문구"]
    # 한글을 읽었으므로 '읽지 못함' 경고가 붙으면 안 된다
    assert "한글 인식" not in m, m.get("한글 인식")

    # 계측 정확도 — 실측 범위 안에 있는가
    smallest_truth = min(truth.values())
    err = (m["최소 문자 높이"] - smallest_truth) / smallest_truth
    assert abs(err) <= 0.12, (
        f"측정 {m['최소 문자 높이']} vs 참값 {smallest_truth:.2f} = {err:+.1%} — "
        "±12% 를 넘으면 계수 교정이 어긋난 것이다")


# =============================================================================
# 자기 검사 — 우리 화면이 우리 기준을 통과하는가
#
# 합동 평가에서 드러난 것: 이 도구는 별표5 3.i(4.5:1)로 키오스크를 판정하면서
# 정작 자기 화면이 미달이었다. '이번 진단 대상' 숫자가 다크 모드에서 1.41:1,
# 점주용 리포트의 주의색이 3.79:1. 크게 키운 글씨가 미달을 가려 준 탓에
# 아무도 눈치채지 못했다. 같은 일이 다시 생기지 않게 판정 엔진으로 우리를 검사한다.
# =============================================================================

def test_our_own_ui_passes_our_own_contrast_rule():
    """수집 앱과 리포트의 모든 색 조합이 4.5:1 을 넘는가 — 라이트·다크 모두."""
    from tools import check_own_ui
    d = check_own_ui.audit()
    failed = [f"{r['theme']} · {r['where']} = "
              f"{r['ratio']:.2f}:1" if r["ratio"] else f"{r['theme']} · {r['where']} = {r['note']}"
              for r in d["failed"]]
    assert not failed, (
        "우리가 키오스크에 요구하는 기준을 우리가 지키지 못하고 있다:\n  "
        + "\n  ".join(failed))


def test_owner_report_has_no_tiny_text():
    """점주용 리포트에 9pt 미만 글자가 없는가.

    점주는 60~80대인 경우가 흔하다. 합동 평가에서 10px 글씨가 34곳 나왔고
    80대 참여자는 한 문장을 소리 내어 읽지 못했다.
    """
    from tools import check_own_ui
    d = check_own_ui.audit()
    assert not d["small_pt"], (
        f"{check_own_ui.MIN_FONT_PT}pt 미만: "
        f"{', '.join(f'{v:g}pt' for v in d['small_pt'])}")


def test_dark_mode_is_checked_as_its_own_theme():
    """다크 모드를 별도 테마로 실제 검사하는가.

    `--deep` 이 다크 블록에 없어 짙은 남색 글씨가 짙은 회색 배경에 얹혀
    1.41:1 이 됐다. 라이트만 검사했다면 영영 못 봤을 결함이다.

    반대 방향 실수도 있었다 — 배경 토큰(`--deep-ink`)을 글자색처럼 밝게 바꿨더니
    흰 글씨가 밝은 바탕에 얹혀 1.39:1 이 됐다. 그래서 **배경으로 쓰이는 조합도**
    검사 목록에 들어 있어야 한다.

    '재정의되지 않은 토큰'을 실패로 삼지 않는 이유: 두 테마에서 모두 통과하는
    색이면 재정의할 필요가 없다. 정확한 보증은 쌍 검사가 한다.
    """
    from tools import check_own_ui
    d = check_own_ui.audit()
    themes = {r["theme"] for r in d["rows"]}
    assert "앱 · 라이트" in themes and "앱 · 다크" in themes, themes
    assert any(p[1] == "--deep-ink" for p in check_own_ui.APP_PAIRS), \
        "배경으로 쓰이는 조합이 빠지면 글자색만 고치다 배경을 망친다"


def test_self_check_actually_catches_a_bad_pair(monkeypatch):
    """검사기가 진짜로 잡아내는가 — 통과만 하는 검사는 검사가 아니다."""
    from tools import check_own_ui
    monkeypatch.setattr(check_own_ui, "REPORT_PAIRS",
                        [("#BBBBBB", "#FFFFFF", "일부러 미달")])
    d = check_own_ui.audit()
    assert any(r["where"] == "일부러 미달" and not r["ok"] for r in d["rows"])


def test_hidden_overlay_is_actually_hidden():
    """hidden 속성이 CSS 에 밀려 무력화된 요소가 없는가.

    실제로 겪은 일이다. `#mask-overlay { display: flex }` 가 브라우저 기본 스타일의
    `[hidden] { display: none }` 을 이겨서, hidden 이 붙은 마스킹 오버레이가
    **화면 전체를 덮고 z-index:100 으로 모든 터치를 가로챘다.**
    기기 ID 칸을 탭하면 mask-canvas 가 잡혔다 — 폰에서는 앱을 쓸 수 없었다.

    DOM 만 읽는 검사로는 안 보인다. 요소는 멀쩡히 거기 있기 때문이다.
    실제로 눌러 봐야 드러나는 종류의 결함이라, 규칙 자체를 검사한다.
    """
    from tools import check_own_ui
    d = check_own_ui.audit()
    assert not d["hidden_broken"], (
        "hidden 이 무력화된 요소: " + ", ".join(d["hidden_broken"])
        + " — 화면을 덮고 터치를 가로챕니다")


def test_hidden_overlay_checker_catches_the_real_bug():
    """검사기가 그 버그를 진짜로 잡는가 — 통과만 하는 검사는 검사가 아니다."""
    from tools.check_own_ui import check_hidden_overlays
    html = '<div id="mask-overlay" hidden>'
    broken = "#mask-overlay { position: fixed; display: flex; }"
    fixed = broken + "\n#mask-overlay[hidden] { display: none; }"
    assert check_hidden_overlays(html, broken) == ["mask-overlay"]
    assert check_hidden_overlays(html, fixed) == []


# =============================================================================
# 합동 평가 후속 — 점주가 읽을 수 있는 리포트
# =============================================================================

def test_every_rule_has_plain_language(rs):
    """40항목 모두에 쉬운 말이 있는가.

    점주는 고시 용어를 읽지 못한다. 합동 평가에서 '개인청취장치',
    '촉각 표시' 에서 참여자 세 명 모두 멈췄고, '최소경계상자(MBR)' 는
    무엇을 하라는 건지 알 수 없다는 답이 돌아왔다.
    """
    missing = [rid for rid in rs.ids() if not (rs[rid].get("plain") or "").strip()]
    assert not missing, f"쉬운 말이 없는 항목: {missing}"


def test_plain_language_avoids_legal_jargon(rs):
    """쉬운 말에 고시 전용어가 남아 있지 않은가."""
    jargon = ["개인청취장치", "최소경계상자", "MBR", "명도 대비", "촉각 표시",
              "무인정보단말기", "작동부"]
    offenders = []
    for rid in rs.ids():
        plain = rs[rid].get("plain") or ""
        for word in jargon:
            if word in plain:
                offenders.append(f"{rid}: '{word}'")
    assert not offenders, "쉬운 말에 남은 전용어: " + ", ".join(offenders)


def test_owner_report_uses_plain_language(rs):
    """리포트 A 의 실행 카드가 쉬운 말을 쓰는가 — 원문이 아니라."""
    from engine.report import matrix as M
    a = Assessment("1.c", Verdict.FAIL,
                   evidence=Evidence(criterion_id="1.c", measured={"최소 변": 10.0}))
    b = M.build(rs, [a], device_id="T", product_type="중대형")
    card = next(c for c in b.cards if c.rule_id == "1.c")
    assert card.title == rs["1.c"]["plain"]
    assert "최소경계상자" not in card.title
    # 원문은 담당 기관용 매트릭스에 남아야 한다
    assert rs["1.c"]["criterion"], "원문이 사라지면 안 된다"


def test_related_rule_note_changes_with_the_pair(rs):
    """짝 항목의 판정에 따라 안내가 달라지는가.

    '작지만 키울 수 있음'과 '작고 키울 수도 없음'은 점주가 할 일이 다르다.
    """
    from engine.report import matrix as M

    def note(other_verdict):
        aa = [Assessment("3.g", Verdict.FAIL,
                         evidence=Evidence(criterion_id="3.g", measured={"최소 문자 높이": 5.0}))]
        if other_verdict is not None:
            aa.append(Assessment("3.h", other_verdict,
                                 evidence=Evidence(criterion_id="3.h", measured={"x": 1})))
        b = M.build(rs, aa, device_id="T", product_type="중대형")
        return next(c for c in b.cards if c.rule_id == "3.g").related

    assert "별도로 확인" in note(Verdict.PASS)
    assert "급하지" not in note(Verdict.PASS)
    assert "우선순위가 올라갑니다" in note(Verdict.FAIL)
    assert "아직 판정되지 않았" in note(None)


# =============================================================================
# 사용성 주의 — 기준은 통과했지만 쓰기 어려운 것
# =============================================================================

def test_usability_caution_never_changes_a_verdict(rs):
    """참고 메모가 판정을 건드리면 안 된다. 별표5 에 그런 등급은 없다."""
    from engine import usability
    from engine.report import matrix as M
    a = Assessment("9.a", Verdict.PASS,
                   evidence=Evidence(criterion_id="9.a", measured={"결제 모듈 높이": 1180.0}),
                   source="MEASURE:테스트")
    b = M.build(rs, [a], device_id="T", product_type="중대형",
                advisory=R.load_advisory())
    row = next(r for r in b.matrix if r["항목"] == "9.a")
    assert row["판정"] == "적합", "주의가 붙어도 적합은 적합이다"
    assert b.cautions and b.cautions[0].rule_id == "9.a"
    assert "별표5 판정이 아닙니다" in usability.NOTE


def test_usability_caution_only_looks_at_passing_items(rs):
    """부적합은 이미 실행 카드로 나간다 — 여기서 또 말하면 두 번 실린다."""
    from engine import usability
    fail = Assessment("9.a", Verdict.FAIL,
                      evidence=Evidence(criterion_id="9.a", measured={"결제 모듈 높이": 1285.0}))
    assert usability.find([fail], R.load_advisory()) == []


@pytest.mark.parametrize("value,expect", [
    (1180.0, True),    # 상한 1,220 에 가깝다 — 어깨 굴곡 제한이면 부담
    (850.0, False),    # 여유 있는 높이
    (450.0, True),     # 하한 근처 — 허리를 굽혀야 한다
])
def test_reach_height_caution_band(value, expect):
    from engine import usability
    a = Assessment("9.a", Verdict.PASS,
                   evidence=Evidence(criterion_id="9.a", measured={"결제 모듈 높이": value}))
    assert bool(usability.find([a], R.load_advisory())) is expect


def test_advisory_is_not_mixed_into_annex5(rs):
    """별표5가 아닌 숫자가 별표5 파일에 섞이지 않았는가.

    섞이면 나중에 누군가 '이것도 고시 기준'이라고 읽는다.
    """
    adv = R.load_advisory()
    assert "별표5 아님" in adv["meta"]["legal_status"]
    for rid in rs.ids():
        assert "caution" not in rs[rid], f"{rid} 에 참고 기준이 섞여 있다"


def test_advisory_missing_file_is_not_fatal(tmp_path):
    """참고 정보 파일이 없다고 진단이 멈추면 안 된다."""
    assert R.load_advisory(tmp_path / "없는파일.yaml") == {"items": []}


# ─────────────────────────── 조작판 · 쉬운 모드 ───────────────────────────
#
# 화면이 하는 일은 결국 셋이다 — 번들을 찾고, 명령을 걸고, 결과를 연다.
# 그 셋에 각각 조용히 틀릴 수 있는 구멍이 있어서 여기에 못을 박아 둔다.

def _make_bundle(root, device="TEST-01", n=2):
    import json
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({
        "schema": "kfa.capture/1",
        "device": {"id": device, "location": "테스트 매장", "product_type": "일반",
                   "scopes": [], "exemptions": []},
        "shots": [{"set": "S1", "shot": f"S1-{i:02d}", "file": f"S1/{i}.jpg"}
                  for i in range(n)],
        "owner_answers": {}, "measurements": {},
    }, ensure_ascii=False), encoding="utf-8")
    return root


def test_easy_finds_bundle_folder(tmp_path, monkeypatch):
    from tools import easy
    b = _make_bundle(tmp_path / "촬영_20260824")
    monkeypatch.setattr(easy, "_search_roots", lambda: [tmp_path])
    found = easy.find_bundles()
    assert [f.path for f in found] == [b]
    assert "TEST-01" in found[0].label and "촬영 2건" in found[0].label


def test_easy_finds_bundle_zip(tmp_path, monkeypatch):
    """폰이 내보내는 것은 ZIP 이다. 풀라고 시키지 않고 그대로 알아봐야 한다."""
    import zipfile
    from tools import easy
    src = _make_bundle(tmp_path / "src")
    z = tmp_path / "TESTDEV_20260824.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(src / "manifest.json", "manifest.json")
        zf.writestr("S1/0.jpg", b"\xff\xd8\xff")
    monkeypatch.setattr(easy, "_search_roots", lambda: [tmp_path])
    found = [f for f in easy.find_bundles() if f.is_zip]
    assert len(found) == 1 and found[0].path == z


def test_easy_ignores_unrelated_zip(tmp_path, monkeypatch):
    """다운로드 폴더에는 우리와 무관한 ZIP 이 훨씬 많다. 열지 못해도 죽지 않는다."""
    import zipfile
    from tools import easy
    with zipfile.ZipFile(tmp_path / "사진첩.zip", "w") as zf:
        zf.writestr("a.txt", "hello")
    (tmp_path / "깨진파일.zip").write_bytes(b"not a zip at all")
    monkeypatch.setattr(easy, "_search_roots", lambda: [tmp_path])
    assert easy.find_bundles() == []


def test_easy_zip_extract_rejects_path_escape(tmp_path, monkeypatch):
    """ZIP 안의 ../ 하나면 실행 파일 옆의 파일을 덮어쓸 수 있다."""
    import zipfile
    from engine import paths
    from tools import easy
    z = tmp_path / "악성.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("../탈출.txt", "escaped")
    monkeypatch.setattr(paths, "output_dir", lambda: tmp_path / "out")
    monkeypatch.setattr(easy, "output_dir", lambda: tmp_path / "out")
    with pytest.raises(easy.UnsafeZip):
        easy.extract(z)
    assert not (tmp_path / "탈출.txt").exists()


def test_panel_only_runs_known_jobs():
    """화면에서 임의 명령이 만들어지지 않는다 — 실행 가능한 것은 목록뿐이다."""
    from tools import panel
    job, why = panel.start_job("rm -rf /")
    assert job is None and "알 수 없는" in why
    job, why = panel.start_job("ingest")            # 폴더 없이는 안 만든다
    assert job is None and "고르세요" in why
    for key, spec in panel.JOBS.items():
        if spec["steps"] is None:
            continue
        for argv in spec["steps"]:
            assert argv and all(isinstance(x, str) for x in argv)


def test_panel_open_targets_are_fixed():
    """화면이 경로를 넘기지 못한다. 넘기면 무엇이든 열 수 있게 된다."""
    from tools import panel
    ok, err = panel._open_what(r"C:\Windows\System32\cmd.exe")
    assert not ok and "알 수 없는" in err


def test_panel_web_url_rejects_junk(tmp_path, monkeypatch):
    from tools import panel
    monkeypatch.setattr(panel, "output_dir", lambda: tmp_path)
    assert panel._web_url_save("javascript:alert(1)")[0] is False
    assert panel._web_url_save("https://a.kr/\r\nX")[0] is False
    ok, val = panel._web_url_save(" https://example.or.kr/kiosk/ ")
    assert ok and val == "https://example.or.kr/kiosk/"
    assert panel._web_url_load() == "https://example.or.kr/kiosk/"


def test_panel_qr_encodes():
    from tools import panel
    png = panel.qr_png("https://example.or.kr/kiosk/")
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_panel_uses_checked_color_tokens():
    """조작판이 self-check 가 검사한 토큰만 쓰는가.

    조작판이 색을 스스로 정하기 시작하면 '우리 화면도 같은 검사를 받는다'는
    말이 거짓이 된다. :root 를 style.css 에서 그대로 가져오는지 확인한다.
    """
    from engine.paths import resource_dir
    from tools import panel
    css = (resource_dir() / "app" / "style.css").read_text(encoding="utf-8")
    i = css.index(":root {")
    assert panel._root_tokens() == css[i:css.index("}", i) + 1]
    page = panel.page("TOK")
    # 페이지 본문에 날 hex 색이 박혀 있으면 안 된다 (토큰 정의 블록은 제외)
    body = page[page.index("</style>"):]
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", body), "조작판 본문에 직접 쓴 색이 있다"


def test_panel_page_requires_token():
    """토큰 없이 열리면 브라우저의 아무 페이지나 명령을 걸 수 있다."""
    from tools import panel
    h = panel.Handler.__new__(panel.Handler)
    panel.Handler.token = "SECRET"
    h.path = "/run?t=WRONG"
    assert h._authed() is False
    h.path = "/run?t=SECRET"
    assert h._authed() is True


def test_web_export_has_no_absolute_paths(tmp_path):
    """하위 폴더(example.or.kr/kiosk/)에 올려도 그대로 돌아야 한다."""
    from tools import publish_web
    r = publish_web.export(tmp_path / "web")
    assert not r["missing"], r["missing"]
    assert r["absolutes"] == [], r["absolutes"]
    assert (tmp_path / "web" / "올리는 법.txt").exists()
    # GitHub Pages 는 Jekyll 로 한 번 훑는다. 이 파일이 없으면 빌드가 끼어들고,
    # 실패하면 사이트가 통째로 안 올라간다.
    assert (tmp_path / "web" / ".nojekyll").exists()


def test_web_export_carries_no_ks_text(tmp_path):
    """올리는 순간 되돌릴 수 없다 — KS 본문이 섞이지 않았는지 나가기 전에 본다."""
    from tools import check_ks_copyright as KS, publish_web
    r = publish_web.export(tmp_path / "web")
    assert KS.scan_verbatim([tmp_path / "web" / f for f in r["copied"]]) == []


def test_panel_script_parses():
    """조작판의 자바스크립트가 실제로 파싱되는가.

    문법 오류가 나면 화면은 **멀쩡히 그려지고 버튼만 죽는다.**
    실제로 따옴표 하나가 어긋나 스크립트 전체가 죽었는데, 페이지는
    아무 표시 없이 정상으로 보였다. 눈으로는 잡을 수 없는 종류다.
    """
    import shutil
    import subprocess
    import tempfile
    from tools import panel
    page = panel.page("TOK")
    js = page[page.rindex("<script>") + 8:page.rindex("</script>")]

    # 빈 문자열('')은 정상이지만, 그 뒤에 글자가 바로 붙으면 따옴표가 어긋난 것이다.
    # 실제 사고가 이 모양이었다:  '… 처음 화면의 '+ ''촬영 앱을 …' 를 보세요.'
    bad = re.search(r"''(?=[\w가-힣])", js)
    assert not bad, f"따옴표가 어긋났습니다 — {js[max(0, bad.start() - 40):bad.start() + 40]!r}"

    node = shutil.which("node")
    if not node:
        pytest.skip("node 가 없어 문법 검사를 건너뜁니다")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "panel.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run([node, "--check", str(f)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
