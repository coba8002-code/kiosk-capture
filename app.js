/* 배리어프리 현장 수집 앱
 *
 * 이 앱의 역할은 촬영기가 아니라 **누락 방지 체크리스트**다.
 * 입력 품질이 곧 판정 품질이므로, 무엇을 왜 찍는지 알려주고
 * 빠진 컷이 있으면 그 결과가 리포트에 어떻게 나올지 먼저 보여준다.
 *
 * 데이터는 손으로 적지 않는다 — protocol.json 을 읽는다.
 * 그 파일은 rules/ 에서 생성되므로 앱과 엔진이 갈라지지 않는다.
 *
 * 저장: IndexedDB (사진은 localStorage 에 들어가지 않는다)
 * 출력: 무압축 ZIP — 엔진의 `KFA.exe ingest` 가 그대로 읽는다
 */
'use strict';

const App = {
  protocol: null,
  step: 1,
  device: { id: '', location: '', product_type: '중대형', scopes: [], exemptions: [] },
  captured_by: '',
  shots: [],        // {key, set, shot, name, type, blob, marker_plane, masked, marker_hint}
  answers: {},      // ruleId -> 'yes' | 'no' | 'na'
  openSet: null,
  pending: null,    // 마스킹 대기 중인 촬영
};

const STEPS = [
  { n: 1, title: '기기 등록' },
  { n: 2, title: '현장 촬영' },
  { n: 3, title: '점주 셀프 체크' },
  { n: 4, title: '제출 전 확인' },
];

const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false) continue;
    n.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
  }
  return n;
};

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

/* ─────────────────────────── 저장 (IndexedDB) ─────────────────────────── */

const DB = (() => {
  let db = null;
  function open() {
    return new Promise((res, rej) => {
      if (db) return res(db);
      const r = indexedDB.open('kfa-capture', 1);
      r.onupgradeneeded = () => {
        const d = r.result;
        if (!d.objectStoreNames.contains('shots')) d.createObjectStore('shots', { keyPath: 'key' });
        if (!d.objectStoreNames.contains('meta')) d.createObjectStore('meta', { keyPath: 'k' });
      };
      r.onsuccess = () => { db = r.result; res(db); };
      r.onerror = () => rej(r.error);
    });
  }
  const tx = async (store, mode) => (await open()).transaction(store, mode).objectStore(store);
  return {
    async putShot(s) { const o = await tx('shots', 'readwrite'); o.put(s); },
    async delShot(key) { const o = await tx('shots', 'readwrite'); o.delete(key); },
    async allShots() {
      const o = await tx('shots', 'readonly');
      return new Promise((res) => { const r = o.getAll(); r.onsuccess = () => res(r.result || []); });
    },
    async putMeta(k, v) { const o = await tx('meta', 'readwrite'); o.put({ k, v }); },
    async getMeta(k) {
      const o = await tx('meta', 'readonly');
      return new Promise((res) => { const r = o.get(k); r.onsuccess = () => res(r.result?.v); });
    },
    async clearAll() {
      const d = await open();
      await new Promise((res) => {
        const t = d.transaction(['shots', 'meta'], 'readwrite');
        t.objectStore('shots').clear(); t.objectStore('meta').clear();
        t.oncomplete = res;
      });
    },
  };
})();

async function saveState() {
  await DB.putMeta('state', {
    device: App.device, captured_by: App.captured_by,
    answers: App.answers, step: App.step,
  });
}

async function loadState() {
  const s = await DB.getMeta('state');
  if (s) {
    App.device = s.device || App.device;
    App.captured_by = s.captured_by || '';
    App.answers = s.answers || {};
  }
  App.shots = await DB.allShots();
}

/* ─────────────────────────── 진행 계산 ─────────────────────────── */

function shotsFor(shotId) { return App.shots.filter((s) => s.shot === shotId); }

function setStatus(set) {
  const required = set.shots.filter((sh) => !sh.repeat || true);
  const done = set.shots.filter((sh) => shotsFor(sh.id).length > 0).length;
  const total = set.shots.length;
  if (done === 0) return { state: 'empty', done, total };
  if (done < total) return { state: 'partial', done, total };
  return { state: 'done', done, total };
}

/** 지금 제출하면 판정되지 않는 항목들
 *
 * 반드시 **이번 진단 대상 안에서만** 센다. 예전에는 별표5 40항목 전체를 대상으로
 * 세서, '진단 대상 31항목' 아래에 '재촬영 39 · 점주 4 · 실측 7' 이 찍혔다.
 * 대상보다 큰 숫자가 나오는 화면은 읽는 사람이 믿지 않는다.
 *
 * 세 묶음은 서로 겹친다(한 항목이 재촬영이면서 실측일 수 있다). 그래서
 * 합계는 더하지 않고 합집합으로 센다 — 더하면 판정 가능 수가 실제보다 낮게 나온다.
 */
function undeterminedForecast() {
  const p = App.protocol;
  const inScope = new Set(applicableItems().map((r) => r.id));
  const keep = (ids) => [...new Set(ids)].filter((r) => inScope.has(r));

  const missingSets = [];
  const blocked = new Set();

  for (const set of p.sets) {
    const st = setStatus(set);
    if (set.required && st.state !== 'done') {
      missingSets.push({ set, ...st });
      for (const sh of set.shots) {
        if (shotsFor(sh.id).length === 0) (sh.required_for || []).forEach((r) => blocked.add(r));
      }
      if (st.state === 'empty') set.feeds.forEach((r) => blocked.add(r));
    }
  }

  const blockedRules = keep([...blocked]);
  const unanswered = keep(p.owner_questions.filter((q) => !App.answers[q.id]).map((q) => q.id));
  const measure = keep(p.measure_items.map((m) => m.id));
  const undetermined = new Set([...blockedRules, ...unanswered, ...measure]);

  return { missingSets, blockedRules, unanswered, measure, undetermined };
}

