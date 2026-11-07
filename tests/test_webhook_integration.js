"use strict";
// Actual signed HTTP transport + direct Vexa worker routing regression, no model/provider requests.
const assert=require('assert/strict');const http=require('http');const crypto=require('crypto');
const fs=require('fs');const os=require('os');const path=require('path');
(async()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'meeting-webhook-port-'));
 process.env.HERMES_HOME=home;process.env.MEETING_CONFIG=path.join(home,'absent.json');
 const seen=[];
 const server=http.createServer((req,res)=>{let body='';req.on('data',c=>body+=c);req.on('end',()=>{
  const expected=crypto.createHmac('sha256','synthetic-secret').update(body).digest('hex');
  assert.equal(req.headers['x-webhook-signature'],expected);const payload=JSON.parse(body);seen.push(payload);
  res.writeHead(202,{'Content-Type':'application/json'});res.end(JSON.stringify({status:'accepted',delivery_id:payload._dispatch.delivery_id}));
 })});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  const {dispatchPhaseWebhook}=require('../scheduler/run');
  const url=`http://127.0.0.1:${server.address().port}/webhooks/meeting-prep`;
  const payload={id:'synthetic-occurrence',title:'Synthetic [record]',start:new Date(Date.now()+35*60000).toISOString(),end:new Date(Date.now()+60*60000).toISOString(),meet_url:'https://meet.google.com/abc-defg-hij'};
  const result=await dispatchPhaseWebhook({phase:'prep',eventId:payload.id,payload,url,secret:'synthetic-secret',mainLog:()=>{}});
  assert.equal(result.sent,true);assert.equal(seen.length,1);assert.equal(seen[0]._dispatch.phase,'prep');
  assert.ok(seen[0]._dispatch.claim_path);assert.ok(seen[0]._dispatch.receipt_path);
  // Same phase/event within lease does not deliver a second agent webhook.
  const duplicate=await dispatchPhaseWebhook({phase:'prep',eventId:payload.id,payload,url,secret:'synthetic-secret',mainLog:()=>{}});
  assert.equal(duplicate.sent,false);assert.equal(seen.length,1);
  console.log('PASS original prep dispatch: signed HTTP, original payload/claims, duplicate suppression');
 }finally{server.close();fs.rmSync(home,{recursive:true,force:true})}
})().catch(e=>{console.error(e);process.exit(1)});
