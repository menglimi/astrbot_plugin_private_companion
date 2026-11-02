/* Opaque-origin compatible bridge for bundled workspaces. */
"use strict";
(() => {
  const pending=new Map();let count=0,context=null,readyResolve;
  const DEFAULT_REQUEST_TIMEOUT_MS=18000,MAX_REQUEST_TIMEOUT_MS=300000;
  const ready=new Promise(resolve=>{readyResolve=resolve;});
  const send=payload=>parent.postMessage({channel:"companion-workspace",...payload},"*");
  /* 外观维度：主题沿用原有通道，另外四项按 name/value 应用。
     工作区各自的样式表只用得上其中一部分，未声明的维度不会产生副作用。
     主题列表取自 theme.js 注册表，不在这里再抄一份——抄一份就会飘，
     主页面加主题而工作区不认，值会被这个白名单挡掉、主题静默不生效。 */
  const themeIds = globalThis.CompanionTheme?.IDS || ["courtyard","dawn","dusk","night","walnut","contrast"];
  const appearanceDimensions={
    theme:{dataset:"theme",values:themeIds},
    density:{dataset:"density",values:["compact","standard","roomy"]},
    radius:{dataset:"radius",values:["sharp","standard","round"]},
    typeScale:{dataset:"typeScale",values:["s","m","l","xl"]},
    motion:{dataset:"motion",values:["full","low","off"]},
  };
  const applyDimension=(name,value)=>{
    const spec=appearanceDimensions[name];
    // 主题额外走一次旧名迁移：工作区若被单独打开，localStorage 里可能还是 mist/grove/coral。
    if(name==="theme"&&globalThis.CompanionTheme)value=globalThis.CompanionTheme.migrate(value);
    if(!spec||!spec.values.includes(value))return false;
    document.documentElement.dataset[spec.dataset]=value;
    document.dispatchEvent(new CustomEvent("companion-appearance-change",{detail:{name,value}}));
    return true;
  };
  window.addEventListener("message",event=>{
    if(event.source!==parent||event.data?.channel!=="companion-workspace")return;
    const message=event.data;
    if(message.kind==="context"){context=message.context;readyResolve(context);return;}
    if(message.kind==="host-mappings-refreshed"){
      document.dispatchEvent(new CustomEvent("companion-mappings-change",{detail:message}));return;
    }
    if(message.kind==="host-mappings-refresh-failed"){
      document.dispatchEvent(new CustomEvent("companion-mappings-refresh-failed",{detail:message}));return;
    }
    if(message.kind==="theme"&&appearanceDimensions.theme.values.includes(message.theme)){
      applyDimension("theme",message.theme);
      document.dispatchEvent(new CustomEvent("companion-theme-change",{detail:{theme:message.theme}}));
      return;
    }
    if(message.kind==="appearance"){
      if(message.appearance)for(const [name,value] of Object.entries(message.appearance))applyDimension(name,value);
      else applyDimension(message.name,message.value);
      return;
    }
    if(message.kind==="navigate-failed"){
      document.dispatchEvent(new CustomEvent("companion-navigate-failed",{detail:message}));return;
    }
    if(message.kind!=="response"||!pending.has(message.requestId))return;
    const item=pending.get(message.requestId);pending.delete(message.requestId);clearTimeout(item.timer);
    if(message.ok)item.resolve(message.data);else item.reject(Object.assign(new Error(message.error),
      {code:message.code,reason:message.reason,sourceLabel:message.sourceLabel,
        confirmed:message.confirmed===true}));
  });
  function request(method,path,body,options){return new Promise((resolve,reject)=>{
    const requestId="workspace-"+(++count);
    const requested=options?.timeoutMs;
    const timeoutMs=Number.isInteger(requested)&&requested>=1000
      ?Math.min(requested,MAX_REQUEST_TIMEOUT_MS):DEFAULT_REQUEST_TIMEOUT_MS;
    const timer=setTimeout(()=>{pending.delete(requestId);reject(Object.assign(
      new Error("未收到执行结果，请核对后重试本次操作。"),{code:"bridge_timeout",confirmed:false}));},timeoutMs+1000);
    pending.set(requestId,{resolve,reject,timer});send({kind:"request",requestId,method,path,body,timeoutMs});
  });}
  window.AstrBotPluginPage={ready:()=>ready,getContext:()=>context,
    apiGet:(path,body,options)=>request("GET",path,body,options),
    apiPost:(path,body,options)=>request("POST",path,body,options)};
  document.addEventListener("DOMContentLoaded",async()=>{
    document.querySelectorAll("#start-host,#stop-host").forEach(el=>el.hidden=true);
    await ready;
    if(context.tab)document.querySelector('[data-tab="'+CSS.escape(context.tab)+'"]')?.click();
    let last=0;
    new ResizeObserver(()=>{const height=document.body.scrollHeight;if(height!==last){last=height;send({kind:"resize",height});}}).observe(document.body);
  });
  send({kind:"ready"});
})();