function applicableItems() {
  // 엔진의 RuleSet.applicable 과 같은 규칙으로 추린다.
  // 예전에는 base=26 과 조건부별 증가량을 앱이 손으로 들고 있었다.
  // 룰 DB 가 바뀌면 조용히 어긋나고, 실제로 '진단 대상 26' 아래에
  // 29·4·7 이 찍혀 합이 40 이 되는 화면이 나왔다. 이제 protocol.json 의
  // items 를 그대로 본다 — 단일 진실 원천은 언제나 rules/ 다.
  const scopes = new Set(['기본', ...App.device.scopes]);
  const exempt = new Set(
    (App.protocol.exemptions || [])
      .filter((e) => App.device.exemptions.includes(e.id))
      .flatMap((e) => e.exempts));
  return (App.protocol.items || []).filter((r) =>
    r.applies_to.includes(App.device.product_type)
    && scopes.has(r.scope)
    && !exempt.has(r.id));
}

function applicableCount() {
  return applicableItems().length;
}

function trackCounts() {
  // 이번 진단 대상 안에서만 센다. 전체 40항목 기준으로 세면
  // '대상 26 / AI 29' 처럼 대상보다 큰 숫자가 찍힌다.
  const t = { A_AI: 0, B_OWNER: 0, C_MEASURE: 0, D_USER: 0 };
  applicableItems().forEach((r) => { t[r.track] = (t[r.track] || 0) + 1; });
  return t;
}

/* ─────────────────────────── 화면 ─────────────────────────── */

function render() {
  const step = STEPS[App.step - 1];
  $('#step-label').textContent = `STEP ${step.n} / 4`;
  $('#step-title').textContent = step.title;
  $('#device-tag').textContent = App.device.id || '';
  $('#progress-fill').style.width = `${(App.step / 4) * 100}%`;

  const view = $('#view');
  view.innerHTML = '';
  view.scrollTop = 0;
  ({ 1: renderDevice, 2: renderCapture, 3: renderOwner, 4: renderSubmit })[App.step](view);
}

/* ── STEP 1 · 기기 등록 ── */
function renderDevice(view) {
  const wrap = el('div', { class: 'stack' });
  wrap.appendChild(el('p', { class: 'lede' },
    '진단할 키오스크의 기본 정보를 입력합니다. 여기서 고른 값이 평가 항목 수를 정합니다.'));

  const field = (label, key, ph) => {
    const g = el('div');
    g.appendChild(el('div', { class: 'label' }, label));
    g.appendChild(el('input', {
      type: 'text', value: App.device[key] || '', placeholder: ph,
      oninput: (e) => {
        App.device[key] = e.target.value;
        saveState();
        $('#device-tag').textContent = App.device.id;
        if (key === 'id') {
          const ready = App.device.id.trim().length > 0;
          const start = $('#start-capture');
          const note = $('#start-note');
          if (start) start.disabled = !ready;
          if (note) note.textContent = ready
            ? '마커 카드를 준비했는지 확인하세요. 없으면 치수 6항목이 판정되지 않습니다.'
            : '기기 ID를 입력해야 시작할 수 있습니다.';
        }
      },
    }));
    return g;
  };
  wrap.appendChild(field('기기 ID', 'id', '예: GB-CAFE-001'));
  wrap.appendChild(field('설치 장소', 'location', '예: 경북 구미시 · 무인카페 A'));

  const who = el('div');
  who.appendChild(el('div', { class: 'label' }, '촬영자'));
  who.appendChild(el('input', {
    type: 'text', value: App.captured_by, placeholder: '이름',
    oninput: (e) => { App.captured_by = e.target.value; saveState(); },
  }));
  wrap.appendChild(who);

  // 제품 구분
  const seg = el('div');
  seg.appendChild(el('div', { class: 'label' }, '제품 구분 · 화면 대각선'));
  const tiles = el('div', { class: 'tiles' });
  [['중대형', '28cm 초과'], ['소형', '28cm 이하']].forEach(([t, s]) => {
    tiles.appendChild(el('button', {
      class: 'tile', type: 'button', 'aria-pressed': App.device.product_type === t,
      onclick: () => { App.device.product_type = t; saveState(); render(); },
    }, el('strong', {}, t), el('small', {}, s)));
  });
  seg.appendChild(tiles);
  wrap.appendChild(seg);

  // 조건부 기능
  const cond = el('div');
  cond.appendChild(el('div', { class: 'label' }, '이 기기에 있는 기능 · 조건부 항목'));
  const chips = el('div', { class: 'chips' });
  App.protocol.scopes.forEach((s) => {
    const on = App.device.scopes.includes(s);
    chips.appendChild(el('button', {
      class: 'chip', type: 'button', 'aria-pressed': on,
      onclick: () => {
        App.device.scopes = on ? App.device.scopes.filter((x) => x !== s) : [...App.device.scopes, s];
        saveState(); render();
      },
    }, el('span', { class: 'mk' }, on ? '✓' : '+'), s));
  });
  cond.appendChild(chips);
  wrap.appendChild(cond);

  // 면제 경로 — 공공 민원 단말이면 6항목이 진단 범위에서 빠진다
  if (App.protocol.exemptions?.length) {
    const ex = el('div');
    ex.appendChild(el('div', { class: 'label' }, '면제 경로 · 해당하면 진단 범위가 줄어듭니다'));
    App.protocol.exemptions.forEach((e) => {
      const on = App.device.exemptions.includes(e.id);
      const card = el('div', { class: `card ${on ? 'sel' : ''}`, style: 'padding:13px' });
      card.appendChild(el('div', { style: 'font-size:14px;font-weight:600;margin-bottom:4px' }, e.name));
      card.appendChild(el('div', { style: 'font-size:12px;color:var(--muted);line-height:1.55' },
        `${e.exempts.length}항목 면제 — ${e.exempts.join(', ')}`));
      card.appendChild(el('div', { style: 'font-size:11.5px;color:var(--c);margin-top:5px' },
        `제출물: ${e.evidence_required}`));
      card.appendChild(el('button', {
        class: 'btn small', style: 'margin-top:10px',
        'aria-pressed': on,
        onclick: () => toggleExemption(e.id),
      }, on ? '✓ 적용 중 — 해제하기' : '적용하기'));
      ex.appendChild(card);
    });
    wrap.appendChild(ex);
  }

  // 평가 항목 수 요약
  const sum = el('div', { class: 'summary' });
  sum.appendChild(el('div', { class: 'sum-head' },
    el('span', {}, '이번 진단 대상'),
    el('b', {}, String(applicableCount())),
    el('small', {}, `/ 별표5 ${App.protocol.total_items}항목`)));
  const bars = el('div', { class: 'bars' });
  const t = trackCounts();
  // '자동 판정'이라고 쓰지 않는다. 촬영물 분석이 내는 것은 판정 후보이고,
  // 부적합 확정은 검토자만 한다. 앱이 여기서 과장하면 리포트의 절제가 무의미해진다.
  [['촬영물 분석', 'var(--a)', t.A_AI], ['점주 셀프 체크', 'var(--b)', t.B_OWNER],
   ['전문 실측', 'var(--c)', t.C_MEASURE]].forEach(([n, c, v]) => {
    bars.appendChild(el('div', { class: 'bar' },
      el('i', { style: `background:${c}` }), el('span', {}, n), el('b', {}, `${v}항목`)));
  });
  sum.appendChild(bars);
  wrap.appendChild(sum);

  view.appendChild(wrap);

  const ready = App.device.id.trim().length > 0;
  footer(
    el('button', { id: 'start-capture', class: 'btn primary', disabled: !ready, onclick: () => go(2) }, '촬영 시작'),
    el('div', { id: 'start-note', class: 'footnote' },
      ready ? '마커 카드를 준비했는지 확인하세요. 없으면 치수 6항목이 판정되지 않습니다.'
            : '기기 ID를 입력해야 시작할 수 있습니다.'));
}

