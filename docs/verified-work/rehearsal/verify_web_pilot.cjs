const { chromium } = require('C:/Users/Matt C/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'); const path=require('node:path'); const {pathToFileURL}=require('node:url');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/Matt C/AppData/Local/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe'});
 const page=await browser.newPage({viewport:{width:1100,height:800}});
 const failures=[]; const network=[];
 page.on('pageerror',error=>failures.push(String(error)));
 await page.route('**/*',route=>{const url=route.request().url();
   if(/^https?:/.test(url)){network.push(url);return route.abort();}return route.continue();});
 try {
   await page.goto(pathToFileURL(path.resolve('outputs/web-pilot/index.html')).href);
   const assert=(value,message)=>{if(!value)throw Error(message);};
   assert(await page.title()==='Collective task pilot','page title');
   assert(await page.locator('#tasks li').count()===3,'initial inventory');
   assert(await page.locator('#count').textContent()==='3 tasks','initial count');
   await page.getByRole('button',{name:'Blocked tasks',exact:true}).click();
   assert(await page.locator('#tasks li').count()===1,'blocked filter');
   assert((await page.locator('#tasks').textContent()).includes('Deploy to VPS'),'filtered task');
   assert(await page.locator('#count').textContent()==='1 tasks','filtered count');
   await page.getByRole('button',{name:'All tasks',exact:true}).click();
   assert(await page.locator('#tasks li').count()===3,'reset filter');
   await page.screenshot({path:'outputs/web-pilot/preview.png',fullPage:true});
   await page.setViewportSize({width:390,height:844});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile overflow');
   assert(failures.length===0,'browser errors'); assert(network.length===0,'unexpected network');
   fs.writeFileSync('outputs/web-pilot/browser-checks.json',JSON.stringify({passed:true,
     checks:['title','initial inventory','initial count','blocked filter','filtered task','filtered count','reset filter','mobile width','no page errors','no HTTP requests'],
     fixture_only:true,native_execution:false,certification_eligible:false,failures,network},null,2));
   process.stdout.write('10 browser checks passed for the reviewed synthetic fixture.\n');
 } finally {await browser.close();}
})().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});
