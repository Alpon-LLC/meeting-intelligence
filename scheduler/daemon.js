"use strict";
// Original run.js remains a one-tick cron entrypoint. This local wrapper serializes ticks.
const { spawn } = require('child_process');
const path = require('path');
let current = null;
let stopping = false;
function tick() {
  if (stopping) return;
  current = spawn(process.execPath, [path.join(__dirname,'run.js')], {stdio:'inherit',env:process.env});
  current.on('exit', () => {
    current = null;
    if (!stopping) setTimeout(tick, Number(process.env.MEETING_POLL_MS || 120000));
  });
}
for (const signal of ['SIGINT','SIGTERM']) process.on(signal, () => {
  stopping = true;
  if (current) current.kill(signal);
  else process.exit(0);
});
tick();