/* ── STEP 2 · 촬영 ── */
function renderCapture(view) {
  const p = App.protocol;
  const wrap = el('div', { class: 'stack' });

  const totalShots = p.sets.reduce((n, s) => n + s.shots.length, 0);
  const doneShots = p.sets.reduce((n, s) => n + s.shots.filter((sh) => shotsFor(sh.id).length).length, 0);
  const reqDone = p.sets.filter((s) => s.required && setStatus(s).state === 'done').length;
  const reqTotal = p.sets.filter((s) => s.required).length;

  wrap.appendChild(el('div', { class: 'note info' },
    el('b', {}, `필수 세트 ${reqDone} / ${reqTotal} 완료`),
    ` · 컷 ${doneShots} / ${totalShots} · 마커는 재는 평면과 같은 면에 붙이세요.`));

  // 저장 공간 — 사진 24컷에 영상 4개면 폰 저장소가 실제로 찬다.
  // 다 찍고 제출하려는 순간 실패하면 현장을 다시 나가야 하므로 미리 알린다.
  const spaceBox = el('div', { class: 'note warn', hidden: true, id: 'space-warn' });
  wrap.appendChild(spaceBox);
  checkSpace(spaceBox);

  p.sets.forEach((set) => {
    const st = setStatus(set);
    const cls = st.state === 'done' ? 'done' : (set.required && st.state === 'empty' ? 'missing' : '');
    const row = el('button', {
      class: `setrow ${cls}`, type: 'button',
      onclick: () => { App.openSet = App.openSet === set.id ? null : set.id; render(); },
    },
      el('div', { class: 'setid' }, set.id),
      el('div', { class: 'setmid' },
        el('div', { class: 'setname' }, set.name,
          set.marker ? el('span', { class: 'badge mk' }, '마커') : null,
          !set.required ? el('span', { class: 'badge opt' }, '선택') : null),
        el('div', { class: 'setmeta' },
          `${st.done} / ${st.total}컷  ·  ${set.feeds.length ? `별표5 ${set.feeds.length}항목 기여` : '참고 수집'}`)),
      el('span', { class: `badge ${st.state === 'done' ? 'ok' : st.state === 'partial' ? 'go' : 'no'}` },
        st.state === 'done' ? '완료' : st.state === 'partial' ? '진행중' : '미촬영'));
    wrap.appendChild(row);

    if (App.openSet === set.id) {
      const card = el('div', { class: 'card' });
      if (set.constraints && Object.keys(set.constraints).length) {
        card.appendChild(el('div', { class: 'note warn', style: 'margin-bottom:10px' },
          Object.entries(set.constraints).map(([k, v]) => `${k}: ${v}`).join(' · ')));
      }
      set.shots.forEach((sh) => card.appendChild(renderShot(set, sh)));
      wrap.appendChild(card);
    }
  });

  view.appendChild(wrap);
  footer(
    el('div', { class: 'row' },
      el('button', { class: 'btn', onclick: () => go(1) }, '이전'),
      el('button', { class: 'btn primary', onclick: () => go(3) }, '점주 확인으로')));
}

