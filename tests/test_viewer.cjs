// AI-assisted: written with Codex. Run: node --test tests/test_viewer.cjs
// Exercises application state and API boundaries without a browser dependency.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function harness() {
  const elements = new Map();
  function element() {
    return {value:'', children:[], style:{}, dataset:{}, disabled:false,
      setAttribute(k,v){this[k]=v;}, removeAttribute(k){delete this[k];},
      replaceChildren(...children){this.children=children;}, append(...children){this.children.push(...children);},
      getContext(){return {createImageData(w,h){return {data:new Uint8ClampedArray(w*h*4)};},putImageData(){}};},
      toDataURL(){return 'data:image/png;base64,fixture';}
    };
  }
  const document = {
    getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},
    createElement:element, addEventListener(){}
  };
  for(const [id,value] of Object.entries({target:'WISE 0855-0714',mode:'blink',fov:'120',speed:'500',source:'api',inputMode:'name'}))document.getElementById(id).value=value;
  const location = {href:'http://localhost/',search:''};
  const calls=[];
  const context=vm.createContext({document,location,history:{replaceState(_a,_b,url){location.href=String(url);}},
    URL,URLSearchParams,AbortController,Image:class{async decode(){}},navigator:{},
    window:{addEventListener(){}},setInterval,clearInterval,setTimeout:fn=>fn(),
    fetch:async (url)=>{calls.push(url);throw Error('API unavailable');}
  });
  vm.runInContext(script,context);
  return {context,calls,el:document.getElementById.bind(document),run:code=>vm.runInContext(code,context)};
}

test('explicit preview loads six frames, exposes warnings and supports navigation',async()=>{
  const h=harness();h.el('source').value='preview';await h.run('load()');
  assert.equal(h.el('badge').textContent,'SYNTHETIC DATA');
  assert.equal(h.el('warnings').children.length,2);
  assert.equal(h.el('timeline').children.length,2);
  assert.equal(h.el('counter').textContent,'Frame 1 / 6');
  h.el('next').onclick();assert.equal(h.el('counter').textContent,'Frame 2 / 6');
  assert.equal(h.calls.length,0);
  assert.match(h.context.location.href,/preview=1/);
});

test('stack disables blink controls and incomplete wavelength window is rejected',async()=>{
  const h=harness();h.el('source').value='preview';h.el('mode').value='stack';await h.run('load()');
  assert.equal(h.el('play').disabled,true);assert.equal(h.el('counter').textContent,'6 frames stacked');
  h.el('wmin').value='4.1';await h.run('load()');
  assert.match(h.el('message').textContent,/both wavelength/);
});

test('API failure shows retry without falling back to synthetic images',async()=>{
  const h=harness();await h.run('load()');
  assert.equal(h.calls.length,1);assert.equal(h.el('retry').hidden,false);
  assert.equal(h.el('sky').hidden,true);assert.equal(h.el('badge').textContent,'NO DATA LOADED');
});

test('API mode uses contract fields, preloads PNGs and excludes UI-only query parameters',async()=>{
  const h=harness();const m=h.run('previewMeta(params())');m.synthetic=false;m.data_source='cache';
  h.context.fetch=async url=>{h.calls.push(url);return url.startsWith('/api/meta')?
    {ok:true,json:async()=>m}:{ok:true,headers:{get:()=> 'image/png'},blob:async()=>new Blob(['fixture'],{type:'image/png'})};};
  await h.run('load()');
  assert.equal(h.calls.length,7);assert.equal(h.el('badge').textContent,'CACHE');
  for(const url of h.calls){const p=new URL(url,'http://localhost').searchParams;assert.equal(p.has('speed'),false);assert.equal(p.has('frame'),false);assert.equal(p.has('preview'),false);}
  assert.equal(new URL(h.calls[6],'http://localhost').searchParams.get('index'),'5');
  h.run('clearView()');
});

test('empty coverage produces an empty state, preserving structured server errors',async()=>{
  const h=harness();h.context.fetch=async()=>({ok:false,status:404,json:async()=>({error:{code:'no_coverage',message:'No SPHEREx coverage here.'}})});
  await h.run('load()');assert.equal(h.el('placeholder').textContent,'No SPHEREx coverage here.');assert.equal(h.el('play').disabled,true);
});

test('display filters never alter API selection and reset to neutral',()=>{
  const h=harness();
  const before=h.run('params().toString()');
  h.el('brightness').value='150';h.el('contrast').value='80';
  h.el('brightness').oninput();
  assert.equal(h.el('sky').style.filter,'brightness(150%) contrast(80%)');
  assert.equal(h.run('params().toString()'),before);
  h.el('resetDisplay').onclick();
  assert.equal(h.el('sky').style.filter,'brightness(100%) contrast(100%)');
});

test('GIF download uses loaded selection and speed, reports JSON errors without disturbing images',async()=>{
  const h=harness();
  h.run('meta=previewMeta(params());active=params();controller=new AbortController()');
  h.el('target').value='An unsaved different target';h.el('speed').value='750';
  h.context.fetch=async url=>{
    h.calls.push(url);
    return {ok:false,status:502,json:async()=>({error:{message:'Archive unavailable.'}})};
  };
  await h.el('downloadGif').onclick();
  const u=new URL(h.calls[0],'http://localhost');
  assert.equal(u.pathname,'/api/blink.gif');
  assert.equal(u.searchParams.get('target'),'WISE 0855-0714');
  assert.equal(u.searchParams.get('speed'),'750');
  assert.equal(u.searchParams.has('brightness'),false);
  assert.equal(h.el('gifStatus').textContent,'Archive unavailable.');
  assert.equal(h.el('downloadGif').disabled,false);
  assert.equal(h.run('meta.n_frames'),6);
});
