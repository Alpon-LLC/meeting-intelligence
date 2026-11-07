"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

if (process.argv[2] === "hold-mutex") {
  const claimPath = process.argv[3];
  const { withMutex } = require("./dispatch-lease");
  const result = withMutex(claimPath, () => {
    process.stdout.write("locked\n");
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 60_000);
    return true;
  });
  if (result !== true) process.exit(2);
  process.exit(0);
}

if (process.argv[2] === "child") {
  const [claimRoot, barrier, eventId] = process.argv.slice(3);
  while (!fs.existsSync(barrier)) {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1);
  }
  const { acquireDispatch } = require("./dispatch-lease");
  const result = acquireDispatch({
    claimRoot,
    phase: "start",
    eventId,
    payload: { id: eventId, title: "Concurrent claim test" },
  });
  process.stdout.write(`${JSON.stringify({ action: result.action, reason: result.reason })}\n`);
  process.exit(0);
}

function launch(claimRoot, barrier, eventId) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [__filename, "child", claimRoot, barrier, eventId], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.on("data", chunk => { stderr += chunk; });
    child.on("error", reject);
    child.on("exit", code => {
      if (code !== 0) return reject(new Error(`child exited ${code}: ${stderr}`));
      resolve(JSON.parse(stdout));
    });
  });
}

function waitForLine(child) {
  return new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", chunk => {
      stdout += chunk;
      if (stdout.includes("\n")) resolve(stdout.trim());
    });
    child.stderr.on("data", chunk => { stderr += chunk; });
    child.on("error", reject);
    child.on("exit", code => reject(new Error(`holder exited ${code} before lock: ${stderr}`)));
  });
}

function waitForExit(child) {
  return new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", (code, signal) => resolve({ code, signal }));
  });
}

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "meeting-claim-race-"));
  try {
    const crashClaim = path.join(root, "crash-release", "claim.json");
    const holder = spawn(process.execPath, [__filename, "hold-mutex", crashClaim], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    assert.strictEqual(await waitForLine(holder), "locked");
    const holderExitPromise = waitForExit(holder);
    holder.kill("SIGKILL");
    const holderExit = await holderExitPromise;
    assert.strictEqual(holderExit.signal, "SIGKILL");
    const { withMutex } = require("./dispatch-lease");
    assert.strictEqual(
      withMutex(crashClaim, () => true),
      true,
      "a crashed owner must release immediately without stale-file reclamation",
    );

    for (let round = 0; round < 5; round += 1) {
      const claimRoot = path.join(root, `claims-${round}`);
      const barrier = path.join(root, `barrier-${round}`);
      const eventId = `race-${round}`;
      const children = Array.from({ length: 16 }, () => launch(claimRoot, barrier, eventId));
      await new Promise(resolve => setTimeout(resolve, 50));
      fs.writeFileSync(barrier, "go\n");
      const results = await Promise.all(children);
      assert.strictEqual(
        results.filter(result => result.action === "dispatch").length,
        1,
        `round ${round}: exactly one process must win the atomic claim`,
      );
      assert(results.every(result => result.action === "dispatch" || result.reason === "lease-active" || result.reason === "claim-busy"));
    }
    console.log("dispatch concurrency: PASS");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