function renderShot(set, sh) {
  const mine = shotsFor(sh.id);
  const box = el('div', { class: 'shot' });
  const body = el('div', { class: 'shot-body' });
  body.appendChild(el('div', { class: 'shot-id' }, sh.id));
  body.appendChild(el('div', { class: 'shot-label' }, sh.label));
  if (sh.note) body.appendChild(el('div', { class: 'shot-note' }, sh.note));
  if (sh.required_for?.length) {
    body.appendChild(el('div', { class: 'shot-req' },
      `이 컷이 없으면 판정 불가: ${sh.required_for.join(', ')}`));
  }

  if (mine.length) {
    const thumbs = el('div', { class: 'thumbs' });
    mine.forEach((s) => {
      const t = el('div', { class: 'thumb' });
      const url = URL.createObjectURL(s.blob);
      t.appendChild(s.type === 'video'
        ? el('video', { src: url, muted: true, playsinline: true })
        : el('img', { src: url, alt: s.name }));
      if (s.marker_plane) {
        t.appendChild(el('div', { class: `mk ${s.marker_hint === 'ok' ? 'ok' : 'no'}` },
          s.marker_hint === 'ok' ? '마커' : '확인'));
      }
      t.appendChild(el('button', {
        class: 'x', type: 'button', 'aria-label': '삭제',
        onclick: async (e) => {
          e.stopPropagation();
          await DB.delShot(s.key);
          App.shots = App.shots.filter((x) => x.key !== s.key);
          render();
        },
      }, '×'));
      thumbs.appendChild(t);
    });
    body.appendChild(thumbs);
  }

  const btn = el('button', {
    class: 'btn small ' + (mine.length ? '' : 'accent'),
    onclick: () => capture(set, sh),
  }, mine.length ? '추가 촬영' : (set.medium === 'video' ? '영상 촬영'
    : set.medium === 'audio' ? '녹음' : set.medium === 'document' ? '파일 선택' : '촬영'));

  box.appendChild(body);
  box.appendChild(el('div', {}, btn));
  return box;
}

/* ── STEP 3 · 점주 체크 ── */
function renderOwner(view) {
  const wrap = el('div', { class: 'stack' });
  wrap.appendChild(el('p', { class: 'lede' },
    '카메라가 볼 수 없는 동작입니다. 직접 해보고 답해 주세요. 미응답은 리포트에 ‘점주 확인 필요’로 남습니다.'));

  App.protocol.owner_questions.forEach((q) => {
    const cur = App.answers[q.id];
    const card = el('div', { class: `card ${cur ? 'sel' : ''}` });
    card.appendChild(el('div', { style: 'display:flex;gap:8px;align-items:center;margin-bottom:8px' },
      el('span', { class: 'badge' }, q.id), el('strong', {}, q.name)));
    card.appendChild(el('div', { style: 'font-size:14px;color:var(--ink-2);line-height:1.65' }, q.question));

    const row = el('div', { class: 'answers' });
    const opts = [['예', 'yes'], ['아니오', 'no']];
    if (q.options.length > 2) opts.push(['해당 없음', 'na']);
    opts.forEach(([label, val]) => {
      row.appendChild(el('button', {
        class: val, type: 'button', 'aria-pressed': cur === val,
        onclick: () => {
          App.answers[q.id] = App.answers[q.id] === val ? undefined : val;
          if (!App.answers[q.id]) delete App.answers[q.id];
          saveState(); render();
        },
      }, label));
    });
    card.appendChild(row);
    wrap.appendChild(card);
  });

  const answered = Object.keys(App.answers).length;
  wrap.appendChild(el('div', { class: 'note info' },
    `${answered} / ${App.protocol.owner_questions.length} 응답`));

  view.appendChild(wrap);
  footer(el('div', { class: 'row' },
    el('button', { class: 'btn', onclick: () => go(2) }, '이전'),
    el('button', { class: 'btn primary', onclick: () => go(4) }, '제출 검토로')));
}

