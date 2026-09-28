const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app/app.js','utf8').split('(async function init()')[0];
const nodes = new Map();
function node() { return {attrs:{},style:{},scrollTop:350,children:[],
  setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]},
  appendChild(v){this.children.push(v)},addEventListener(){},click(){this.clicked=true}}; }
const context = {document:{createElement:node,createTextNode:s=>s,
  querySelector:s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s)}},
  window:{scrollY:510,scrollTo(x,y){this.scrollY=y}},console,URL,Blob};
vm.createContext(context);vm.runInContext(source,context);
vm.runInContext(`
  if(el('button',{'aria-pressed':true}).attrs['aria-pressed']!=='true')throw Error('true ARIA');
  if(el('button',{'aria-pressed':false}).attrs['aria-pressed']!=='false')throw Error('false ARIA');
  renderDevice=()=>{};render();
  if($('#view').scrollTop!==350 || window.scrollY!==510)throw Error('scroll moved');
  render(true);if($('#view').scrollTop!==0)throw Error('navigation scroll');
  chooseCaptureInput({medium:'video'}, {id:'S2-01'},true);
  if(!$('#capture-gallery').multiple || $('#capture-gallery').accept!=='video/*')throw Error('video multi');
  capture({medium:'photo'}, {id:'S1-04'});
  if(!$('#capture-gallery').multiple || $('#capture-gallery').attrs.capture)throw Error('original camera');
  for(const p of ['display','control_panel','dispenser','elevation','floor']) {
    if(!markerGuideSvg(p).includes('카드 1장'))throw Error('marker guide');
  }
`,context);
assert.ok(!source.includes('files.slice(0, 1)'));
console.log('PASS: ARIA true/false, scroll retention, multi-file video, original gallery, five marker guides');
