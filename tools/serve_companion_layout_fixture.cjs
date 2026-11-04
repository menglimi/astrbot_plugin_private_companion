#!/usr/bin/env node
/*
 * 嵌套工作区布局夹具的静态服务。
 *
 * 用途：在真实 Chrome 里复现“陪伴小屋 765 视口 + identity 工作区 iframe 2400”
 * 的嵌套布局，供对话框定位的失败/通过断言使用。页面资源全部来自仓库里真实
 * 的 pages/陪伴小屋；只有控制台 SDK 被 tests/fixtures/companion-layout/bridge.js
 * 替换成合成数据。
 *
 * 用法：
 *   node tools/serve_companion_layout_fixture.cjs            # 随机端口
 *   FIXTURE_PORT=8907 node tools/serve_companion_layout_fixture.cjs
 *
 * 启动后打印一行 JSON：{"fixture": "...","shell":"..."}；fixture 即要在浏览器里
 * 打开的地址。断言方需要把工作区 frame 内 dialog 的矩形交给页面上的
 * `__fixture.verdict(rect)` 判定。
 */
"use strict";
const http = require("node:http");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const PAGES = path.join(ROOT, "pages", "陪伴小屋");
const FIXTURES = path.join(ROOT, "tests", "fixtures", "companion-layout");
const SHELL_INJECT = '<script src="/__fixture/bridge.js"></script>';

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".woff2": "font/woff2",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".mp4": "video/mp4",
};

function injectBridge(html) {
  const head = html.indexOf("<head>");
  if (head < 0) throw new Error("shell index.html has no <head>");
  const at = head + "<head>".length;
  return html.slice(0, at) + SHELL_INJECT + html.slice(at);
}

async function sendFile(response, file, { inject = false } = {}) {
  let body = await fsp.readFile(file);
  if (inject) body = Buffer.from(injectBridge(body.toString("utf8")), "utf8");
  response.writeHead(200, {
    "content-type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream",
    "cache-control": "no-store",
  });
  response.end(body);
}

function safeJoin(base, requestPath) {
  const decoded = decodeURIComponent(requestPath);
  const target = path.resolve(base, "." + decoded);
  if (target !== base && !target.startsWith(base + path.sep)) return null;
  return target;
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  try {
    if (url.pathname === "/__fixture/index.html" || url.pathname === "/__fixture/") {
      return await sendFile(response, path.join(FIXTURES, "index.html"));
    }
    if (url.pathname === "/__fixture/harness.js") {
      return await sendFile(response, path.join(FIXTURES, "harness.js"));
    }
    if (url.pathname === "/__fixture/bridge.js") {
      return await sendFile(response, path.join(FIXTURES, "bridge.js"));
    }
    const shell = url.pathname === "/" || url.pathname === "/index.html";
    if (shell) {
      const fixtureShell = url.searchParams.get("fixture") === "1";
      return await sendFile(response, path.join(PAGES, "index.html"), { inject: fixtureShell });
    }
    const file = safeJoin(PAGES, url.pathname);
    if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("not found");
      return;
    }
    return await sendFile(response, file);
  } catch (error) {
    response.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
    response.end(String(error && error.stack || error));
  }
});

/* 供测试在进程内启动：返回 fixture/shell 地址，由调用方负责 close()。 */
function startFixtureServer({ port = 0 } = {}) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      const actual = server.address().port;
      const base = "http://127.0.0.1:" + actual;
      resolve({
        server,
        base,
        fixture: base + "/__fixture/index.html",
        shell: base + "/index.html?fixture=1#manage/identity/people",
        close: () => new Promise(done => server.close(done)),
      });
    });
  });
}

module.exports = { startFixtureServer };

if (require.main === module) {
  startFixtureServer({ port: Number(process.env.FIXTURE_PORT || 0) }).then(({ fixture, shell }) => {
    process.stdout.write(JSON.stringify({ fixture, shell }) + "\n");
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => server.close(() => process.exit(0)));
  }
}
