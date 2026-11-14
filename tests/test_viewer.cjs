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
    createElement:element, createElementNS:(_namespace,_tag)=>element(), addEventListener(){}
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

test('difference view requests one image with selected epochs and disables playback',async()=>{
  const h=harness();const m=h.run('previewMeta(params())');m.synthetic=false;m.data_source='cache';
  m.warnings=['Colour changes may mimic motion.'];
  h.el('mode').value='diff';h.el('epoch_a').value='1';h.el('epoch_b').value='0';
  h.el('mode').onchange();assert.equal(h.el('epochFields').hidden,false);
  h.context.fetch=async url=>{h.calls.push(url);return url.startsWith('/api/meta')?
    {ok:true,json:async()=>m}:{ok:true,headers:{get:()=> 'image/png'},blob:async()=>new Blob(['fixture'],{type:'image/png'})};};
  await h.run('load()');
  assert.equal(h.calls.length,2);
  for(const url of h.calls){const p=new URL(url,'http://localhost').searchParams;
    assert.equal(p.get('mode'),'diff');assert.equal(p.get('epoch_a'),'1');assert.equal(p.get('epoch_b'),'0');
    assert.equal(p.has('index'),false);
  }
  assert.equal(h.el('counter').textContent,'Epoch difference');
  assert.equal(h.el('play').disabled,true);
  assert.equal(h.el('diffCaption').hidden,false);
  assert.equal(h.el('warnings').children[0].textContent,m.warnings[0]);
  assert.equal(h.el('epoch_a').children.length,2);
  assert.match(h.context.location.href,/epoch_a=1/);
  h.el('mode').value='blink';h.el('mode').onchange();
  assert.equal(h.el('epochFields').hidden,true);
  assert.equal(h.run('params().has("epoch_a")'),false);
  h.run('clearView()');assert.equal(h.el('diffCaption').hidden,true);
});

test('motion panel renders uncertainties, verdict and true-scale epoch overlay',()=>{
  const h=harness();
  const result={synthetic:false,image_shape:[20,20],epochs:[
    {epoch:0,mjd:60900,x:10,y:10,radius_px:.1,sigma_arcsec:.615,n_frames:2},
    {epoch:2,mjd:61100,x:9,y:8,radius_px:.2,sigma_arcsec:1.23,n_frames:3}],
    fit:{pm_arcsec_per_yr:1,pm_sigma:.5,position_angle_deg:30,pa_sigma:10,
      parallax_arcsec:null,parallax_sigma:null,chi2_reduced:null,n_epochs:2,
      significance:2,uncertainty_scaled:false},
    benchmark:{verdict:'UNINFORMATIVE',citation:'test-only',literature_pm_arcsec_per_yr:1,
      literature_parallax_arcsec:.1},warnings:['Parallax is not constrained.']};
  h.context.result=result;h.run('renderMotion(result)');
  assert.equal(h.el('motionOverlay').hidden,false);
  assert.equal(h.el('motionOverlay').viewBox,'0 0 20 20');
  assert.equal(h.el('motionOverlay').children.length,4); // definitions, vector, two epochs
  assert.equal(h.el('motionOverlay').children[3].children[1].r,'0.2');
  assert.match(h.el('motionStatus').textContent,/not established/);
  assert.equal(h.el('motionValues').children[0].children[1].textContent,'1.0000 ± 0.50000 arcsec/yr');
  assert.equal(h.el('motionValues').children[9].children[1].textContent,'UNINFORMATIVE');
  assert.equal(h.el('motionWarnings').children[0].textContent,result.warnings[0]);
  h.run('clearView()');assert.equal(h.el('motionOverlay').hidden,true);
  assert.equal(h.el('motionTable').hidden,true);
});

test('motion error leaves loaded observations intact',async()=>{
  const h=harness();h.run('meta=previewMeta(params());active=params();controller=new AbortController()');
  h.context.fetch=async url=>{h.calls.push(url);return{ok:false,status:422,
    json:async()=>({error:{message:'Too few accepted centroids.'}})};};
  await h.el('measureMotion').onclick();
  assert.match(h.calls[0],/^\/api\/motion\?/);
  assert.equal(h.el('motionStatus').textContent,'Too few accepted centroids.');
  assert.equal(h.el('measureMotion').disabled,false);
  assert.equal(h.run('meta.n_frames'),6);
});

