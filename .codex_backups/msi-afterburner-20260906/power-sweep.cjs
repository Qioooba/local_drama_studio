// Read-only workload observations; temporary GPU power limits, restored in finally.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const dir = __dirname;
const logPath = path.resolve(dir, '../../logs/comfy-production.log');
const outPath = path.join(dir, 'power-sweep.ndjson');
const smi = 'C:\\Windows\\System32\\nvidia-smi.exe';
const sleep = ms => new Promise(r => setTimeout(r, ms));
function record(data) { fs.appendFileSync(outPath, JSON.stringify({time:Date.now(), ...data})+'\n'); }
function power(watts) {
  execFileSync(smi, ['-i','00000000:04:00.0', '-pl', String(watts)], {encoding:'utf8', windowsHide:true});
}
function telemetry() {
  const row = execFileSync(smi, ['-i','00000000:04:00.0', '--query-gpu=utilization.gpu,power.draw,power.limit,temperature.gpu,clocks.current.graphics,clocks.current.memory', '--format=csv,noheader,nounits'], {encoding:'utf8',windowsHide:true}).trim().split(/,\s*/).map(Number);
  if (row.length !== 6 || row.some(x=>!Number.isFinite(x))) throw Error('Invalid GPU telemetry');
  return {gpu:row[0],watts:row[1],limit:row[2],temp:row[3],core:row[4],memory:row[5]};
}
function progress() {
  const size=fs.statSync(logPath).size, length=Math.min(12000,size), buf=Buffer.alloc(length);
  const fd=fs.openSync(logPath,'r'); try { fs.readSync(fd,buf,0,length,size-length); } finally { fs.closeSync(fd); }
  const text=buf.toString('utf8');
  const rows=[...text.matchAll(/(\d+)\/50\s+\[(\d+):(\d+)<[^\]\r\n]*?,\s*([\d.]+)s\/it\]/g)];
  const last=rows.at(-1);
  return last ? {step:Number(last[1]),elapsed:Number(last[2])*60+Number(last[3]),sPerIt:Number(last[4]),fileSize:size} : null;
}
(async()=>{
  let original;
  try {
    original=telemetry();
    if(Math.abs(original.limit-312)>1) throw Error('Expected the audited 312W starting limit');
    record({type:'start',original});
    for(const limit of [312,331.2,350.4,374.4,417.6,312]) {
      power(limit); record({type:'stage',limit});
      let validSteps=0,lastProgress=null;
      const begin=Date.now();
      while(Date.now()-begin<105000 && validSteps<7) {
        const t=telemetry(),p=progress();
        record({type:'sample',stage:limit,...t,progress:p});
        if(t.temp>=78) throw Error('Temperature guard reached 78C');
        if(p && lastProgress && p.fileSize>lastProgress.fileSize && p.step===lastProgress.step+1 && p.elapsed>lastProgress.elapsed && Date.now()-begin>14000 && t.gpu>=80) validSteps++;
        if(p) lastProgress=p;
        await sleep(900);
      }
      record({type:'stageEnd',limit,validSteps});
    }
    record({type:'complete'});
  } catch(error) { record({type:'error',message:String(error)}); }
  finally {
    if(original) {
      try {power(original.limit);record({type:'restored',...telemetry()});}
      catch(error) {record({type:'restoreError',message:String(error)});}
    }
  }
})();
