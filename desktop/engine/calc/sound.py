"""음량 — 별표5 5.c (65dBA 이상 조절 · 음소거 · 사용 후 65dBA 미만 초기화).

측정 조건이 왜 우리 몫인가 — 표준 체인 추적 결과 (2026-08-22 확정)
─────────────────────────────────────────────────────────────────────
    별표5 5.c            "65dBA 이상으로 조절하거나 음소거" / "65dBA 미만으로 초기화"
                         → 측정 거리·암소음 조건 **없음**
    KS X 9211 §5.3.3 b)  "스피커 음량은 65 dBA 이상으로 조절할 수 있어야 한다"
                         비고 3 "소음이 있는 환경에서는 65 dBA가 부족할 수 있다"
                         비고 4 dBA 정의만. → 측정 거리 **없음**
                         참고: EN 301 549 V3.2.1, 5.1.3.12
    KS X 9211 §5.3.4     "세션이 종료될 때 음소거는 해제, 음량은 65 dBA 미만 자동 초기화"
    EN 301 549 §5.1.3.12 "output amplification up to a level of at least
                         65 dBA (-29 dBPaA)"  → 음압 기준점만 고정, 측정 거리 **없음**
    EN 301 549 §5.1.3.13 "resets the volume to ... 65 dBA or less after every use"

    -29 dBPaA 는 1 Pa 기준 A특성 음압이다. 65 dB SPL(re 20 µPa)를 환산하면
    20·log10(20e-6 × 10^(65/20)) = -28.98 ≈ -29 dBPaA 로 정확히 맞는다.
    즉 표준은 '얼마나 큰 소리인가'는 고정했지만 '어디서 재는가'는 열어 두었다.

    → 측정점은 정의되지 않았으므로 **우리 SOP가 정의하고 리포트에 함께 인쇄한다.**
      측정 조건 없이 인쇄된 dBA 값은 재현되지 않는다.

권고 항목의 출처 (판정 아님)
    EN 301 549 §8.2.1.1  음량 조절 범위 18 dB 이상
    EN 301 549 §8.2.1.2  단계식 제어면 최저 대비 +12 dB 중간 단계 1개 이상
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ── 별표5 판정 임계값 ──────────────────────────────────────────────────────
MAX_VOLUME_MIN_DBA = 65.0       # 5.c 전단: 65dBA '이상'으로 조절 가능해야
RESET_BELOW_DBA = 65.0          # 5.c 후단: 사용 후 65dBA '미만'으로 초기화

# ── 설계 권고 (EN 301 549 — 판정 아님) ────────────────────────────────────
RANGE_ADVISORY_DB = 18.0        # §8.2.1.1
MID_STEP_ADVISORY_DB = 12.0     # §8.2.1.2

# ── 측정 SOP — 표준이 비워 둔 자리를 우리가 채운다 ────────────────────────
#   기기 전면에서 300mm 떨어진, 이용자 귀 높이 두 지점에서 잰다.
#   선 자세와 휠체어 자세 중 **낮은 값**을 판정값으로 쓴다(보수적).
SOP_DISTANCE_MM = 300.0
SOP_EAR_HEIGHT_STANDING_MM = 1500.0
SOP_EAR_HEIGHT_WHEELCHAIR_MM = 1150.0
SOP_WEIGHTING = "A"
SOP_TIME_WEIGHTING = "FAST"
SOP_REPEATS = 3                 # 산술평균

# 암소음 보정 (일반 음향 계측 관례, ISO 3744 계열)
BG_NEGLIGIBLE_DELTA_DB = 10.0   # 이 이상 차이나면 보정 불필요
BG_INVALID_DELTA_DB = 3.0       # 이 미만이면 측정 무효


class MeasurementInvalid(RuntimeError):
    """암소음이 너무 높아 측정이 성립하지 않는다. 재측정을 요구한다."""


def background_correction(measured_dba: float, background_dba: float) -> tuple[float, str]:
    """암소음을 뺀 순수 신호 레벨을 구한다.

        L = 10·log10( 10^(Lm/10) − 10^(Lb/10) )

    Returns:
        (보정된 dBA, 사유 문자열)

    Raises:
        MeasurementInvalid: 신호와 암소음 차이가 3 dB 미만.
    """
    delta = measured_dba - background_dba
    if delta < BG_INVALID_DELTA_DB:
        raise MeasurementInvalid(
            f"암소음 {background_dba:.1f}dBA 가 측정값 {measured_dba:.1f}dBA 에 너무 가깝습니다 "
            f"(차이 {delta:.1f}dB < {BG_INVALID_DELTA_DB:.0f}dB). 조용한 시간대에 재측정하세요."
        )
    if delta >= BG_NEGLIGIBLE_DELTA_DB:
        return measured_dba, f"암소음 차이 {delta:.1f}dB — 보정 불필요"

    corrected = 10.0 * math.log10(10 ** (measured_dba / 10) - 10 ** (background_dba / 10))
    return corrected, f"암소음 차이 {delta:.1f}dB — 보정 적용 ({measured_dba:.1f} → {corrected:.1f}dBA)"


@dataclass
class VolumeReading:
    """한 측정점에서의 판독."""

    point: str                  # 'standing' | 'wheelchair'
    measured_dba: float
    background_dba: float
    corrected_dba: float = 0.0
    note: str = ""

    @classmethod
    def take(cls, point: str, measured_dba: float, background_dba: float) -> "VolumeReading":
        corrected, note = background_correction(measured_dba, background_dba)
        return cls(point, measured_dba, background_dba, round(corrected, 1), note)


@dataclass
class VolumeResult:
    """5.c 판정 결과."""

    max_dba: float | None                       # 두 측정점 중 낮은 값 (보수적)
    reset_dba: float | None
    mute_available: bool | None
    readings: list[VolumeReading] = field(default_factory=list)
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)

    def as_measured(self) -> dict[str, object]:
        out: dict[str, object] = {
            "최대 음량(판정값)": self.max_dba,
            "기준": f"{MAX_VOLUME_MIN_DBA:.0f}dBA 이상",
            "초기화 음량": self.reset_dba,
            "초기화 기준": f"{RESET_BELOW_DBA:.0f}dBA 미만",
            "음소거 가능": self.mute_available,
            "측정 조건": (
                f"기기 전면 {SOP_DISTANCE_MM:.0f}mm · 귀 높이 "
                f"{SOP_EAR_HEIGHT_STANDING_MM:.0f}/{SOP_EAR_HEIGHT_WHEELCHAIR_MM:.0f}mm · "
                f"{SOP_WEIGHTING}특성 {SOP_TIME_WEIGHTING} · {SOP_REPEATS}회 평균"
            ),
            "측정점": [
                {"위치": r.point, "실측": r.measured_dba, "암소음": r.background_dba,
                 "보정": r.corrected_dba, "비고": r.note}
                for r in self.readings
            ],
        }
        if self.failures:
            out["미달 사유"] = self.failures
        if self.advisory:
            out["설계 권고 미달"] = self.advisory
            out["권고 출처"] = "EN 301 549 §8.2.1.1 / §8.2.1.2 — 판정 아님"
        return out


def check_volume(
    readings: list[VolumeReading],
    *,
    reset_dba: float | None,
    mute_available: bool | None,
    min_volume_dba: float | None = None,
    mid_step_dba: float | None = None,
) -> VolumeResult:
    """별표5 5.c 판정 + EN 301 549 권고 확인.

    Args:
        readings: SOP 측정점별 판독. 최소 1개.
        reset_dba: 세션 종료 후 자동 초기화된 음량.
        mute_available: 음소거 수단 제공 여부.
        min_volume_dba: 최저 음량 설정 (권고 §8.2.1.1 범위 확인용).
        mid_step_dba: 단계식 제어의 중간 단계 (권고 §8.2.1.2 확인용).
    """
    result = VolumeResult(
        max_dba=None, reset_dba=reset_dba, mute_available=mute_available, readings=list(readings)
    )
    if not readings:
        result.failures.append("측정값 없음 — SOP 측정점에서 재측정 필요")
        return result

    # 보수적으로: 두 측정점 중 낮은 값이 65dBA 를 넘어야 한다
    result.max_dba = round(min(r.corrected_dba for r in readings), 1)

    # ── 별표5 5.c 전단 ──────────────────────────────────────────────────
    # '65dBA 이상 조절' 또는 '음소거' — 조문은 '거나'이지만, 두 기능은 서로
    # 대체되지 않는다. 난청 사용자에게 음소거는 대안이 아니다.
    # 따라서 둘 다 확인하되, 음량 미달만으로 부적합을 확정하지는 않고
    # 음소거가 없을 때 함께 부적합으로 본다. 판정 근거를 사유에 남긴다.
    volume_ok = result.max_dba >= MAX_VOLUME_MIN_DBA
    if not volume_ok:
        result.failures.append(
            f"최대 음량 {result.max_dba:.1f}dBA < {MAX_VOLUME_MIN_DBA:.0f}dBA"
        )
    if mute_available is False:
        result.failures.append("음소거 수단 없음")
    elif mute_available is None:
        result.failures.append("음소거 수단 확인 안 됨")

    # ── 별표5 5.c 후단 ──────────────────────────────────────────────────
    if reset_dba is None:
        result.failures.append("사용 후 초기화 음량 확인 안 됨")
    elif reset_dba >= RESET_BELOW_DBA:
        result.failures.append(
            f"초기화 음량 {reset_dba:.1f}dBA ≥ {RESET_BELOW_DBA:.0f}dBA (미만이어야 함)"
        )

    result.passed = not result.failures

    # ── 설계 권고 (EN 301 549) ──────────────────────────────────────────
    if min_volume_dba is not None:
        span = result.max_dba - min_volume_dba
        if span < RANGE_ADVISORY_DB:
            result.advisory.append(
                f"음량 조절 범위 {span:.1f}dB < {RANGE_ADVISORY_DB:.0f}dB (EN §8.2.1.1)"
            )
        if mid_step_dba is not None:
            step = mid_step_dba - min_volume_dba
            if step < MID_STEP_ADVISORY_DB:
                result.advisory.append(
                    f"중간 단계 +{step:.1f}dB < +{MID_STEP_ADVISORY_DB:.0f}dB (EN §8.2.1.2)"
                )

    return result


def sop_summary() -> dict[str, object]:
    """리포트에 함께 인쇄할 측정 조건. 조건 없는 dBA 값은 재현되지 않는다."""
    return {
        "측정 거리": f"기기 전면 {SOP_DISTANCE_MM:.0f}mm",
        "측정 높이": {
            "선 자세": f"{SOP_EAR_HEIGHT_STANDING_MM:.0f}mm",
            "휠체어 자세": f"{SOP_EAR_HEIGHT_WHEELCHAIR_MM:.0f}mm",
        },
        "판정값": "두 측정점 중 낮은 값 (보수적)",
        "주파수 가중": f"{SOP_WEIGHTING}특성",
        "시간 가중": SOP_TIME_WEIGHTING,
        "반복": f"{SOP_REPEATS}회 산술평균",
        "암소음": (
            f"신호와 차이 {BG_NEGLIGIBLE_DELTA_DB:.0f}dB 이상이면 보정 불필요, "
            f"{BG_INVALID_DELTA_DB:.0f}~{BG_NEGLIGIBLE_DELTA_DB:.0f}dB 이면 보정, "
            f"{BG_INVALID_DELTA_DB:.0f}dB 미만이면 측정 무효"
        ),
        "근거": (
            "별표5·KS X 9211 §5.3.3·EN 301 549 §5.1.3.12 어디에도 측정 거리 규정이 없어 "
            "본 SOP 가 정의한 조건임"
        ),
    }