/* ── STEP 4 · 제출 ── */
function renderSubmit(view) {
  const f = undeterminedForecast();
  const wrap = el('div', { class: 'stack' });
  wrap.appendChild(el('p', { class: 'lede' }, '이 상태로 제출하면 리포트가 이렇게 나옵니다.'));

  const total = applicableCount();
  // 세 묶음은 겹친다. 더하지 않고 합집합으로 센다.
  const judged = Math.max(0, total - f.undetermined.size);

  const sum = el('div', { class: 'summary' });
  sum.appendChild(el('div', { class: 'sum-head' },
    el('span', {}, '판정 가능 예상'), el('b', {}, String(judged)),
    el('small', {}, `/ 진단 대상 ${total}항목`)));
  const bars = el('div', { class: 'bars' });
  [['자동 판정 시도', 'var(--a)', judged],
   ['현장 재촬영 필요', 'var(--bad)', f.blockedRules.length],
   ['점주 확인 필요', 'var(--b)', f.unanswered.length],
   ['전문 실측 필요', 'var(--c)', f.measure.length]].forEach(([n, c, v]) => {
    bars.appendChild(el('div', { class: 'bar' },
      el('i', { style: `background:${c}` }), el('span', {}, n), el('b', {}, `${v}항목`)));
  });
  sum.appendChild(bars);
  wrap.appendChild(sum);

  if (f.missingSets.length) {
    const card = el('div', { class: 'card bad' });
    card.appendChild(el('div', { class: 'label', style: 'color:var(--bad)' }, '빠진 필수 세트'));
    f.missingSets.forEach((m) => {
      card.appendChild(el('div', { style: 'font-size:14px;margin:5px 0' },
        `${m.set.id} ${m.set.name} — ${m.done}/${m.total}컷`));
    });
    if (f.blockedRules.length) {
      card.appendChild(el('div', { class: 'note bad', style: 'margin-top:9px' },
        `판정 불가: ${f.blockedRules.slice(0, 14).join(', ')}${f.blockedRules.length > 14 ? ' 외' : ''}`));
    }
    wrap.appendChild(card);
  }

  if (f.unanswered.length) {
    wrap.appendChild(el('div', { class: 'note warn' },
      `점주 문항 ${f.unanswered.length}개 미응답 — ${f.unanswered.join(', ')}`));
  }

  wrap.appendChild(el('div', { class: 'note info' },
    el('b', {}, '전문 실측 ' + f.measure.length + '항목'),
    ' — 촬영으로는 판정되지 않습니다. 실측 업체에 의뢰하세요.'));

  const masked = App.shots.filter((s) => s.masked).length;
  wrap.appendChild(el('div', { class: 'note ok' },
    el('b', {}, '개인정보'), ` — ${App.shots.length}건 중 ${masked}건 가림 처리. 원본은 전송되지 않습니다.`));

  view.appendChild(wrap);

  const ok = App.shots.length > 0 && App.device.id.trim();
  footer(
    el('button', {
      class: 'btn accent', disabled: !ok,
      onclick: async (e) => {
        // 파일이 많으면 ZIP 만드는 데 몇 초 걸린다. 아무 반응이 없으면
        // 촬영자가 다시 누르고, 그러면 같은 일을 두 번 한다.
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = '번들 만드는 중…';
        try {
          await exportBundle();
        } catch (err) {
          toast(`번들을 만들지 못했습니다: ${err.message || err}`, 8000);
        } finally {
          btn.disabled = false;
          btn.textContent = `번들 다시 만들기 (${App.shots.length}개 파일)`;
        }
      },
    }, `번들 내려받기 (${App.shots.length}개 파일)`),
    el('div', { class: 'row' },
      el('button', { class: 'btn', onclick: () => go(2) }, '부족한 컷 채우기'),
      el('button', { class: 'btn', onclick: resetAll }, '처음부터')),
    el('div', { class: 'footnote' }, '내려받은 ZIP 을 진단 PC 에서 KFA.exe ingest 로 여세요.'));
}

/* ─────────────────────── 내보내기 결과 ─────────────────────── */

/** ZIP 을 만든 뒤 저장 수단을 보여준다.
 *
 * 저장 방식이 기기마다 다르므로 하나만 믿지 않는다.
 *   · 공유 시트  — 아이폰의 정식 경로. 파일 앱·카톡·메일·드라이브로 보낼 수 있다
 *   · 내려받기   — 안드로이드·PC 의 정식 경로
 *   · 링크 유지  — 둘 다 막혔을 때 길게 눌러 저장. 화면을 떠나기 전까지 살아 있다
 *
 * 그리고 **성공했다고 말하지 않는다.** 브라우저는 저장 성공 여부를 알려주지 않는다.
 * 확인은 촬영자가 파일 앱에서 하는 것이고, 앱은 그렇게 안내만 한다.
 */
function showExportResult(blob, name) {
  const url = URL.createObjectURL(blob);
  const mb = (blob.size / 1024 / 1024).toFixed(1);
  const file = new File([blob], name, { type: 'application/zip' });
  const canShare = !!(navigator.canShare && navigator.canShare({ files: [file] }));

  const box = el('div', { class: 'export-done' });
  box.appendChild(el('div', { class: 'ttl' }, `${name} · ${mb}MB 준비됨`));
  box.appendChild(el('div', { class: 'sub' },
    '아래 버튼을 눌러 저장하세요. 저장하기 전에는 이 화면을 닫지 마세요.'));

  const row = el('div', { class: 'row' });
  if (canShare) {
    row.appendChild(el('button', {
      class: 'btn accent',
      onclick: async () => {
        try {
          await navigator.share({ files: [file], title: name });
        } catch (e) {
          if (e.name !== 'AbortError') {
            toast('공유를 열지 못했습니다. 아래 링크를 길게 눌러 저장하세요.', 6000);
          }
        }
      },
    }, '공유 · 파일 앱에 저장'));
  }
  row.appendChild(el('a', {
    class: 'btn', href: url, download: name,
    // 새로 누르는 것이므로 제스처가 살아 있다
  }, canShare ? '내려받기' : '내려받기 (파일 저장)'));
  box.appendChild(row);

  box.appendChild(el('div', { class: 'hint' },
    '저장이 안 되면 위 ',
    el('b', {}, '내려받기'),
    ' 를 ',
    el('b', {}, '길게 눌러'),
    ' 저장하세요. 아이폰은 ',
    el('b', {}, '파일 앱 → 다운로드'),
    ' 에 들어갑니다.'));
  box.appendChild(el('div', { class: 'hint' },
    '촬영본은 폰에 그대로 남아 있습니다. 저장이 안 되면 이 화면에서 다시 시도하면 됩니다.'));

  const view = $('#view');
  const old = view.querySelector('.export-done');
  if (old) old.remove();
  view.insertBefore(box, view.firstChild);
  view.scrollTop = 0;
}

function footer(...nodes) {
  const b = $('#bottombar');
  b.innerHTML = '';
  nodes.flat().forEach((n) => b.appendChild(n));
}

function toggleExemption(id) {
  const on = App.device.exemptions.includes(id);
  App.device.exemptions = on
    ? App.device.exemptions.filter((x) => x !== id)
    : [...App.device.exemptions, id];
  saveState();
  render();
  toast(on ? '면제를 해제했습니다.' : '면제를 적용했습니다. 해당 항목은 진단에서 빠집니다.');
}

function go(step) { App.step = step; saveState(); render(); }

async function resetAll() {
  if (!confirm('촬영물과 입력을 모두 지웁니다. 계속할까요?')) return;
  await DB.clearAll();
  App.shots = []; App.answers = {}; App.device = { id: '', location: '', product_type: '중대형', scopes: [], exemptions: [] };
  App.captured_by = ''; App.step = 1;
  render();
}