function gaiaFixture(){return{source:'gaia_dr3',epoch:2025.9,n_sources:3,image_shape:[20,20],warnings:['One PM is missing.'],sources:[
  {source_id_text:'1234567890123456789',x:10,y:10,g_mag:12,pmra:4,pmdec:-3,parallax:2,pm_missing:false},
  {source_id_text:'987654321098765432',x:12,y:8,g_mag:18,pmra:null,pmdec:null,parallax:null,pm_missing:true},
  {source_id_text:'123',x:null,y:null,g_mag:null,pmra:null,pmdec:null,parallax:null,pm_missing:true}]};}

test('Gaia starts off, sizes circles by magnitude and exposes lossless source details',()=>{
  const h=harness();assert.equal(!!h.el('gaiaToggle').checked,false);assert.equal(h.calls.length,0);
  h.context.catalog=gaiaFixture();h.run('renderGaia(catalog)');
  const svg=h.el('gaiaOverlay');assert.equal(svg.viewBox,'0 0 20 20');
  assert.equal(svg.children.length,2);
  assert.ok(Number(svg.children[0].r)>Number(svg.children[1].r));
  assert.equal(svg.children[0].cx,'10');assert.equal(svg.children[1].cy,'8');
  assert.match(h.el('gaiaStatus').textContent,/epoch 2025.9 · 3 sources \(2 on image\)/);
  svg.children[0].onclick();assert.match(h.el('gaiaDetails').textContent,/1234567890123456789/);
  assert.match(h.el('gaiaDetails').textContent,/4 mas\/yr/);
  svg.children[1].onkeydown({key:'Enter',preventDefault(){}});
  assert.match(h.el('gaiaDetails').textContent,/Proper motion missing/);
  assert.equal(h.el('gaiaWarnings').children[0].textContent,'One PM is missing.');
  h.run('clearGaia()');assert.equal(svg.children.length,0);
});

test('Gaia failure is isolated and stale requests cannot restore a disabled layer',async()=>{
  const h=harness();h.run('meta=previewMeta(params());active=params()');h.el('gaiaToggle').checked=true;
  h.context.fetch=async url=>{h.calls.push(url);return{ok:false,status:502,json:async()=>({error:{message:'Gaia service unreachable.'}})};};
  await h.run('loadGaia()');assert.match(h.calls[0],/^\/api\/catalog\?/);
  assert.equal(h.el('gaiaStatus').textContent,'Gaia unavailable');
  assert.equal(h.el('retryGaia').hidden,false);assert.equal(h.run('meta.n_frames'),6);
  let resolve;h.context.fetch=()=>new Promise(r=>{resolve=r;});
  const pending=h.run('loadGaia()');h.el('gaiaToggle').checked=false;h.el('gaiaToggle').onchange();
  resolve({ok:true,json:async()=>gaiaFixture()});await pending;
  assert.equal(h.el('gaiaOverlay').children.length,0);
  assert.equal(h.el('gaiaStatus').textContent,'Gaia layer off');
  assert.equal(h.run('meta.n_frames'),6);
});

test('Gaia query uses loaded selection and an empty catalog is available, not an error',async()=>{
  const h=harness();h.run('meta=previewMeta(params());active=params()');h.el('gaiaToggle').checked=true;
  h.el('target').value='Unsaved target';
  h.context.fetch=async url=>{h.calls.push(url);return{ok:true,json:async()=>({...gaiaFixture(),sources:[],n_sources:0,warnings:[]})};};
  await h.run('loadGaia()');
  assert.equal(new URL(h.calls[0],'http://localhost').searchParams.get('target'),'WISE 0855-0714');
  assert.match(h.el('gaiaStatus').textContent,/0 sources/);
  assert.equal(h.el('gaiaDetails').textContent,'No Gaia sources on this image.');
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
