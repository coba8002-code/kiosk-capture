const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const handlers = {}, deleted = [], stored = [];
let shell;
const cached = {cached:true};
const context = {
  URL, location: {origin:'https://example.com'},
  self: {registration:{scope:'https://example.com/kiosk/'},
    addEventListener:(name, fn) => handlers[name] = fn,
    skipWaiting:async()=>{}, clients:{claim:async()=>{}}},
  caches: {
    open: async()=>({addAll:async(items)=>{shell=items;}, put:async(...args)=>stored.push(args)}),
    keys:async()=>['other-product', 'kfa-app-v3', 'kfa-app-v4'],
    delete:async(name)=>deleted.push(name), match:async()=>cached
  },
  fetch:async()=>({ok:false})
};
vm.runInNewContext(fs.readFileSync('app/sw.js','utf8'),context);
(async()=>{
  let pending;
  handlers.install({waitUntil:p=>pending=p}); await pending;
  assert(shell.includes('./protocol.json'));
  handlers.activate({waitUntil:p=>pending=p}); await pending;
  assert.deepEqual(deleted,['kfa-app-v3', 'kfa-app-v4']);
  handlers.fetch({request:{method:'GET',url:'https://example.com/kiosk/protocol.json'},
    respondWith:p=>pending=p});
  assert.equal(await pending,cached);
  let intercepted=false;
  handlers.fetch({request:{method:'GET',url:'https://example.com/other/'},
    respondWith:()=>intercepted=true});
  assert.equal(intercepted,false);
  console.log('Service worker runtime checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
