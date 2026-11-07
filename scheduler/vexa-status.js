"use strict";
// Direct provider status/stop synchronization; leaves prep/finish composition to original agent webhooks.
const fs=require('fs'); const path=require('path');
const {atomicWriteJson}=require('./dispatch-lease');
async function syncVexaStatus(home, fetchFn=global.fetch) {
  const base=process.env.VEXA_API_ENDPOINT;
  const keyFile=process.env.VEXA_API_KEY_FILE;
  if(!base || !keyFile || !fs.existsSync(keyFile)) return;
  const key=fs.readFileSync(keyFile,'utf8').trim();
  const root=path.join(home,'workspace/meetings');
  if(!fs.existsSync(root)) return;
  for(const name of fs.readdirSync(root)) {
    const dir=path.join(root,name); const stateFile=path.join(dir,'transcription.state.json');
    if(!fs.existsSync(stateFile)) continue;
    const state=JSON.parse(fs.readFileSync(stateFile,'utf8'));
    if(!state.vexa_bot_id || ['completed','failed_nonretryable','retry_exhausted'].includes(state.live_status)) continue;
    const response=await fetchFn(base.replace(/\/bots\/?$/,'').replace(/\/$/,'')+'/transcripts/by-id/'+encodeURIComponent(state.vexa_bot_id),
      {headers:{'X-API-Key':key},signal:AbortSignal.timeout(15000)});
    if(!response.ok) throw new Error('Vexa status request failed (body withheld)');
    const doc=await response.json();
    if(String(doc.id)!==String(state.vexa_bot_id)) throw new Error('Vexa bot identity mismatch');
    const meta=JSON.parse(fs.readFileSync(path.join(dir,'event_meta.json'),'utf8'));
    if(doc.status==='active' && doc.start_time) {
      const lock=path.join(root,'active',name+'.lock');
      atomicWriteJson(lock,{backend:'vexa-primary',event_id:name,bot_id:doc.id,pid:null,status:'active',joinedAt:doc.start_time});
      atomicWriteJson(path.join(dir,'.joining'),{backend:'vexa-primary',pid:null,status:'admitted',bot_id:doc.id});
      if(Date.now()>Date.parse(meta.end) && !fs.existsSync(path.join(dir,'.vexa-stop-requested'))) {
        atomicWriteJson(path.join(dir,'.vexa-stop-requested'),{status:'submitting',bot_id:doc.id});
        const stopped=await fetchFn(base.replace(/\/bots\/?$/,'').replace(/\/$/,'')+'/bots/google_meet/'+encodeURIComponent(doc.native_meeting_id),
          {method:'DELETE',headers:{'X-API-Key':key},signal:AbortSignal.timeout(15000)});
        if(!stopped.ok) throw new Error('Vexa stop request failed; reconcile exact bot before retry');
      }
    }
    if(['completed','failed'].includes(doc.status)) {
      atomicWriteJson(path.join(dir,'transcript.live.raw.json'),doc);
      // The signed completion bridge, not this poll, authorizes lifecycle/signal readiness.
    }
  }
}
module.exports={syncVexaStatus};
