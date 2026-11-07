"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(`${__dirname}/run.js`, "utf8");
const start = source.indexOf("function readJsonFile");
const end = source.indexOf("// Returns { skip: boolean, reason: string }\n// Skip finalization", start);
assert(start >= 0 && end > start, "finalization helpers not found");

const files = new Map();
const mtimes = new Map();
const path = { join: (...parts) => parts.join("/") };
const fsMock = {
  existsSync: p => files.has(p),
  readFileSync: p => files.get(p),
  statSync: p => ({ mtimeMs: mtimes.get(p) }),
};
const context = { fs: fsMock, path, MEETINGS_DIR: "/meetings", Date, process };
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

const dir = "/meetings/event-1";
const flag = `${dir}/.review-flag.txt`;
const status = `${dir}/status.json`;
files.set(flag, "review");
files.set(status, JSON.stringify({ transcriptLines: 3 }));
mtimes.set(flag, 2000);
mtimes.set(status, 1000);
assert.strictEqual(context.checkFinalizeLock("event-1").skip, true,
  "current review flag must suppress repeated finalization even with sparse lines");

mtimes.set(status, 3000);
assert.strictEqual(context.checkFinalizeLock("event-1").skip, false,
  "newer capture must supersede an older review flag");

console.log("review-flag guard: PASS");
