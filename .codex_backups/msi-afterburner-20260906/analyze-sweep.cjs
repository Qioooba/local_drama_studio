const fs=require('node:fs');
const path=require('node:path');
const rows=fs.readFileSync(path.join(__dirname,'power-sweep.ndjson'),'utf8').trim().split('\n').map(JSON.parse);
const stages=[];
for(const r of rows){
 if(r.type==='stage')stages.push({limit:r.limit,start:r.time,rows:[]});
 else if(r.type==='sample') stages.at(-1)?.rows.push(r);
}
const mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
const result=stages.map(s=>{
 const changes=[];let last=null;
 for(const r of s.rows){if(r.progress && (!last || r.progress.fileSize!==last.progress.fileSize)){changes.push(r);last=r;}}
 const intervals=[];
 for(let i=2;i<changes.length;i++){
  const a=changes[i-1],b=changes[i];
  if(a.time-s.start<14000 || a.progress.step+1!==b.progress.step || b.progress.step>=50)continue;
  const duration=b.progress.elapsed-a.progress.elapsed;
  const samples=s.rows.filter(r=>r.time>=a.time && r.time<b.time);
  if(duration<=0 || samples.length<2 || mean(samples.map(r=>r.gpu))<85)continue;
  intervals.push({step:b.progress.step,seconds:duration,watts:mean(samples.map(r=>r.watts)),temp:mean(samples.map(r=>r.temp)),core:mean(samples.map(r=>r.core))});
 }
 return {limit:s.limit,intervals:intervals.length,secondsPerStep:mean(intervals.map(x=>x.seconds)),meanWatts:mean(intervals.map(x=>x.watts)),joulesPerStep:mean(intervals.map(x=>x.watts*x.seconds)),temp:mean(intervals.map(x=>x.temp)),core:mean(intervals.map(x=>x.core)),details:intervals};
});
console.log(JSON.stringify(result,null,2));
