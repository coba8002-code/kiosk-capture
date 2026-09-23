"""현장 촬영 번들의 완전성·분석 가능성 검사.

법정 적합성 판정과 분리한다. 여기서 '준비됨'은 촬영 자료가 진단에 투입될
수 있다는 뜻이지, 키오스크가 접근성 기준에 적합하다는 뜻이 아니다.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .calc import scale
from .media.audio_quality import AudioQuality, from_manifest, inspect_wav

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".avi", ".mkv"}


@dataclass
class QualityIssue:
    level: str                     # block | warn | info
    code: str
    shot: str
    message: str
    fix: str


@dataclass
class QualityReport:
    device_id: str
    total_files: int
    required_sets: int
    covered_sets: int
    checklist_total: int
    checklist_captured: int
    marker_expected: int
    marker_found: int
    audio_total: int
    audio_analyzed: int
    metadata_required: int
    metadata_filled: int
    masked_files: int
    issues: list[QualityIssue] = field(default_factory=list)
    audio: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(x.level == "block" for x in self.issues):
            return "보완 후 진단"
        if any(x.level == "warn" for x in self.issues):
            return "진단 가능 · 주의 있음"
        return "진단 준비됨"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "status": self.status,
                "disclaimer": "이 품질검사는 법정 적합성 인증이 아닙니다."}


def audit(bundle, protocol: dict[str, Any]) -> QualityReport:
    sets = {s["id"]: s for s in protocol.get("sets", [])}
    required = [s for s in sets.values() if s.get("required")]
    present_sets = {s.set_id for s in bundle.shots}
    checklist = [(s["id"], sh) for s in sets.values() for sh in s.get("shots", [])]
    captured_ids = {s.shot_id for s in bundle.shots}
    issues: list[QualityIssue] = []

    for s in required:
        if s["id"] not in present_sets:
            issues.append(QualityIssue(
                "block", "missing-set", s["id"], f"필수 촬영 세트 ‘{s['name']}’가 없습니다.",
                "촬영 앱에서 이 세트를 한 건 이상 추가하세요."))

    marker_expected = marker_found = 0
    audio_rows: list[dict[str, Any]] = []
    audio_total = audio_analyzed = 0
    meta_required = meta_filled = 0
    masked = 0

    shot_specs = {sh["id"]: sh for _, sh in checklist}
    for shot in bundle.shots:
        spec = shot_specs.get(shot.shot_id, {})
        suffix = shot.path.suffix.lower()
        if shot.masked:
            masked += 1
        elif suffix in IMAGE_SUFFIXES:
            issues.append(QualityIssue(
                "warn", "privacy-unconfirmed", shot.shot_id,
                "개인정보 가림 완료 표시가 없습니다.",
                "얼굴·카드번호·성명·주민번호가 없는지 확인하고 필요하면 가림 처리하세요."))

        for q in shot.quality_issues:
            issues.append(QualityIssue(
                "warn", "capture-quality", shot.shot_id, q,
                "현장에서 다시 촬영할 수 있으면 해당 컷을 교체하세요."))

        for fld in spec.get("metadata") or []:
            if not fld.get("required"):
                continue
            meta_required += 1
            if str(shot.metadata.get(fld.get("key"), "")).strip():
                meta_filled += 1
            else:
                issues.append(QualityIssue(
                    "block", "metadata-missing", shot.shot_id,
                    f"필수 기록 ‘{fld.get('label', fld.get('key'))}’이 비어 있습니다.",
                    "촬영 앱에서 사진 아래 기록란을 채운 뒤 다시 제출하세요."))

        if shot.marker_plane and suffix in IMAGE_SUFFIXES:
            marker_expected += 1
            if _has_marker(shot.path):
                marker_found += 1
            else:
                issues.append(QualityIssue(
                    "block", "marker-missing", shot.shot_id,
                    "치수 환산용 측정 마커를 검출하지 못했습니다.",
                    "마커를 대상과 같은 평면에 두고 더 가까이, 정면에서 다시 찍으세요."))

        if shot.set_id == "S5":
            audio_total += 1
            aq = _audio(shot)
            row = {"shot": shot.shot_id, "file": shot.path.name, **aq.to_dict()}
            audio_rows.append(row)
            if aq.readable:
                audio_analyzed += 1
            else:
                issues.append(QualityIssue(
                    "warn", "audio-unreadable", shot.shot_id,
                    aq.note or "음성 품질 지표를 읽지 못했습니다.",
                    "촬영 앱에서 다시 녹음하면 휴대폰 안에서 품질을 자동 검사합니다."))
            for msg in aq.issues:
                issues.append(QualityIssue(
                    "warn", "audio-quality", shot.shot_id, msg,
                    "주변 소음을 줄이고 스피커 가까이에서 2초 이상 다시 녹음하세요."))

    return QualityReport(
        bundle.device_id, len(bundle.shots), len(required),
        sum(1 for s in required if s["id"] in present_sets),
        len(checklist), len(captured_ids & {sh["id"] for _, sh in checklist}),
        marker_expected, marker_found, audio_total, audio_analyzed,
        meta_required, meta_filled, masked, issues, audio_rows,
    )


def _has_marker(path: Path) -> bool:
    try:
        import cv2
        import numpy as np
        raw = np.fromfile(str(path), dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        return bool(image is not None and scale.detect_marker_corners(image)[0] is not None)
    except Exception:  # 품질검사 실패를 판정으로 바꾸지 않는다
        return False


def _audio(shot) -> AudioQuality:
    found = from_manifest(shot.audio_metrics)
    if found.readable:
        return found
    if shot.path.suffix.lower() == ".wav":
        return inspect_wav(shot.path)
    return found


def render(report: QualityReport) -> str:
    """외부 리소스 없는 인쇄용 HTML."""
    e = html.escape
    cards = [
        ("필수 세트", f"{report.covered_sets}/{report.required_sets}"),
        ("촬영 체크", f"{report.checklist_captured}/{report.checklist_total}"),
        ("측정 마커", f"{report.marker_found}/{report.marker_expected}"),
        ("음성 품질", f"{report.audio_analyzed}/{report.audio_total}"),
        ("필수 기록", f"{report.metadata_filled}/{report.metadata_required}"),
    ]
    issue_rows = "".join(
        f"<tr><td><span class='{x.level}'>{ {'block':'보완','warn':'주의','info':'안내'}[x.level] }</span></td>"
        f"<td>{e(x.shot)}</td><td>{e(x.message)}</td><td>{e(x.fix)}</td></tr>"
        for x in report.issues
    ) or "<tr><td colspan='4'>발견된 문제가 없습니다.</td></tr>"
    audio_rows = "".join(
        f"<tr><td>{e(str(x['shot']))}</td><td>{e(str(x['status']))}</td>"
        f"<td>{x.get('duration_s') if x.get('duration_s') is not None else '-'}</td>"
        f"<td>{x.get('rms_dbfs') if x.get('rms_dbfs') is not None else '-'}</td>"
        f"<td>{e(' · '.join(x.get('issues') or []) or x.get('note',''))}</td></tr>"
        for x in report.audio
    ) or "<tr><td colspan='5'>S5 음성 자료가 없습니다.</td></tr>"
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>현장자료 품질 리포트</title>
<style>
@page{{size:A4;margin:15mm}}*{{box-sizing:border-box}}body{{font-family:'Malgun Gothic',sans-serif;color:#132c44;margin:0;background:#fff;font-size:14px;line-height:1.55}}
h1{{font-size:27px;margin:0 0 5px}}h2{{font-size:18px;margin:28px 0 10px}}.lead{{color:#506579}}.status{{display:inline-block;background:#e8f2ff;color:#154f84;border-radius:999px;padding:7px 13px;font-weight:800}}
.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:20px 0}}.card{{border:1px solid #b9c9d8;border-radius:10px;padding:12px}}.card b{{display:block;font-size:20px}}.card span{{font-size:12px;color:#52697d}}
table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #c9d5df;padding:8px;text-align:left;vertical-align:top}}th{{background:#edf3f8}}.block,.warn,.info{{font-weight:800;white-space:nowrap}}.block{{color:#b42318}}.warn{{color:#8a4b00}}.info{{color:#245b86}}.notice{{margin-top:25px;padding:13px;background:#f1f5f8;border-left:5px solid #245b86}}
</style></head><body><h1>현장자료 품질 리포트</h1>
<p class='lead'>기기 {e(report.device_id)} · 촬영 자료가 분석 가능한지 먼저 확인합니다.</p>
<p><span class='status'>{e(report.status)}</span></p>
<div class='cards'>{''.join(f"<div class='card'><span>{e(k)}</span><b>{e(v)}</b></div>" for k,v in cards)}</div>
<h2>보완할 사항</h2><table><thead><tr><th>수준</th><th>촬영</th><th>문제</th><th>해결 방법</th></tr></thead><tbody>{issue_rows}</tbody></table>
<h2>S5 음성 품질</h2><table><thead><tr><th>촬영</th><th>상태</th><th>초</th><th>RMS dBFS</th><th>비고</th></tr></thead><tbody>{audio_rows}</tbody></table>
<div class='notice'><b>해석 주의</b><br>dBFS는 녹음 파일의 디지털 품질 지표이며 dBA가 아닙니다. 별표5 5.c의 65 dBA 판정은 교정된 소음계와 정해진 측정 조건으로 별도 실측해야 합니다.<br>이 품질검사는 법정 적합성 인증이 아닙니다.</div>
</body></html>"""


def write(report: QualityReport, folder: str | Path) -> list[Path]:
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    j = out / "현장자료_품질.json"
    h = out / "현장자료_품질.html"
    j.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    h.write_text(render(report), encoding="utf-8")
    return [j, h]
