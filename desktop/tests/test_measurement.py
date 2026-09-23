import pytest
from types import SimpleNamespace
from engine import rules, pipeline
from engine.measurement import evaluate
from engine.verdict import Verdict

def record(values,u=.1,**changes):
    return dict(method='numeric/1', values=values,error_bound=u,by='담당자',
        confirmed=True,coverage_complete=True,condition='현장',instrument='교정 확인',scope='전체',**changes)

@pytest.mark.parametrize('height,u,result',[(8,.1,Verdict.PASS),(7.25,.1,Verdict.UNDETERMINED),(7,.1,Verdict.FAIL),(8,None,Verdict.UNDETERMINED)])
def test_height_interval(height,u,result):
    assert evaluate(rules.load()['3.g'],record({'height':height},u)).verdict is result

@pytest.mark.parametrize('rid,values,result',[
 ('1.c',{'width':13,'height':13},Verdict.PASS),('1.c',{'width':11,'height':13},Verdict.FAIL),
 ('1.d',{'force':22.2},Verdict.UNDETERMINED),('1.d',{'force':23},Verdict.FAIL),
 ('9.a',{'lowest':410,'highest':1210},Verdict.PASS),('9.a',{'lowest':399,'highest':1200},Verdict.FAIL),
 ('9.b',{'height':1401},Verdict.FAIL)])
def test_rules(rid,values,result):
    assert evaluate(rules.load()[rid],record(values)).verdict is result

@pytest.mark.parametrize('key',['confirmed','coverage_complete'])
def test_no_pass_without_confirmation_and_coverage(key):
    r=record({'height':8});r[key]=False
    assert evaluate(rules.load()['3.g'],r).verdict is Verdict.UNDETERMINED

@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,True,'8'])
def test_bad_numeric_input(value):
    with pytest.raises(ValueError): evaluate(rules.load()['3.g'],record({'height':value}))

def test_pipeline_recomputes_not_trusts_claimed_verdict():
    r=record({'height':7.25});r['verdict']='pass'
    bundle=SimpleNamespace(measurements={'3.g':r})
    assert pipeline._measurements(rules.load(),bundle)[0].verdict is Verdict.UNDETERMINED

def test_report_coverage_escapes_input():
    from engine.report.coverage import render
    run=SimpleNamespace(bundle=SimpleNamespace(shots=[]),skipped=['<script>bad</script>'])
    html=render(run)
    assert '<script>' not in html and '&lt;script&gt;' in html

def test_measured_borderline_replaces_photo_pass(tmp_path,monkeypatch):
    import json
    from engine.verdict import Assessment
    from engine.ocr.provider import Provider
    (tmp_path/'manifest.json').write_text(json.dumps({'schema':pipeline.SCHEMA,
        'device':{'id':'T','product_type':'소형'},'shots':[],
        'measurements':{'3.g':record({'height':7.25})}}),encoding='utf-8')
    monkeypatch.setattr(pipeline,'_text_height_rule',lambda *args:Assessment('3.g',Verdict.PASS))
    run=pipeline.analyze(pipeline.load_bundle(tmp_path),rules.load(),ocr_provider=Provider())
    items=[a for a in run.assessments if a.rule_id=='3.g']
    assert len(items)==1
    assert items[0].verdict is Verdict.UNDETERMINED
    assert items[0].source.startswith('MEASURE:')
