import playwright from 'playwright';
const {chromium}=playwright;
import {execFileSync} from 'node:child_process';
import {existsSync,mkdirSync,rmSync} from 'node:fs';
import {resolve,dirname,basename} from 'node:path';
import {tmpdir} from 'node:os';

const args=Object.fromEntries(process.argv.slice(2).map((arg,index,all)=>arg.startsWith('--')?[arg.slice(2),all[index+1]]:[]).filter(pair=>pair.length));
const html=resolve(args.html||'./index.html');
const output=resolve(args.output||'./output.mp4');
const width=Number(args.width||1920),height=Number(args.height||1080),captureFps=Number(args.captureFps||20),outputFps=Number(args.outputFps||30),duration=Number(args.duration||65);
const audio=args.audio?resolve(args.audio):null;
const captureScale=Number(args.captureScale||0.6667);
const captureWidth=Math.round(width*captureScale),captureHeight=Math.round(height*captureScale);
const workers=Math.max(1,Number(args.workers||4));
const projectKey=basename(dirname(html)).replace(/[^\w\u4e00-\u9fff-]/g,'-');
const tempDir=resolve(tmpdir(),`${projectKey}-frames-${width}x${height}`);
const ffmpeg='ffmpeg';
mkdirSync(dirname(output),{recursive:true});rmSync(tempDir,{recursive:true,force:true});mkdirSync(tempDir,{recursive:true});
const frames=Math.ceil(duration*captureFps);
async function renderWorker(workerIndex){
  const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-gpu']});
  const page=await browser.newPage({viewport:{width:captureWidth,height:captureHeight},deviceScaleFactor:1});
  await page.goto(`file://${html}`,{waitUntil:'load'});
  await page.evaluate(async scale=>{document.body.style.transformOrigin='top left';document.body.style.transform=`scale(${scale})`;await document.fonts?.ready;},captureScale);
  for(let frame=workerIndex;frame<frames;frame+=workers){await page.evaluate(t=>window.renderFrame(t),frame/captureFps);await page.screenshot({path:resolve(tempDir,`frame-${String(frame).padStart(5,'0')}.png`),type:'png'});}
  await browser.close();
}
await Promise.all(Array.from({length:workers},(_,i)=>renderWorker(i)));
const silent=resolve(tempDir,'silent.mp4');
execFileSync(ffmpeg,['-y','-hide_banner','-loglevel','error','-framerate',String(captureFps),'-i',resolve(tempDir,'frame-%05d.png'),'-t',duration.toFixed(3),'-vf',`scale=${width}:${height}:flags=lanczos`,'-r',String(outputFps),'-c:v','libx264','-profile:v','high','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',silent],{stdio:'inherit'});
if(audio&&existsSync(audio)){execFileSync(ffmpeg,['-y','-hide_banner','-loglevel','error','-i',silent,'-i',audio,'-map','0:v:0','-map','1:a:0','-t',duration.toFixed(3),'-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-ac','1','-movflags','+faststart',output],{stdio:'inherit'});}else{execFileSync(ffmpeg,['-y','-hide_banner','-loglevel','error','-i',silent,'-c','copy',output],{stdio:'inherit'});}
rmSync(tempDir,{recursive:true,force:true});console.log(`Rendered ${basename(output)} (${width}x${height}, ${duration}s, ${outputFps}fps; captured ${captureFps}fps)`);
