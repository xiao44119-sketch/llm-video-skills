import playwright from 'playwright';
const {chromium}=playwright;
import {mkdirSync,rmSync} from 'node:fs';

const html='/home/user/.super_doubao/super-doubao-runtime/workspace/agi-debt-video/video.html';
const outDir='/home/user/.super_doubao/super-doubao-runtime/workspace/agi-debt-video/verify';
rmSync(outDir,{recursive:true,force:true});mkdirSync(outDir,{recursive:true});

const checkpoints=[
  [4.0,'s1-quiet'],[9.0,'s1-thunder'],[13.0,'s1-end'],
  [16.0,'s2-title'],[24.0,'s2-time'],[30.0,'s2-geo'],[37.0,'s2-gene'],[41.0,'s2-note'],
  [45.0,'s3-title'],[50.0,'s3-core'],[56.0,'s3-arrows'],[65.0,'s3-note'],
  [71.0,'s4-title'],[76.0,'s4-down'],[82.0,'s4-up'],[85.0,'s4-musk'],[93.0,'s4-end'],
  [98.0,'s5-main'],[103.0,'s5-sub'],[110.0,'s5-end'],
  [115.0,'s6-end']
];

const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-gpu']});
const page=await browser.newPage({viewport:{width:1280,height:720},deviceScaleFactor:1});
const errs=[];
page.on('pageerror',e=>errs.push(String(e)));
await page.goto(`file://${html}`,{waitUntil:'load'});
await page.evaluate(async()=>{document.body.style.transformOrigin='top left';document.body.style.transform='scale(0.6667)';await document.fonts?.ready;});
for(const [t,label] of checkpoints){
  await page.evaluate(tt=>window.renderFrame(tt),t);
  await page.screenshot({path:`${outDir}/${label}.png`,type:'png'});
}
console.log('errors:',errs.length?errs.join(' | '):'none');
await browser.close();
console.log('verify frames done:',checkpoints.length);