/* ─────────────────────────── 촬영 ─────────────────────────── */

function capture(set, sh) {
  const input = set.medium === 'video' ? $('#capture-video')
    : set.medium === 'audio' ? $('#capture-audio')
    : set.medium === 'document' ? $('#capture-any')
    : $('#capture-input');

  App.pending = { set, sh };
  input.value = '';
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    if (set.medium === 'photo') {
      openMask(file);                 // 사진은 가리기 단계를 거친다
    } else {
      await storeShot(file, false);
    }
  };
  input.click();
}

async function storeShot(blob, masked) {
  const { set, sh } = App.pending;
  const n = shotsFor(sh.id).length + 1;
  // 확장자는 **실제 파일**에서 정한다. 세트만 보고 정하면 안 된다 —
  // iPhone 에는 음성 녹음기가 없어서 S5(음성)에서 영상으로 녹화하게 되는데,
  // 세트만 보면 그 mp4 를 .m4a 로 저장해 버린다. 진단 PC 는 확장자로 갈라 읽으므로
  // 그렇게 저장된 파일은 아무도 읽지 않는 파일이 된다.
  const ext = extFor(blob, set.medium);
  const type = typeFor(blob, set.medium);

  const plane = set.marker_planes?.[0] || null;
  const rec = {
    key: `${sh.id}_${Date.now()}`,
    set: set.id, shot: sh.id,
    name: `${sh.id}_${String(n).padStart(3, '0')}.${ext}`,
    type, blob, marker_plane: set.marker ? plane : null,
    masked, marker_hint: null,
  };

  if (set.marker && type === 'photo') {
    rec.marker_hint = await quickMarkerHint(blob) ? 'ok' : 'check';
  }

  try {
    await DB.putShot(rec);
  } catch (e) {
    // 용량이 차면 여기서 터진다. 조용히 넘어가면 촬영자는 찍힌 줄 알고
    // 현장을 떠나고, 제출할 때가 되어서야 컷이 비어 있는 것을 안다.
    toast('저장 실패 — 폰 저장 공간이 부족할 수 있습니다. '
          + '지금까지 찍은 것을 먼저 제출하고 이어서 진행하세요.', 8000);
    App.pending = null;
    render();
    return;
  }
  App.shots.push(rec);
  App.pending = null;
  render();
  if (rec.marker_hint === 'check') {
    toast('마커가 잘 보이지 않습니다. 더 가까이·정면으로 다시 찍어 주세요.', 4200);
  }
}

/** 남은 저장 공간을 확인해 부족하면 알린다.
 *
 * navigator.storage.estimate() 는 안드로이드·아이폰 모두 지원한다.
 * 아이폰은 실제 여유보다 보수적인 값을 주는 경향이 있어, 임계값을 낮게 잡았다.
 * 못 읽는 브라우저에서는 아무 말도 하지 않는다 — 근거 없는 경고는 무시를 부른다.
 */
async function checkSpace(box) {
  if (!navigator.storage?.estimate) return;
  try {
    const { usage = 0, quota = 0 } = await navigator.storage.estimate();
    if (!quota) return;
    const freeMB = (quota - usage) / 1024 / 1024;
    if (freeMB > 400) return;
    box.hidden = false;
    box.textContent = freeMB > 150
      ? `저장 여유 ${Math.round(freeMB)}MB — 영상 몇 개면 찹니다. `
        + '중간에 한 번 제출하고 이어서 찍는 편이 안전합니다.'
      : `저장 여유 ${Math.round(freeMB)}MB — 부족합니다. `
        + '지금까지 찍은 것을 먼저 제출하고, 폰 저장 공간을 비운 뒤 이어가세요.';
  } catch (e) { /* 못 읽으면 말하지 않는다 */ }
}

/** ZIP 파일명에 쓸 ASCII 이름.
 *
 * iPhone Safari 는 blob 내려받기에서 한글 파일명을 흘리거나 이름을 바꿔 버린다.
 * 그렇다고 한글을 그냥 지우면 '구미 무인카페 A' 가 'A' 가 되어 촬영자가
 * 무슨 파일인지 못 알아본다. 그럴 바에는 CAPTURE 가 낫다 —
 * 기기 ID 는 안의 manifest 에 정확히 들어가므로 잃는 정보가 없다.
 */
