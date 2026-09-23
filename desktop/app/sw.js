/* 오프라인 캐시 — 현장은 신호가 약하다.
 *
 * 먼저 알아둘 것: 이 워커는 **폰에서는 등록되지 않는다.**
 * 서비스 워커는 보안 컨텍스트(https 또는 localhost)에서만 살아나는데,
 * 폰이 진단 PC 에 붙는 주소는 http://192.168.x.x 라 해당하지 않는다.
 * 그래서 지금 이 파일이 실제로 도는 곳은 진단 PC 의 브라우저뿐이고,
 * 폰에는 app.js 의 registerOffline() 이 그 사실을 띠로 알린다.
 * 나중에 https 로 서비스하게 되면 아래 전략이 그대로 쓰인다.
 *
 * 두 가지를 동시에 만족해야 한다.
 *   ① 신호가 없어도 앱이 즉시 떠야 한다      → 캐시에서 먼저 준다
 *   ② 고친 앱이 현장 폰에 반드시 도달해야 한다 → 준 뒤에 몰래 새로 받아 둔다
 *
 * 예전에는 셸을 캐시 우선으로만 줬다(cache || fetch). 그러면 한 번 저장된
 * app.js 가 영원히 남는다 — **앱 버그를 고쳐도 현장 폰은 낡은 앱을 계속 쓴다.**
 * 캐시 이름(kfa-app-vN)을 올리는 것으로만 갱신되는데, 그건 사람이 잊는다.
 *
 * 그래서 stale-while-revalidate 로 바꿨다.
 *   · 캐시에 있으면 그것을 즉시 돌려준다 (오프라인·저신호에서 빠르다)
 *   · 동시에 네트워크로 새로 받아 캐시를 갱신한다
 *   · 그 결과는 **다음 실행** 때 반영된다
 * 현장에서 한 번 열었다 닫으면 최신이 된다. 촬영 중에 화면이 바뀌지도 않는다.
 *
 * protocol.json 만은 네트워크 우선이다. 촬영 목록과 점주 문항이 여기 들어 있어,
 * 낡은 것을 쓰면 앱과 엔진이 갈라진다. 실패하면 캐시로 물러난다.
 */
// 배포본의 화면·프로토콜 구조가 바뀌면 반드시 올린다. 같은 이름을 재사용하면
// 기존 사용자는 첫 새로고침에서 낡은 app.js를 한 번 더 받게 된다.
const CACHE = 'kfa-app-v5';
const SHELL = ['./', './index.html', './style.css', './app.js', './zip.js',
               './icon.svg', './manifest.webmanifest', './protocol.json'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k.startsWith('kfa-app-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (!url.href.startsWith(self.registration.scope)) return;

  // 프로토콜은 네트워크 우선 — 촬영 목록이 낡으면 진단이 어긋난다
  if (url.pathname.endsWith('protocol.json')) {
    e.respondWith(
      fetch(e.request)
        .then(async (r) => {
          if (!r.ok) throw new Error('protocol unavailable');
          await (await caches.open(CACHE)).put(e.request, r.clone());
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // 나머지는 캐시를 즉시 주고, 뒤에서 새로 받아 둔다
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const fresh = fetch(e.request)
        .then(async (r) => {
          if (r && r.ok) await (await caches.open(CACHE)).put(e.request, r.clone());
          return r;
        })
        .catch(() => hit);          // 오프라인이면 캐시가 답이다
      e.waitUntil(fresh);
      return hit || fresh;
    })
  );
});
