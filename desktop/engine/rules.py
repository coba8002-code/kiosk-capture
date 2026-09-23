"""룰 DB 로더 — annex5.yaml 을 읽고, 검증하고, 질의한다.

엔진·앱·리포트는 모두 이 모듈을 통해서만 룰에 접근한다.
YAML 을 직접 읽는 코드 경로가 생기면 스키마 검증을 우회하게 되므로 금지한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from .paths import rules_path

DEFAULT_RULES_PATH = rules_path("annex5.yaml")
DEFAULT_PROTOCOL_PATH = rules_path("capture-protocol.yaml")
# 별표5가 아닌 참고 기준. 판정을 바꾸지 않는다 — engine/usability.py 머리말 참조.
DEFAULT_ADVISORY_PATH = rules_path("usability-advisory.yaml")
DEFAULT_SOP_PATH = rules_path("measurement-sop.yaml")

TRACKS = {"A_AI", "B_OWNER", "C_MEASURE", "D_USER"}
AI_ROLES = {"measure", "judge", "screen", "none"}
AI_SCOPES = {"both", "fail_only", "none"}
FINAL_BY = {"REVIEWER", "MEASURE", "OWNER"}
REMEDY_TYPES = {"SW", "HW", "OPS", "NONE"}
DIFFICULTIES = {"낮음", "중간", "높음"}
CAPTURE_SETS = {"S1", "S2", "S3", "S4", "S5", "S6"}


class RuleError(ValueError):
    """룰 DB 자체가 잘못된 경우. 실행 전에 죽인다."""


@dataclass
class RuleSet:
    """검증을 통과한 룰 DB."""

    meta: dict[str, Any]
    enums: dict[str, Any]
    categories: list[dict[str, Any]]
    user_validation: dict[str, Any]
    exemptions: list[dict[str, Any]]
    _index: dict[str, dict[str, Any]]

    # ---- 질의 ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._index)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._index.values())

    def __getitem__(self, rule_id: str) -> dict[str, Any]:
        try:
            return self._index[rule_id]
        except KeyError:
            raise RuleError(f"존재하지 않는 항목: {rule_id}") from None

    def ids(self) -> list[str]:
        return list(self._index)

    def by_track(self, track: str) -> list[dict[str, Any]]:
        return [r for r in self if r["track"] == track]

    def by_category(self, no: int) -> list[dict[str, Any]]:
        for cat in self.categories:
            if cat["area"] == no:
                return cat["items"]
        raise RuleError(f"존재하지 않는 영역: {no}")

    def category_of(self, rule_id: str) -> dict[str, Any]:
        for cat in self.categories:
            if any(i["id"] == rule_id for i in cat["items"]):
                return {"no": cat["area"], "name": cat["name"]}
        raise RuleError(f"존재하지 않는 항목: {rule_id}")

    def graded(self) -> list[dict[str, Any]]:
        """우수/보통 등급이 부여되는 항목."""
        return [r for r in self if (r.get("verdict") or {}).get("grade")]

    def requiring_marker(self) -> list[dict[str, Any]]:
        return [r for r in self if r.get("marker_required")]

    def fed_by(self, capture_set: str) -> list[dict[str, Any]]:
        """특정 촬영 세트가 입력으로 들어가는 항목."""
        return [r for r in self if capture_set in (r.get("evidence") or [])]

    def applicable(
        self,
        *,
        product_type: str,
        available_scopes: set[str] | None = None,
        exemption_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """이번 진단에서 실제로 평가할 항목만 추린다.

        Args:
            product_type: '중대형' 또는 '소형'
            available_scopes: 이 기기에 존재하는 조건부 기능 집합.
                              None 이면 '기본' 항목만 평가한다.
            exemption_ids: 적용되는 면제 경로 ID 집합.
        """
        scopes = {"기본"} | (available_scopes or set())
        exempt: set[str] = set()
        for ex in self.exemptions:
            if exemption_ids and ex["id"] in exemption_ids:
                exempt |= set(ex["exempts"])

        out = []
        for r in self:
            if product_type not in r["applies_to"]:
                continue
            if r["scope"] not in scopes:
                continue
            if r["id"] in exempt:
                continue
            out.append(r)
        return out

    def exempted_by(self, exemption_ids: set[str]) -> set[str]:
        out: set[str] = set()
        for ex in self.exemptions:
            if ex["id"] in exemption_ids:
                out |= set(ex["exempts"])
        return out


def load(path: str | Path = DEFAULT_RULES_PATH, *, validate: bool = True) -> RuleSet:
    """룰 DB 를 읽어 검증한다."""
    p = Path(path)
    if not p.exists():
        raise RuleError(f"룰 DB 파일이 없습니다: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuleError("룰 DB 최상위가 매핑이 아닙니다")

    index: dict[str, dict[str, Any]] = {}
    for cat in raw.get("categories", []):
        for item in cat.get("items", []):
            rid = item.get("id")
            if not rid:
                raise RuleError(f"영역 {cat.get('no')} 에 id 없는 항목이 있습니다")
            if rid in index:
                raise RuleError(f"중복 항목 ID: {rid}")
            index[rid] = item

    rs = RuleSet(
        meta=raw.get("meta", {}),
        enums=raw.get("enums", {}),
        categories=raw.get("categories", []),
        user_validation=raw.get("user_validation", {}),
        exemptions=raw.get("exemptions", []),
        _index=index,
    )

    if validate:
        problems = check(rs)
        if problems:
            raise RuleError("룰 DB 검증 실패:\n  - " + "\n  - ".join(problems))
    return rs


def check(rs: RuleSet) -> list[str]:
    """스키마·정합성 검사. 문제 목록을 돌려준다(빈 리스트면 통과)."""
    problems: list[str] = []
    required = {
        "id", "scope", "applies_to", "criterion", "verdict",
        "track", "ai_role", "ai_verdict_scope", "final_by",
        "evidence", "marker_required", "remedy",
    }

    declared_total = rs.meta.get("total_items")
    if declared_total is not None and declared_total != len(rs):
        problems.append(f"meta.total_items={declared_total} 이지만 실제 항목 수는 {len(rs)}")

    declared_graded = rs.meta.get("graded_items")
    actual_graded = len(rs.graded())
    if declared_graded is not None and declared_graded != actual_graded:
        problems.append(f"meta.graded_items={declared_graded} 이지만 실제는 {actual_graded}")

    valid_scopes = set(rs.enums.get("scope", {}))
    valid_products = set(rs.enums.get("product_type", {}))

    for r in rs:
        rid = r["id"]
        missing = required - set(r)
        if missing:
            problems.append(f"{rid}: 필수 필드 누락 {sorted(missing)}")
            continue

        if r["track"] not in TRACKS:
            problems.append(f"{rid}: 알 수 없는 track {r['track']!r}")
        if r["ai_role"] not in AI_ROLES:
            problems.append(f"{rid}: 알 수 없는 ai_role {r['ai_role']!r}")
        if r["ai_verdict_scope"] not in AI_SCOPES:
            problems.append(f"{rid}: 알 수 없는 ai_verdict_scope {r['ai_verdict_scope']!r}")
        if r["final_by"] not in FINAL_BY:
            problems.append(f"{rid}: 알 수 없는 final_by {r['final_by']!r}")
        if valid_scopes and r["scope"] not in valid_scopes:
            problems.append(f"{rid}: 알 수 없는 scope {r['scope']!r}")
        for pt in r["applies_to"]:
            if valid_products and pt not in valid_products:
                problems.append(f"{rid}: 알 수 없는 product_type {pt!r}")

        # --- 트랙과 AI 사정거리의 정합성 -------------------------------------
        if r["track"] == "A_AI" and r["ai_verdict_scope"] == "none":
            problems.append(f"{rid}: track=A_AI 인데 ai_verdict_scope=none")
        if r["ai_role"] == "none" and r["ai_verdict_scope"] != "none":
            problems.append(f"{rid}: ai_role=none 인데 ai_verdict_scope={r['ai_verdict_scope']}")
        if r["track"] == "C_MEASURE" and r["final_by"] != "MEASURE":
            problems.append(f"{rid}: track=C_MEASURE 인데 final_by={r['final_by']}")
        if r["track"] == "B_OWNER" and r["final_by"] != "OWNER":
            problems.append(f"{rid}: track=B_OWNER 인데 final_by={r['final_by']}")

        # --- 근거 요구 -------------------------------------------------------
        for ev in r["evidence"]:
            if ev not in CAPTURE_SETS:
                problems.append(f"{rid}: 알 수 없는 촬영 세트 {ev!r}")
        if r["ai_role"] in ("measure", "judge", "screen") and not r["evidence"]:
            problems.append(f"{rid}: AI가 관여하는데 evidence 가 비어 있습니다")
        if r["marker_required"] and not r["evidence"]:
            problems.append(f"{rid}: marker_required 인데 evidence 가 비어 있습니다")
        if r["ai_role"] == "measure" and not r.get("logic_ref"):
            problems.append(f"{rid}: ai_role=measure 인데 logic_ref 가 없습니다")

        # --- 점주 트랙은 질문이 있어야 한다 -----------------------------------
        if r["track"] == "B_OWNER" and not r.get("owner_question"):
            problems.append(f"{rid}: track=B_OWNER 인데 owner_question 이 없습니다")

        # --- 실측 트랙은 장비가 명시돼야 한다 ---------------------------------
        if r["track"] == "C_MEASURE" and not (r.get("instrument") or r.get("logic_ref")):
            problems.append(f"{rid}: track=C_MEASURE 인데 instrument 가 없습니다")

        # --- 처방 ------------------------------------------------------------
        remedy = r["remedy"]
        if remedy.get("type") not in REMEDY_TYPES:
            problems.append(f"{rid}: 알 수 없는 remedy.type {remedy.get('type')!r}")
        if remedy.get("difficulty") not in DIFFICULTIES:
            problems.append(f"{rid}: 알 수 없는 remedy.difficulty {remedy.get('difficulty')!r}")
        if remedy.get("cost_band") not in (1, 2, 3):
            problems.append(f"{rid}: 알 수 없는 remedy.cost_band {remedy.get('cost_band')!r}")

        # --- 등급 ------------------------------------------------------------
        grade = (r.get("verdict") or {}).get("grade")
        if grade and set(grade) != {"우수", "보통"}:
            problems.append(f"{rid}: grade 는 우수/보통 두 키만 가져야 합니다")

    # --- 면제 경로가 실재 항목을 가리키는지 -----------------------------------
    for ex in rs.exemptions:
        for rid in ex.get("exempts", []):
            if rid not in rs._index:
                problems.append(f"면제 {ex['id']}: 존재하지 않는 항목 {rid} 참조")
        for r in rs:
            declared = set(r.get("exemptible_by") or [])
            if ex["id"] in declared and r["id"] not in ex.get("exempts", []):
                problems.append(
                    f"{r['id']}: exemptible_by 에 {ex['id']} 가 있으나 면제 목록에 없습니다"
                )

    # --- 사용자 검증 인원 합계 -------------------------------------------------
    uv = rs.user_validation
    if uv:
        total = sum(c["min_count"] for c in uv.get("composition", []))
        if total != uv.get("total_min_count"):
            problems.append(
                f"user_validation: 구성 합계 {total} ≠ total_min_count {uv.get('total_min_count')}"
            )

    return problems


def load_protocol(path: str | Path = DEFAULT_PROTOCOL_PATH) -> dict[str, Any]:
    """촬영 프로토콜을 읽는다."""
    p = Path(path)
    if not p.exists():
        raise RuleError(f"촬영 프로토콜 파일이 없습니다: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_advisory(path: str | Path = DEFAULT_ADVISORY_PATH) -> dict[str, Any]:
    """사용성 주의 기준을 읽는다.

    **별표5가 아니다.** 없으면 빈 것으로 돌려준다 — 참고 정보이므로
    파일이 없다고 진단이 멈출 이유가 없다.
    """
    p = Path(path)
    if not p.exists():
        return {"items": []}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {"items": []}


def cross_check(rs: RuleSet, protocol: dict[str, Any]) -> list[str]:
    """룰 DB 와 촬영 프로토콜의 정합성. 둘이 어긋나면 촬영해도 판정이 안 된다."""
    problems: list[str] = []
    sets = {s["id"]: s for s in protocol.get("sets", [])}

    for r in rs:
        for ev in r.get("evidence") or []:
            if ev not in sets:
                problems.append(f"{r['id']}: 프로토콜에 없는 촬영 세트 {ev} 참조")

    for sid, s in sets.items():
        for rid in s.get("feeds") or []:
            if rid not in rs._index:
                problems.append(f"프로토콜 {sid}.feeds: 존재하지 않는 항목 {rid}")
            elif sid not in (rs[rid].get("evidence") or []):
                problems.append(
                    f"프로토콜 {sid} 은 {rid} 를 먹인다고 하는데 "
                    f"{rid}.evidence 에 {sid} 가 없습니다"
                )
        for shot in s.get("shots") or []:
            for rid in shot.get("required_for") or []:
                if rid not in rs._index:
                    problems.append(f"프로토콜 {shot['id']}.required_for: 존재하지 않는 항목 {rid}")

    # 마커가 필요한 항목은 마커 평면이 있는 세트를 근거로 가져야 한다
    marker_planes = {
        s["id"] for s in protocol.get("sets", []) if s.get("marker_plane")
    }
    for r in rs.requiring_marker():
        if not (set(r["evidence"]) & marker_planes):
            problems.append(
                f"{r['id']}: marker_required 인데 근거 세트 {r['evidence']} 에 "
                f"마커 평면이 정의된 세트가 없습니다"
            )

    return problems
