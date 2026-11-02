"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..", "astrbot_host/native_extensions/together/web");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");

test("mobile room exposes a manual reconnect control", () => {
  assert.match(html, /id="reconnectButton"/);
  assert.match(app, /reconnectButton/);
  assert.match(app, /function reconnectNow/);
});

test("mobile room releases media and reconnects across page lifecycle", () => {
  for (const marker of ["pagehide", "visibilitychange", "function pageCleanup",
                        "WebSocket.CLOSED", "window.addEventListener(\"online\""]) {
    assert.ok(app.includes(marker), "missing lifecycle marker: " + marker);
  }
});

test("mobile room holds the screen awake and backs camera frames off", () => {
  for (const marker of ["wakeLock.request(\"screen\")", "releaseWakeLock",
                        "function cameraFrameInterval", "function scheduleCameraFrame",
                        "saveData", "effectiveType"]) {
    assert.ok(app.includes(marker), "missing mobile optimization: " + marker);
  }
  assert.doesNotMatch(app, /setInterval\(sendCameraFrame, 8000\)/);
});
