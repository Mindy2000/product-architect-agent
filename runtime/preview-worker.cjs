const readline = require('node:readline');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const {randomUUID}=require('node:crypto');
const {chromium} = require('/opt/worker/node_modules/playwright');
(async()=>{
 if(!fs.existsSync('/workspace/node_modules')) fs.symlinkSync('/opt/worker/node_modules','/workspace/node_modules','dir');
 let backend;
 if(process.env.PA_FULLSTACK==='1'){
  backend=spawn('/opt/fullstack/venv/bin/python',['-m','uvicorn','backend.runtime:app','--host','127.0.0.1','--port','8001','--workers','1'],{cwd:'/workspace',env:{...process.env,APP_DATABASE:'/appdata/app.sqlite3'},stdio:'ignore'});
  let apiReady=false;for(let i=0;i<100;i++){try{const r=await fetch('http://127.0.0.1:8001/api/health');if(r.ok){apiReady=true;break}}catch{}await new Promise(r=>setTimeout(r,200));}
  if(!apiReady)throw new Error('业务数据库与源码不匹配，请先显式迁移');
 }
 const server=spawn('node',['/opt/worker/node_modules/vite/bin/vite.js','preview','--host','127.0.0.1','--port','4173','--strictPort'],{cwd:'/workspace',stdio:'ignore'});
 let ready=false;
 for(let i=0;i<150;i++) { try{const r=await fetch('http://127.0.0.1:4173');if(r.ok){ready=true;break;}}catch{} await new Promise(r=>setTimeout(r,200)); }
 if(!ready)throw new Error('preview server unavailable');
 const browser=await chromium.launch({headless:true});
 const state='/workspace/.preview-storage.json';
 const context=await browser.newContext({viewport:{width:1000,height:720},...(fs.existsSync(state)?{storageState:state}:{})});
 const livePage=await context.newPage(); let page=livePage;let visualContext=null;let visual={enabled:false,viewport:"desktop",storage:{},api_fixtures:{},masks:[],region:null};
 await page.goto('http://127.0.0.1:4173',{waitUntil:'networkidle'});
 let selection=null;
 const send=async()=>{
  if(!visual.enabled)await context.storageState({path:state});
  if(visual.enabled){await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0)});await page.evaluate(async()=>{await document.fonts.ready});}
  const maskToken=randomUUID();
  await page.evaluate(({masks,token})=>{for(const r of masks){const e=document.createElement('div');e.dataset.paMask=token;Object.assign(e.style,{position:'fixed',left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px',background:'#000',zIndex:'2147483647',pointerEvents:'none'});document.body.appendChild(e)}},{masks:visual.masks,token:maskToken});
  let png;try{png=await page.screenshot({animations:visual.enabled?'disabled':'allow',caret:visual.enabled?'hide':'initial'});}finally{await page.evaluate(token=>document.querySelectorAll('[data-pa-mask]').forEach(e=>{if(e.dataset.paMask===token)e.remove()}),maskToken);}
  const size=page.viewportSize();process.stdout.write(JSON.stringify({ok:true,image:png.toString('base64'),...size,visual_settings:visual,route:new URL(page.url()).pathname+new URL(page.url()).search+new URL(page.url()).hash,selection})+'\n');};
 await send();
 for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
  try {
   const a=JSON.parse(line);
   selection=null;
   if(a.action==='configure'){
    const next=a.visual;
    if(visualContext){await visualContext.close();visualContext=null;}
    visual=next;
    if(visual.enabled){
      visualContext=await browser.newContext({viewport:visual.viewport==='mobile'?{width:390,height:844}:{width:1000,height:720},locale:'zh-CN',timezoneId:'UTC',reducedMotion:'reduce',colorScheme:'light'});
      await visualContext.route('**/api/**',route=>{const u=new URL(route.request().url());const body=visual.api_fixtures[u.pathname+u.search];return route.fulfill({status:body===undefined?404:200,contentType:'application/json',body:body??'{}'});});
      await visualContext.addInitScript(storage=>{if(location.origin==='http://127.0.0.1:4173'){localStorage.clear();sessionStorage.clear();for(const [k,v] of Object.entries(storage))localStorage.setItem(k,v)}},visual.storage);
      page=await visualContext.newPage();await page.clock.install({time:new Date('2026-01-01T00:00:00Z')});await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
      await page.goto(livePage.url(),{waitUntil:'networkidle'});
    }else{page=livePage;await page.setViewportSize(visual.viewport==='mobile'?{width:390,height:844}:{width:1000,height:720});}
   }
   else if(a.action==='inspect'){selection=await page.evaluate(({x,y})=>{const e=document.elementFromPoint(x,y);if(!e)return null;const r=e.getBoundingClientRect();let n=e;const parts=[];for(let i=0;n&&i<5;i++,n=n.parentElement){const siblings=n.parentElement?[...n.parentElement.children].filter(v=>v.tagName===n.tagName):[n];parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')')}const source=e.closest('[data-pa-source]');return {source_marker:source?.getAttribute('data-pa-source')||null,tag:e.tagName.toLowerCase(),text:(e.textContent||'').trim().slice(0,200),selector:parts.join(' > '),rect:{x:r.x,y:r.y,width:r.width,height:r.height}}},{x:a.x,y:a.y});}
   else if(a.action==='click')await page.mouse.click(a.x,a.y);
   else if(a.action==='type')await page.keyboard.insertText(a.text);
   else if(a.action==='key')await page.keyboard.press(a.key);
   else if(a.action==='scroll')await page.mouse.wheel(0,a.y);
   else if(a.action==='reload')await page.reload({waitUntil:'networkidle'});
   else if(a.action==='close')break;
   else if(a.action!=='snapshot')throw new Error('invalid action');
   await page.waitForTimeout(100);await send();
  }catch{process.stdout.write(JSON.stringify({ok:false,error:'预览操作未完成，请重新打开预览。'})+'\n');}
 }
 await browser.close();server.kill();if(backend)backend.kill();
})().catch(()=>{process.stdout.write(JSON.stringify({ok:false,error:'隔离预览启动失败。'})+'\n');process.exit(1);});
