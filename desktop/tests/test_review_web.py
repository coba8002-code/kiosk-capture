import json
from types import SimpleNamespace
import pytest
from engine import pipeline, rules
from engine.verdict import Assessment, Verdict
from tools import review_web as web

@pytest.fixture
def opened(tmp_path, monkeypatch):
    (tmp_path/'manifest.json').write_text(json.dumps({'schema':pipeline.SCHEMA,
        'device':{'id':'TEST','product_type':'소형'},'shots':[]}),encoding='utf-8')
    monkeypatch.setattr(pipeline,'analyze',lambda *args:SimpleNamespace(assessments=[Assessment('3.i',Verdict.SUSPECTED_FAIL)]))
    web.SESSIONS.clear()
    return tmp_path,web.load(tmp_path)

def test_save_human_confirmation_and_measurement(opened):
    root,d=opened
    web.save({'session':d['session'],'reviewer':'담당자', 'decisions':{'3.i':'approve'},
        'measurements':{'3.g':{'verdict':'fail','value':'5 mm','condition':'자 측정'}}})
    bundle=pipeline.load_bundle(root)
    assert bundle.measurements['3.g']['by']=='담당자'
    assert len(bundle.review['history'])==1
    a=pipeline.apply_review(rules.load(),bundle,[Assessment('3.i',Verdict.SUSPECTED_FAIL)])
    assert a[0].verdict is Verdict.FAIL
    assert d['session'] not in web.SESSIONS

def test_conflicting_edit_preserved(opened):
    root,d=opened
    original='{"schema":"kfa.review/1","reviewer":"다른 사람"}'
    (root/'review.json').write_text(original,encoding='utf-8')
    with pytest.raises(ValueError,match='다른 창'):
        web.save({'session':d['session'],'reviewer':'A','decisions':{'3.i':'approve'}})
    assert (root/'review.json').read_text(encoding='utf-8')==original

@pytest.mark.parametrize('changes',[
    {'reviewer':''}, {'decisions':{'1.a':'approve'}},
    {'decisions':{'3.i':'pass'}},
    {'measurements':{'3.g':{'verdict':'pass','value':'','condition':'x'}}},
    {'measurements':{'3.i':{'verdict':'pass','value':'5','condition':'x'}}}
])
def test_invalid_edits_do_not_write(opened,changes):
    root,d=opened
    data={'session':d['session'],'reviewer':'A'}
    data.update(changes)
    with pytest.raises(ValueError): web.save(data)
    assert not (root/'review.json').exists()

def test_reject_does_not_become_pass(opened):
    root,d=opened
    web.save({'session':d['session'],'reviewer':'A','decisions':{'3.i':'reject'}})
    a=pipeline.apply_review(rules.load(),pipeline.load_bundle(root),[Assessment('3.i',Verdict.SUSPECTED_FAIL)])
    assert a[0].verdict is Verdict.UNDETERMINED

def test_numeric_save_and_recompute(opened):
    root,d=opened
    web.save({'session':d['session'],'reviewer':'담당자','measurements':{'3.g':{
        'method':'numeric/1','values':{'height':7.25},'error_bound':.1,
        'condition':'현장','instrument':'눈금 확대경 및 교정 기록','scope':'필수 문자 전체',
        'coverage_complete':True,'confirmed':True,'verdict':'pass'}}})
    bundle=pipeline.load_bundle(root)
    rec=bundle.measurements['3.g']
    assert 'verdict' not in rec
    assert pipeline._measurements(rules.load(),bundle)[0].verdict is Verdict.UNDETERMINED
