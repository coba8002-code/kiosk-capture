import json
import zipfile
import pytest

from tools import easy
from engine import pipeline
from engine.l2.anthropic_provider import parse
from engine.l2.provider import Judgment
from engine.verdict import Verdict


@pytest.mark.parametrize('name', ['../outside.txt', '../capture-other/x', '/absolute',
    'C:/temp/x', 'S1/file:stream', 'S1/../x', 'S1/trailing.'])
def test_reject_unsafe_archive(tmp_path, monkeypatch, name):
    monkeypatch.setattr(easy, 'output_dir', lambda: tmp_path / 'out')
    z = tmp_path / 'capture.zip'
    with zipfile.ZipFile(z, 'w') as f:
        f.writestr('manifest.json', '{}')
        f.writestr(name, 'bad')
    with pytest.raises(easy.UnsafeZip):
        easy.extract(z)


def test_reimport_does_not_inherit_review(tmp_path, monkeypatch):
    monkeypatch.setattr(easy, 'output_dir', lambda: tmp_path / 'out')
    z = tmp_path / 'capture.zip'
    with zipfile.ZipFile(z, 'w') as f:
        f.writestr('manifest.json', '{}')
    first = easy.extract(z)
    (first / 'review.json').write_text('{}')
    second = easy.extract(z)
    assert first != second
    assert not (second / 'review.json').exists()
    assert (first / 'review.json').exists()


@pytest.mark.parametrize('file', ['../outside.jpg', 7, '.'])
def test_bundle_rejects_external_or_invalid_file(tmp_path, file):
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'schema': pipeline.SCHEMA,
        'device': {'id': 'TEST', 'product_type': '소형'},
        'shots': [{'file': file}]
    }), encoding='utf-8')
    with pytest.raises(pipeline.BundleError):
        pipeline.load_bundle(tmp_path)


@pytest.mark.parametrize('confidence', [float('nan'), float('inf'), -1, 1.1])
def test_invalid_model_confidence_rejected(confidence):
    with pytest.raises(ValueError):
        Judgment(Verdict.FAIL, confidence, 'reason')
    response = json.dumps({'verdict': '위반', 'confidence': confidence,
                          'rationale': 'reason', 'cited': ['real.jpg']})
    assert parse(response, provider='test', fallback_cited=['real.jpg']) is None


@pytest.mark.parametrize('cited', [[], 'real.jpg', ['invented.jpg'], [3]])
def test_model_cannot_invent_evidence(cited):
    response = json.dumps({'verdict': '위반', 'confidence': .9,
                          'rationale': 'reason', 'cited': cited})
    assert parse(response, provider='test', fallback_cited=['real.jpg']) is None


def test_model_valid_evidence_kept():
    response = json.dumps({'verdict': '위반', 'confidence': .9,
                          'rationale': 'reason', 'cited': ['real.jpg']})
    assert parse(response, provider='test', fallback_cited=['real.jpg']).cited == ['real.jpg']
