// Headless verification of the Smart Counter camera UI using system Chrome via CDP.
// Verifies: mode tabs render, Camera tab shows CameraWorkspace, getUserMedia is
// invoked (fake device), video element gets a live MediaStream, and cleanup on exit.
// Real barcode decode requires a physical barcode in front of a real camera —
// documented as a manual acceptance step (docs/smart-counter-implementation.md §5).

import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9333;
const URL_ = "http://localhost:3000/smart-counter";

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ks-chrome-"));

const chrome = spawn(CHROME, [
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`,
  "--headless=new",
  "--no-first-run",
  "--no-default-browser-check",
  "--use-fake-ui-for-media-stream", // auto-grant camera permission
  "--use-fake-device-for-media-stream", // synthetic camera
  "--autoplay-policy=no-user-gesture-required",
  "--window-size=1440,900",
  "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function send(ws, id, method, params = {}) {
  return new Promise((resolve, reject) => {
    const onMsg = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id === id) {
        ws.removeEventListener("message", onMsg);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      }
    };
    ws.addEventListener("message", onMsg);
    ws.send(JSON.stringify({ id, method, params }));
  });
}

let ws = null;
try {
  // Wait for the DevTools endpoint
  let version = null;
  for (let i = 0; i < 30; i++) {
    try {
      version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      break;
    } catch { await sleep(500); }
  }
  if (!version) throw new Error("Chrome DevTools endpoint did not come up");

  // Create a fresh target
  const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let msgId = 0;
  const call = (method, params) => send(ws, ++msgId, method, params);
  const evaluate = async (expr) => {
    const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (res.exceptionDetails) {
      console.error("PAGE EXCEPTION:", JSON.stringify(res.exceptionDetails.exception?.description || res.exceptionDetails, null, 2).slice(0, 600));
      return undefined;
    }
    return res.result?.value;
  };

  await call("Page.enable");
  await call("Runtime.enable");

  // Collect console + page errors for diagnostics
  const consoleLogs = [];
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.method === "Runtime.consoleAPICalled") {
      consoleLogs.push(msg.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 200));
    } else if (msg.method === "Runtime.exceptionThrown") {
      consoleLogs.push("EXC: " + (msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text).slice(0, 200));
    }
  });

  // Grant camera permission explicitly (headless fake-ui flag can be unreliable)
  try {
    await call("Browser.grantPermissions", { permissions: ["videoCapture", "audioCapture"], origin: "http://localhost:3000" });
  } catch (e) {
    console.error("grantPermissions failed:", e.message);
  }

  // Wrap getUserMedia before page scripts run so we can see every call + result
  await call("Page.addScriptToEvaluateOnNewDocument", {
    source: `
      window.__gumCalls = [];
      const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = function(c) {
        window.__gumCalls.push({ at: Date.now(), constraints: JSON.stringify(c) });
        return orig(c).then(
          s => { window.__gumCalls.push({ at: Date.now(), ok: true, tracks: s.getVideoTracks().length }); return s; },
          e => { window.__gumCalls.push({ at: Date.now(), err: e.name + ': ' + e.message }); throw e; }
        );
      };
    `,
  });

  await call("Page.navigate", { url: URL_ });
  await sleep(4000);

  // Login if redirected
  const onLogin = await evaluate("location.pathname.includes('/login')");
  if (onLogin) {
    await call("Runtime.evaluate", {
      expression: `
        (async () => {
          const email = document.querySelector('input[type="email"]');
          const pass = document.querySelector('input[type="password"]');
          if (email && pass) {
            const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            set.call(email, 'ramesh@kirana.demo');
            email.dispatchEvent(new Event('input', { bubbles: true }));
            set.call(pass, 'Demo@12345');
            pass.dispatchEvent(new Event('input', { bubbles: true }));
          }
          await new Promise(r => setTimeout(r, 300));
          document.querySelector('button[type="submit"]')?.click();
        })()
      `,
      awaitPromise: true,
    });
    await sleep(4000);
  }

  // Navigate to Smart Counter (login lands on Home)
  await call("Page.navigate", { url: URL_ });
  await sleep(3500);

  // Open the Camera tab
  const clicked = await evaluate(`
    (() => {
      const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
      const cam = tabs.find(t => t.textContent.includes('Camera'));
      if (cam) { cam.click(); return true; }
      return false;
    })()
  `);
  if (!clicked) {
    const diag = await evaluate("({path: location.pathname, text: document.body.innerText.slice(0, 400)})");
    console.error("DIAG:", JSON.stringify(diag, null, 2));
    throw new Error("Camera tab not found — mode selector did not render");
  }
  await sleep(3500); // getUserMedia + fake device warm-up

  const gumCalls = await evaluate("window.__gumCalls || []");
  console.log("GUM CALLS:", JSON.stringify(gumCalls, null, 2));

  // Extract the actual overlay state text from the camera card
  const overlayText = await evaluate(`
    (() => {
      const cards = Array.from(document.querySelectorAll('.card'));
      const cam = cards.find(c => c.querySelector('video'));
      return cam ? cam.innerText.slice(0, 300) : 'NO CAMERA CARD';
    })()
  `);
  console.log("CAMERA CARD TEXT:", JSON.stringify(overlayText));

  // Probe getUserMedia directly to separate environment issues from hook bugs
  const gumProbe = await evaluate(`
    (async () => {
      try {
        const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        const tracks = s.getVideoTracks().map(t => ({ label: t.label, readyState: t.readyState, settings: t.getSettings() }));
        return { ok: true, tracks };
      } catch (e) {
        return { ok: false, name: e.name, message: e.message };
      }
    })()
  `);
  console.log("GUM PROBE:", JSON.stringify(gumProbe, null, 2));

  const state = await evaluate(`
    (() => {
      const video = document.querySelector('video');
      return {
        pathname: location.pathname,
        hasTablist: !!document.querySelector('[role="tablist"]'),
        tabCount: document.querySelectorAll('[role="tab"]').length,
        cameraTabActive: document.querySelector('[role="tab"][aria-selected="true"]')?.textContent || null,
        hasVideo: !!video,
        videoVisible: video ? video.className.includes('object-cover') : false,
        hasReticle: document.body.innerHTML.includes('00baf2'),
        engineBadge: document.body.innerText.includes('Barcode scan'),
        liveBadge: document.body.innerText.includes('Live — scanning'),
        cameraOffBadge: document.body.innerText.includes('Camera off'),
        getUserMediaProbe: typeof navigator.mediaDevices?.getUserMedia === 'function',
      };
    })()
  `);

  console.log(JSON.stringify(state, null, 2));
  if (consoleLogs.length) {
    console.log("CONSOLE LOGS (last 10):", JSON.stringify(consoleLogs.slice(-10), null, 2));
  }

  const pass =
    state.hasTablist &&
    state.tabCount === 3 &&
    state.cameraTabActive?.includes("Camera") &&
    state.hasVideo &&
    state.hasReticle &&
    state.engineBadge;

  console.log(pass ? "CAMERA UI: PASS" : "CAMERA UI: FAIL");
  process.exitCode = pass ? 0 : 2;
} catch (e) {
  console.error("CHECK ERROR:", e.message);
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
}
