"""Structured measurements with explicit interval and coverage checks.

The supplied bound is not AI confidence or a calibrated uncertainty estimate.
"""
import math
from .verdict import Assessment, Evidence, Verdict

FIELDS = {
 '1.c': [('width','전체 대상의 최소 너비','mm'),('height','전체 대상의 최소 높이','mm')],
 '1.d': [('force','전체 조작부의 최대 조작력','N')],
 '3.g': [('height','전체 필수 문자의 최소 높이','mm')],
 '9.a': [('lowest','가장 낮은 조작부 높이','mm'),('highest','가장 높은 조작부 높이','mm')],
 '9.b': [('height','전체 화면의 필수 정보 최상단 최대 높이','mm')],
}

def number(value):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < 0:
        raise ValueError('측정값과 오차 범위는 0 이상의 유한한 숫자여야 합니다.')
    return float(value)

def evaluate(rule, rec):
    rid=rule['id']
    if rid not in FIELDS: raise ValueError('자동 비교 미지원 항목입니다.')
    values=rec.get('values')
    if not isinstance(values,dict): raise ValueError('항목별 측정값이 필요합니다.')
    v={k:number(values.get(k)) for k,_,_ in FIELDS[rid]}
    bound=rec.get('error_bound')
    u=number(bound) if bound is not None else None
    if rid=='9.a' and v['lowest']>v['highest']: raise ValueError('최저 높이가 최고 높이보다 큽니다.')
    p=rule['params']; checks=[]
    def minimum(key, limit):
        x=v[key]; checks.append(('미달' if x+u<limit else '충족' if x-u>=limit else '경계',f'{key} ≥ {limit}'))
    def maximum(key, limit):
        x=v[key]; checks.append(('미달' if x-u>limit else '충족' if x+u<=limit else '경계',f'{key} ≤ {limit}'))
    if u is not None:
        if rid=='1.c':
            minimum('width',p['side_min_mm']); minimum('height',p['side_min_mm'])
            lo=max(0,v['width']-u)*max(0,v['height']-u); hi=(v['width']+u)*(v['height']+u)
            limit=p['mbr_area_min_mm2']
            checks.append(('미달' if hi<limit else '충족' if lo>=limit else '경계',f'MBR 면적 ≥ {limit} mm²'))
        elif rid=='1.d': maximum('force',p['force_max_n'])
        elif rid=='3.g': minimum('height',p['char_height_min_mm'])
        elif rid=='9.a': minimum('lowest',p['height_min_mm']);maximum('highest',p['height_max_mm'])
        elif rid=='9.b': maximum('height',p['height_max_mm'])
    states=[s for s,_ in checks]
    comparison='오차 미입력' if u is None else '기준 미달' if '미달' in states else '경계 — 재측정' if '경계' in states else '입력 범위 충족'
    verified=rec.get('confirmed') is True and bool(str(rec.get('by','')).strip())
    if not verified or u is None or '경계' in states and '미달' not in states:
        verdict=Verdict.UNDETERMINED
    elif '미달' in states: verdict=Verdict.FAIL
    elif rec.get('coverage_complete') is True: verdict=Verdict.PASS
    else: verdict=Verdict.UNDETERMINED
    measured={'입력 측정값':v,'단위':FIELDS[rid][0][2],'오차 범위 ±':u,
      '오차의 의미':'측정자가 입력한 오차 한계. AI 확신도나 교정된 불확실성과 동일하지 않음',
      '비교 결과':comparison,'비교 기준':[c for _,c in checks],
      '전수 확인':rec.get('coverage_complete') is True,'담당자 확인':verified,
      '측정 조건':rec.get('condition',''),'장비·교정·오차 근거':rec.get('instrument',''),
      '검사 범위':rec.get('scope',''),'by':rec.get('by','')}
    return Assessment(rid,verdict,evidence=Evidence(criterion_id=rid,measured=measured),
        source='MEASURE:'+str(rec.get('by','미상')),next_owner='MEASURE' if verdict is Verdict.UNDETERMINED else None)
