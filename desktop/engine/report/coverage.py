"""Explain collected evidence without equating file counts to coverage."""
from collections import Counter
from html import escape

def render(run):
    counts=Counter(s.set_id for s in run.bundle.shots)
    rows=''.join('<tr><td>'+escape(s)+'</td><td>'+str(counts[s])+'</td></tr>' for s in ('S1','S2','S3','S4','S5','S6'))
    notes=''.join('<li>'+escape(str(s))+'</li>' for s in run.skipped)
    return ('<section style="page-break-before:always;padding:24px"><h2>검사 범위와 측정 근거</h2>'
      '<p>아래 숫자는 수집 파일 수입니다. 기능 검사 완료율이나 기준 충족률이 아닙니다.</p>'
      '<table><tr><th>촬영 세트</th><th>수집 파일 수</th></tr>'+rows+'</table>'
      '<p>사진 분석은 검출된 영역에 한정됩니다. 원본 화면 색상·실제 조작 순서·누락된 화면을 모두 검증한 결과가 아닙니다. '
      '음성·영상의 의미 분석과 실제 장애인·고령자 사용성 시험은 이 보고서로 대체되지 않습니다.</p>'
      '<p>수치 실측 비교의 오차 한계는 측정자가 입력한 값입니다. 교정 성적서의 불확실성 또는 AI 확신도와 같지 않습니다. '
      '오차 범위가 기준에 걸치면 재측정하고, 적합 확정에는 전체 대상 확인과 담당자의 확인이 필요합니다.</p>'
      '<h3>이번 실행에서 자동 확인하지 못한 사항</h3><ul>'+notes+'</ul></section>')
