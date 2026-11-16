"use strict";
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const {fixture} = require("./role_scope_browser.cjs");

const cookieName = "companion_browser_test";
const pageRoot = path.join(__dirname, "..", "..", "pages", "陪伴小屋");
const read = name => fs.readFileSync(path.join(pageRoot, name), "utf8");

// 合成 API 仅模拟浏览器往返 Cookie；生产端的身份、校验和响应头由 Python 测试覆盖。
function cookiePreferences(request, response, method, patch = {}) {
  const raw = (request.headers.cookie || "").split("; ").find(item => item.startsWith(cookieName + "="));
  const preferences = raw ? JSON.parse(Buffer.from(raw.slice(cookieName.length + 1), "base64url").toString())
    : {selection:null,room_card_position:null};
  if (method === "POST") {
    Object.assign(preferences, patch);
    const value = Buffer.from(JSON.stringify(preferences)).toString("base64url");
    response.setHeader("Set-Cookie", `${cookieName}=${value}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax`);
  }
  return {preferences};
}

// 与 AstrBot 相同的 iframe 沙箱；同源父页面代为请求 API 并传回结果。
const parentPage = `<!doctype html><meta charset="utf-8"><style>html,body,iframe{margin:0;width:100%;height:100%;border:0;overflow:hidden}</style>
<iframe id="plugin" sandbox="allow-scripts allow-forms allow-downloads" src="/index.html#manage/expression/reaction"></iframe>
<script>
addEventListener("message",async event=>{
  const frame=document.querySelector("iframe");
  if(event.source!==frame.contentWindow || event.data?.channel!=="test-plugin-request")return;
  const {id,method,endpoint,params}=event.data;
  try {
    const response=await fetch("/rpc",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({method,endpoint,params})});
    if(!response.ok)throw new Error("browser_preferences_unavailable");
    event.source.postMessage({channel:"test-plugin-response",id,value:await response.json()},"*");
  }catch(error){event.source.postMessage({channel:"test-plugin-response",id,error:error.message},"*");}
});
</script>`;

const frameBridge = `<script>
(()=>{
  let sequence=0;
  const pending=new Map();
  addEventListener("message",event=>{
    if(event.source!==parent || event.data?.channel!=="test-plugin-response")return;
    const {id,value,error}=event.data, task=pending.get(id);
    if(!task)return;
    pending.delete(id);
    if(error)task.reject(new Error(error));else task.resolve(value);
  });
  const request=(method,endpoint,params)=>new Promise((resolve,reject)=>{
    const id=++sequence;pending.set(id,{resolve,reject});
    parent.postMessage({channel:"test-plugin-request",id,method,endpoint,params},"*");
  });
  window.AstrBotPluginPage={ready:async()=>({pluginName:"astrbot_plugin_private_companion",pageName:"陪伴小屋"}),
    apiGet:(endpoint,params)=>request("GET",endpoint,params),apiPost:(endpoint,params)=>request("POST",endpoint,params),onContext:()=>()=>{}};
})();
</script>`;

async function browserPreferencesServer() {
  const data = fixture();
  const css = JSON.parse(read("css-sources.json")).map(read).join("\n");
  let failPreferences = false;
  const server = http.createServer(async (request,response) => {
    const pathname = decodeURIComponent(new URL(request.url,"http://test").pathname);
    if (pathname === "/rpc") {
      let raw = "";
      for await (const chunk of request) raw += chunk;
      const {method,endpoint,params} = JSON.parse(raw);
      response.setHeader("Content-Type","application/json");
      if (endpoint === "page/browser-preferences") {
        data.calls.push({method,endpoint,params});
        if (failPreferences) { response.writeHead(503); response.end("{}"); return; }
        response.end(JSON.stringify(cookiePreferences(request,response,method,params)));
      } else response.end(JSON.stringify(data.rpc(method,endpoint,params)));
      return;
    }
    if (pathname === "/") { response.setHeader("Content-Type","text/html; charset=utf-8"); response.end(parentPage); return; }
    const file = pathname.slice(1);
    if (file === "bundle.css") { response.setHeader("Content-Type","text/css"); response.end(css); return; }
    const types = {".js":"application/javascript",".css":"text/css",".html":"text/html; charset=utf-8",".webp":"image/webp",".json":"application/json",".svg":"image/svg+xml"};
    try {
      const content = file === "index.html" ? read(file).replace("<head>","<head>"+frameBridge) : fs.readFileSync(path.join(pageRoot,file));
      response.setHeader("Content-Type",types[path.extname(file)] || "application/octet-stream");
      response.end(content);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  return {data,url:`http://127.0.0.1:${server.address().port}/`,setFailure:value=>{failPreferences=value;},
    close:()=>new Promise(resolve=>server.close(resolve))};
}

module.exports = {cookiePreferences,browserPreferencesServer,cookieName};