function safeFileId(id) {
  const raw = (id || '').trim();
  const ascii = raw.replace(/[^A-Za-z0-9._-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
  const kept = ascii.replace(/-/g, '').length;
  if (!raw || kept < 3 || kept < raw.replace(/\s/g, '').length / 2) return 'CAPTURE';
  return ascii;
}

/** 실제 MIME·파일명에서 확장자를 정한다. 세트가 기대하는 것과 다를 수 있다. */
function extFor(blob, medium) {
  const mime = (blob.type || '').toLowerCase();
  if (mime.startsWith('image/')) return mime.includes('png') ? 'png' : 'jpg';
  if (mime.startsWith('video/')) return mime.includes('quicktime') ? 'mov' : 'mp4';
  if (mime.startsWith('audio/')) return mime.includes('mpeg') ? 'mp3' : 'm4a';
  const fromName = (blob.name || '').split('.').pop();
  if (fromName && fromName.length <= 5 && !fromName.includes('/')) return fromName.toLowerCase();
  return medium === 'video' ? 'mp4' : medium === 'audio' ? 'm4a'
    : medium === 'document' ? 'pdf' : 'jpg';
}

/** 진단 PC 가 무엇으로 읽을지. 이것도 실제 파일을 따른다. */
function typeFor(blob, medium) {
  const mime = (blob.type || '').toLowerCase();
  if (mime.startsWith('image/')) return 'photo';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  return medium === 'video' ? 'video' : medium === 'audio' ? 'audio'
    : medium === 'document' ? 'doc' : 'photo';
}

/**
 * 마커 예비 확인 — 정식 검출은 진단 PC 가 한다.
 *
 * 여기서 하는 것은 "마커를 아예 안 찍었거나 너무 작다"를 현장에서 잡는 일이다.
 * 밝기를 이진화해 큰 검은 정사각 덩어리가 있는지만 본다.
 * ArUco 디코딩은 하지 않으므로 '검출됨'을 보장하지 않는다 — 그래서 이름이 hint 다.
 */
async function quickMarkerHint(blob) {
  try {
    const bmp = await createImageBitmap(blob);
    const W = 480, H = Math.round(bmp.height * (480 / bmp.width));
    const cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(bmp, 0, 0, W, H);
    const d = ctx.getImageData(0, 0, W, H).data;

    // 어두운 픽셀 마스크
    const dark = new Uint8Array(W * H);
    for (let i = 0, p = 0; i < d.length; i += 4, p++) {
      const l = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      dark[p] = l < 90 ? 1 : 0;
    }
    // 연결 요소 중 가장 큰 것의 채움율·정사각도
    const seen = new Uint8Array(W * H);
    const stack = new Int32Array(W * H);
    let best = 0, bestBox = null;
    for (let p = 0; p < W * H; p++) {
      if (!dark[p] || seen[p]) continue;
      let sp = 0, n = 0;
      let x0 = W, y0 = H, x1 = 0, y1 = 0;
      stack[sp++] = p; seen[p] = 1;
      while (sp) {
        const q = stack[--sp]; n++;
        const x = q % W, y = (q / W) | 0;
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
        if (x > 0 && dark[q - 1] && !seen[q - 1]) { seen[q - 1] = 1; stack[sp++] = q - 1; }
        if (x < W - 1 && dark[q + 1] && !seen[q + 1]) { seen[q + 1] = 1; stack[sp++] = q + 1; }
        if (y > 0 && dark[q - W] && !seen[q - W]) { seen[q - W] = 1; stack[sp++] = q - W; }
        if (y < H - 1 && dark[q + W] && !seen[q + W]) { seen[q + W] = 1; stack[sp++] = q + W; }
      }
      if (n > best) { best = n; bestBox = [x0, y0, x1 - x0 + 1, y1 - y0 + 1]; }
    }
    if (!bestBox) return false;
    const [, , bw, bh] = bestBox;
    const fill = best / (bw * bh);
    const square = Math.min(bw, bh) / Math.max(bw, bh);
    const sizeOk = bw / W > 0.05;          // 프레임 대비 5% 이상
    return sizeOk && square > 0.6 && fill > 0.35;
  } catch {
    return true;    // 확인 불가면 통과시킨다 — 앱이 촬영을 막지는 않는다
  }
}

/* ─────────────────────────── 가리기(마스킹) ─────────────────────────── */

const Mask = { canvas: null, ctx: null, drawing: false, size: 60, history: [], src: null };

async function openMask(file) {
  const bmp = await createImageBitmap(file);
  const max = 1600;
  const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const W = Math.round(bmp.width * scale), H = Math.round(bmp.height * scale);

  const cv = $('#mask-canvas');
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  ctx.drawImage(bmp, 0, 0, W, H);

  Mask.canvas = cv; Mask.ctx = ctx; Mask.history = [ctx.getImageData(0, 0, W, H)];
  Mask.src = file;
  $('#mask-overlay').hidden = false;
}

function maskAt(x, y) {
  const ctx = Mask.ctx, r = Mask.size / 2;
  // 픽셀화 — 흐림보다 확실하고 되돌릴 수 없다
  const x0 = Math.max(0, Math.round(x - r)), y0 = Math.max(0, Math.round(y - r));
  const w = Math.min(Mask.canvas.width - x0, Math.round(Mask.size));
  const h = Math.min(Mask.canvas.height - y0, Math.round(Mask.size));
  if (w <= 0 || h <= 0) return;
  const img = ctx.getImageData(x0, y0, w, h);
  const d = img.data;
  let r0 = 0, g0 = 0, b0 = 0;
  for (let i = 0; i < d.length; i += 4) { r0 += d[i]; g0 += d[i + 1]; b0 += d[i + 2]; }
  const n = d.length / 4;
  r0 = (r0 / n) | 0; g0 = (g0 / n) | 0; b0 = (b0 / n) | 0;
  for (let i = 0; i < d.length; i += 4) { d[i] = r0; d[i + 1] = g0; d[i + 2] = b0; }
  ctx.putImageData(img, x0, y0);
}

function bindMask() {
  const cv = $('#mask-canvas');
  const pos = (e) => {
    const r = cv.getBoundingClientRect();
    const t = e.touches?.[0] || e;
    return [(t.clientX - r.left) * (cv.width / r.width), (t.clientY - r.top) * (cv.height / r.height)];
  };
  const down = (e) => {
    e.preventDefault();
    Mask.history.push(Mask.ctx.getImageData(0, 0, cv.width, cv.height));
    if (Mask.history.length > 12) Mask.history.shift();
    Mask.drawing = true; maskAt(...pos(e));
  };
  const move = (e) => { if (Mask.drawing) { e.preventDefault(); maskAt(...pos(e)); } };
  const up = () => { Mask.drawing = false; };

  cv.addEventListener('pointerdown', down);
  cv.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);

  $('#mask-size').addEventListener('input', (e) => { Mask.size = +e.target.value; });
  $('#mask-undo').addEventListener('click', () => {
    if (Mask.history.length > 1) Mask.history.pop();
    Mask.ctx.putImageData(Mask.history[Mask.history.length - 1], 0, 0);
  });
  $('#mask-cancel').addEventListener('click', () => {
    $('#mask-overlay').hidden = true; App.pending = null;
  });
  $('#mask-skip').addEventListener('click', () => finishMask(false));
  $('#mask-done').addEventListener('click', () => finishMask(true));
}

function finishMask(masked) {
  Mask.canvas.toBlob(async (blob) => {
    $('#mask-overlay').hidden = true;
    await storeShot(blob, masked);
  }, 'image/jpeg', 0.92);
}

/* ─────────────────────────── 번들 내보내기 ─────────────────────────── */

async function exportBundle() {
  const files = [];
  const shots = [];

  for (const s of App.shots) {
    const path = `${s.set}/${s.name}`;
    files.push({ name: path, data: new Uint8Array(await s.blob.arrayBuffer()) });
    shots.push({
      set: s.set, shot: s.shot, file: path,
      marker_plane: s.marker_plane, masked: !!s.masked,
      note: s.marker_hint === 'check' ? '마커 예비확인 실패' : '',
    });
  }

  const manifest = {
    schema: App.protocol.schema,
    device: {
      id: App.device.id.trim(),
      location: App.device.location.trim(),
      product_type: App.device.product_type,
      scopes: App.device.scopes,
      exemptions: App.device.exemptions,
    },
    captured_at: new Date().toISOString(),
    captured_by: App.captured_by.trim(),
    protocol_version: App.protocol.protocol_version,
    app_version: '0.2.0',
    shots,
    owner_answers: App.answers,
    measurements: {},
  };
  files.unshift({
    name: 'manifest.json',
    data: new TextEncoder().encode(JSON.stringify(manifest, null, 2)),
  });

  const blob = KfaZip.createZip(files);
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  // 파일명은 ASCII 로 만든다. iPhone Safari 는 blob 내려받기에서 한글 파일명을
  // 흘리거나 이름을 통째로 바꿔 버리는 일이 있다. 기기 ID 는 안에 manifest 로
  // 정확히 들어가므로 파일명이 단순해도 잃는 정보가 없다.
  const name = `${safeFileId(manifest.device.id)}_${stamp}.zip`;

  // **여기서 바로 내려받지 않는다.**
  //
  // 위에서 파일마다 await 을 걸었기 때문에, 이 지점은 이미 사용자 제스처가
  // 끝난 뒤다. iOS 사파리는 제스처가 끊긴 다음의 다운로드·공유를 조용히 막는다.
  // 예전 코드는 그 상태로 a.click() 을 부르고 "저장됨" 이라고 알렸다 —
  // 촬영자는 저장된 줄 알고 현장을 떠나는데 파일은 없다.
  //
  // 그래서 만들기만 하고, 저장은 **사용자가 다시 누르는 새 제스처**로 넘긴다.
  showExportResult(blob, name);
}

/* ─────────────────────────── 시작 ─────────────────────────── */

(async function init() {
  try {
    const res = await fetch('protocol.json', { cache: 'no-cache' });
    App.protocol = await res.json();
  } catch {
    document.body.innerHTML =
      '<div style="padding:28px;font-family:sans-serif;line-height:1.7">' +
      '<h2>protocol.json 을 읽지 못했습니다</h2>' +
      '<p>진단 PC 에서 <code>KFA.exe build-app</code> 을 실행해 파일을 만든 뒤, ' +
      'app 폴더 전체를 웹 서버로 열어야 합니다. 파일을 직접 열면(file://) ' +
      '브라우저 보안 정책 때문에 읽을 수 없습니다.</p></div>';
    return;
  }

  await loadState();
  bindMask();
  render();

  registerOffline();
})();

/* 오프라인 저장 — 되는지 안 되는지를 **화면에 말한다**
 *
 * 서비스 워커는 보안 컨텍스트(https 또는 localhost)에서만 등록된다.
 * 폰이 진단 PC 에 붙는 주소는 http://192.168.x.x 라서 보안 컨텍스트가 아니고,
 * 실측해 보면 navigator.serviceWorker 가 **아예 없다.**
 *
 * 그런데 안내문은 '한 번 열어두면 신호가 없어도 동작한다'고 적혀 있었다.
 * 촬영자가 그 말을 믿고 가게 밖으로 나가면 앱이 안 열린다.
 * 찍어 둔 사진은 IndexedDB 에 남으므로 잃지 않지만, 앱 자체가 뜨지 않는다.
 *
 * 그래서 조용히 실패하지 않고, 무엇이 되고 무엇이 안 되는지 띠로 알린다.
 */
async function registerOffline() {
  if ('serviceWorker' in navigator) {
    try {
      await navigator.serviceWorker.register('sw.js');
      return;                                   // 오프라인 저장 가능
    } catch (e) { /* 아래 안내로 내려간다 */ }
  }
  const why = window.isSecureContext === false
    ? `이 주소(${location.host})를 브라우저가 보안 연결로 보지 않아 앱을 폰에 저장할 수 없습니다.`
    : '이 브라우저가 오프라인 저장을 지원하지 않습니다.';
  const bar = el('div', { class: 'offline-warn', role: 'status' },
    el('b', { class: 'ttl' }, '오프라인 저장이 켜지지 않았습니다'),
    el('span', {}, why + ' 촬영은 그대로 되고 찍은 것도 폰에 남지만, '),
    el('b', {}, '진단 PC 의 와이파이 범위를 벗어나면 앱이 다시 열리지 않습니다.'),
    el('span', {}, ' 제출(ZIP 내려받기)까지 마친 뒤 이동하세요.'));
  document.body.insertBefore(bar, document.body.firstChild);
}
