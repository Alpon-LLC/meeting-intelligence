"use strict";
const assert=require('assert/strict'); const fs=require('fs'); const os=require('os'); const path=require('path');
const {syncVexaStatus}=require('../scheduler/vexa-status');
(async()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'vexa-status-port-'));
 try {
  const dir=path.join(home,'workspace/meetings/event');fs.mkdirSync(dir,{recursive:true});
  fs.writeFileSync(path.join(home,'key'),'synthetic-key');
  process.env.VEXA_API_ENDPOINT='http://127.0.0.1:19056';process.env.VEXA_API_KEY_FILE=path.join(home,'key');
  fs.writeFileSync(path.join(dir,'transcription.state.json'),JSON.stringify({vexa_bot_id:42,live_status:'running'}));
  fs.writeFileSync(path.join(dir,'event_meta.json'),JSON.stringify({end:new Date(Date.now()-10000).toISOString()}));
  const calls=[];
  const fetch=async(url,options)=>{calls.push({url,method:options.method||'GET'});return {ok:true,json:async()=>({id:42,native_meeting_id:'abc-defg-hij',status:'active',start_time:new Date().toISOString()})}};
  await syncVexaStatus(home,fetch);await syncVexaStatus(home,fetch);
  assert.equal(calls.filter(c=>c.method==='DELETE').length,1);
  assert.equal(JSON.parse(fs.readFileSync(path.join(dir,'.joining'))).status,'admitted');
  assert.ok(fs.existsSync(path.join(dir,'.vexa-stop-requested')));
  console.log('PASS direct Vexa exact-ID admission and single stop request');
 }finally{fs.rmSync(home,{recursive:true,force:true})}
})().catch(e=>{console.error(e);process.exit(1)});
