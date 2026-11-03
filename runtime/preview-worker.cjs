const readline = require('node:readline');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
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
 const page=await context.newPage();
 await page.goto('http://127.0.0.1:4173',{waitUntil:'networkidle'});
 const send=async()=>{await context.storageState({path:state}); const png=await page.screenshot();process.stdout.write(JSON.stringify({ok:true,image:png.toString('base64'),width:1000,height:720})+'\n');};
 await send();
 for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
  try {
   const a=JSON.parse(line);
   if(a.action==='click')await page.mouse.click(a.x,a.y);
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
