"""Validated browser review storage."""
import hashlib
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from engine import pipeline, rules
from engine.measurement import FIELDS, evaluate

LOCK = threading.Lock()
SESSIONS = {}

def revision(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ''

def load(path):
    bundle = pipeline.load_bundle(path)
    rs = rules.load()
    pending = pipeline.pending_review(rs, pipeline.analyze(bundle, rs).assessments)
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = {'root': bundle.root, 'revision': revision(bundle.review_path),
                       'shots': [s.path for s in bundle.shots],
                       'pending': {a.rule_id for a in pending}}
    while len(SESSIONS) > 20:
        SESSIONS.pop(next(iter(SESSIONS)))
    return {'ok': True, 'session': token, 'device': bundle.device_id,
      'reviewer': bundle.review.get('reviewer', ''),
      'photos': [{'index': i, 'name': s.path.name} for i,s in enumerate(bundle.shots)
                 if s.path.suffix.lower() in ('.jpg','.jpeg','.png','.webp')],
      'pending': [{'id': a.rule_id, 'title': rs[a.rule_id].get('plain', a.rule_id),
        'evidence': a.evidence.measured if a.evidence else {}} for a in pending],
      'measurements': [{'id': r['id'], 'title': r.get('plain', r['id']), 'numeric': FIELDS.get(r['id']),
        'criterion': r.get('criterion', ''), 'previous': bundle.measurements.get(r['id'], {})}
        for r in rs.by_track('C_MEASURE')]}

def save(payload):
    with LOCK:
        session = SESSIONS.get(payload.get('session', ''))
        if session is None:
            raise ValueError('검토 화면을 다시 열어 주세요.')
        bundle = pipeline.load_bundle(session['root'])
        if revision(bundle.review_path) != session['revision']:
            raise ValueError('다른 창에서 기록을 변경했습니다. 다시 열어 확인하세요.')
        reviewer = payload.get('reviewer')
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100:
            raise ValueError('검토자 이름을 100자 이내로 입력하세요.')
        decisions, measurements = payload.get('decisions', {}), payload.get('measurements', {})
        if not isinstance(decisions, dict) or not isinstance(measurements, dict):
            raise ValueError('입력 형식이 올바르지 않습니다.')
        for rid, decision in decisions.items():
            if rid not in session['pending'] or decision not in ('approve', 'reject'):
                raise ValueError('승인 대기 항목만 승인·반려할 수 있습니다.')
        allowed = {r['id'] for r in rules.load().by_track('C_MEASURE')}
        measured = {}
        for rid, rec in measurements.items():
            if rid not in allowed or not isinstance(rec, dict):
                raise ValueError('실측 대상 항목이 아닙니다.')
            if rec.get('method') == 'numeric/1':
                for key in ('condition','instrument','scope'):
                    value = rec.get(key)
                    if not isinstance(value,str) or not value.strip() or len(value)>2000:
                        raise ValueError('측정 조건·장비 및 오차 근거·검사 범위를 모두 입력하세요.')
                clean = {k: rec.get(k) for k in ('method','values','error_bound','condition','instrument','scope','coverage_complete','confirmed')}
                clean['by'] = reviewer.strip()
                evaluate(rules.load()[rid],clean)
                measured[rid] = clean
                continue
            if rec.get('verdict') not in ('pass', 'fail', 'na'):
                raise ValueError('실측 결과를 선택하세요.')
            value, condition = rec.get('value'), rec.get('condition')
            if any(not isinstance(s, str) or not s.strip() or len(s) > 2000 for s in (value, condition)):
                raise ValueError('측정값(또는 해당 없음 사유)과 측정 조건을 입력하세요.')
            measured[rid] = {'verdict': rec['verdict'], 'by': reviewer.strip(),
                             '측정값': value.strip(), '측정 조건': condition.strip()}
        if not decisions and not measured:
            raise ValueError('저장할 변경 사항이 없습니다.')
        review = dict(bundle.review)
        authors = {rid: review.get('reviewer', '검토자') for rid in review.get('decisions', {})}
        authors.update(review.get('decision_reviewers', {}))
        authors.update({rid: reviewer.strip() for rid in decisions})
        review['decision_reviewers'] = authors
        review['reviewer'] = reviewer.strip()
        review['decisions'] = {**review.get('decisions', {}), **decisions}
        review['measurements'] = {**review.get('measurements', {}), **measured}
        review['history'] = [*review.get('history', []), {
            'at': datetime.now(timezone.utc).isoformat(), 'reviewer': reviewer.strip(),
            'decisions': decisions, 'measurements': measured}]
        fd, name = tempfile.mkstemp(prefix='.review-', suffix='.tmp', dir=bundle.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'schema': pipeline.REVIEW_SCHEMA, **review}, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, bundle.review_path)
        finally:
            if Path(name).exists():
                Path(name).unlink()
        SESSIONS.pop(payload['session'], None)
        return {'ok': True, 'path': str(bundle.root)}
