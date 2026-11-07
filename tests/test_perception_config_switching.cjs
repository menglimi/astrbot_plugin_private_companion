"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/app.js"), "utf8");
const activateSource = app.match(/function restorePerceptionSingleDetails\(\)\{[\s\S]*?\n\}\nfunction closePerceptionSingle\(\)\{[\s\S]*?\n\}\nfunction activatePerceptionTab\([\s\S]*?\n\}/)?.[0];
assert.ok(activateSource, "感知配置选择函数应存在");

function detail(id, hidden = false) {
  return {id, tagName:"DETAILS", hidden, open:true, dataset:{}, summary:{focused:false,focus(){this.focused=true;}},
    querySelector(selector) { return selector === "summary" ? this.summary : null; },
    scrollIntoView() { this.scrolled = true; }};
}

function page(id) {
  return {id:`perception-page-${id}`,dataset:{perceptionPage:`config-${id}`},hidden:true,
    classList:{toggle(){}},scrollIntoView(){this.scrolled=true;},querySelectorAll(){return [];}};
}

function tab(id, hidden = false) {
  return {dataset:{perceptionTab:id}, hidden,tabIndex:-1, attributes:{},
    classList:{toggle(){}}, setAttribute(name,value){this.attributes[name]=value;},
    focus(){this.focused=true;}};
}

function panel(id, parentPage, details = []) {
  const node={id,hidden:false,details,classList:{toggle(){}},
    contains(item){return details.includes(item);},
    closest(selector){return selector === "[data-perception-page]" ? parentPage : null;},
    querySelectorAll(selector){return selector === "details.perception-subpanel" ? details : [];},
    querySelector(){return null;}};
  return node;
}

function harness({withTabs = true} = {}) {
  const pages=[page("environment"),page("models"),page("screen"),page("health"),page("news")];
  const byId=Object.fromEntries(pages.map(item=>[item.dataset.perceptionPage,item]));
  const environment=panel("config-environment",byId["config-environment"],[
    detail("perception-detail-place"),detail("perception-detail-calendar"),detail("perception-detail-important-days"),
  ]);
  const models=panel("config-models",byId["config-models"],[
    detail("perception-detail-context"),detail("perception-detail-model-route"),
  ]);
  models.clickedTabs=[];
  models.querySelector=selector=>({click(){models.clickedTabs.push(selector);}});
  const devices=panel("config-screen",byId["config-screen"],[
    detail("perception-detail-device-access"),detail("perception-detail-providers"),detail("perception-detail-mihome",true),
  ]);
  const health=panel("config-health",byId["config-health"],[detail("perception-detail-health")]);
  const news=panel("config-news",byId["config-news"],[detail("perception-detail-external")]);
  const balance=panel("config-balance",byId["config-news"],[detail("perception-detail-balance-source"),detail("perception-detail-balance")]);
  const recall=panel("config-recall",byId["config-news"],[detail("perception-detail-recalls")]);
  const injection=panel("config-injection",byId["config-models"],[detail("perception-detail-injection")]);
  const panels=[environment,models,devices,health,news,balance,recall,injection];
  const tabs=withTabs?[
    tab("config-environment"),tab("config-models"),tab("config-screen"),tab("config-health"),tab("config-news"),
  ]:[];
  const workspace={id:"perceptionConfigWorkspace",hidden:true,dataset:{},scrollIntoView(){this.scrolled=true;},classList:{names:new Set(),toggle(name,on){on?this.names.add(name):this.names.delete(name);},contains(name){return this.names.has(name);}}};
  const singleHead={id:"perceptionSingleItemHead",hidden:true};
  const singleTitle={id:"perceptionSingleItemTitle",textContent:""};
  const selections=[];
  const nodes=new Map([workspace,singleHead,singleTitle,...pages,...panels,...panels.flatMap(item=>item.details)].map(item=>[item.id,item]));
  const context={
    document:{getElementById:id=>nodes.get(id)||null,documentElement:{dataset:{motion:"full"}},querySelectorAll(selector){return selector==='[data-perception-single-hidden]'?[...nodes.values()].filter(node=>node.dataset&&node.dataset.perceptionSingleHidden!==undefined):[];}},
    window:{matchMedia:()=>({matches:false}),CompanionPerceptionOverviewPage:{select(...args){selections.push(args);}}},
    state:{reduceMotion:false},
    $$(selector){
      if(selector===".perception-config-nav [data-perception-tab]")return tabs;
      if(selector==="[data-perception-page]")return pages;
      return [];
    },
    icons(){},
  };
  vm.runInNewContext(`${activateSource}\nglobalThis.activatePerceptionTab=activatePerceptionTab;globalThis.closePerceptionSingle=closePerceptionSingle;`,context);
  return {activate:context.activatePerceptionTab,closeSingle:context.closePerceptionSingle,pages,panels,tabs,workspace,singleHead,singleTitle,selections,
    environment,models,devices,health,news,balance,recall,injection};
}

test("dashboard selections open the matching child page and keep every configuration visible",()=>{
  const view=harness({withTabs:false});
  view.activate("config-environment");

  assert.equal(view.workspace.hidden,false);
  assert.equal(view.pages[0].hidden,false);
  assert.deepEqual(view.pages.slice(1).map(item=>item.hidden),[true,true,true,true]);
  assert.equal(view.environment.details.every(item=>item.open),true);
  assert.equal(view.selections.at(-1)[0],"config-environment:perception-detail-place");
});

test("the perception tabs switch child pages and keep their selection state accessible",()=>{
  const view=harness();
  view.activate("config-screen",{focus:true});

  assert.equal(view.pages[2].hidden,false);
  assert.equal(view.pages[0].hidden,true);
  assert.equal(view.tabs[2].attributes["aria-selected"],"true");
  assert.equal(view.tabs[2].focused,true);
  assert.equal(view.workspace.hidden,false);
  assert.equal(view.devices.details[0].open,true);
  assert.equal(view.devices.details[1].open,true);
});

test("invalid destinations and unavailable tabs do not open a child page",()=>{
  const view=harness();
  view.activate("unknown");
  view.tabs[2].hidden=true;
  view.activate("config-screen");
  assert.equal(view.workspace.hidden,true);
  assert.equal(view.selections.length,0);
  assert.equal(view.pages.every(item=>item.hidden),true);
});

test("model links select the requested task route and external links stay on the fifth page",()=>{
  const view=harness();
  for(const modelTab of ["fn","voice","image"]){
    view.activate("config-models:perception-detail-model-route",{modelTab,scroll:true});
    assert.equal(view.models.clickedTabs.at(-1),`#modelWorkspace [data-model-tab="${modelTab}"]`);
    assert.equal(view.pages[1].hidden,false);
    assert.equal(view.models.details.every(item=>item.open),true);
    assert.equal(view.selections.at(-1)[1].modelTab,modelTab);
  }
  assert.equal(view.workspace.scrolled,true);

  view.activate("config-balance:perception-detail-balance-source");
  assert.equal(view.workspace.dataset.perceptionSection,"balance");
  assert.equal(view.pages[4].hidden,false);
  assert.equal(view.tabs[4].attributes["aria-selected"],"true");
  assert.equal(view.pages[1].hidden,true);
  assert.equal(view.balance.details[0].open,true);
  view.closeSingle();
  assert.equal(view.workspace.dataset.perceptionSection,undefined);
});

test("deep links focus their real section without collapsing sibling settings",()=>{
  const view=harness();
  view.activate("config-environment:perception-detail-calendar",{focus:true});
  const [place,calendar,importantDays]=view.environment.details;
  assert.equal(calendar.summary.focused,true);
  assert.deepEqual([place.open,calendar.open,importantDays.open],[true,true,true]);

  view.activate("config-screen:perception-detail-mihome");
  assert.equal(view.pages[2].hidden,false);
  assert.equal(view.devices.details[0].open,true);
  assert.equal(view.devices.details[1].open,true);
  assert.equal(view.devices.details[2].open,true);
});

test("configuration cards are expanded by default and grouped into the five designed pages",()=>{
  for(const [id,pageId] of [
    ["config-environment","perception-page-environment"],
    ["config-models","perception-page-models"],
    ["config-screen","perception-page-reality"],
    ["config-health","perception-page-health"],
    ["config-news","perception-page-external"],
    ["config-balance","perception-page-external"],
    ["config-recall","perception-page-external"],
    ["config-injection","perception-page-models"],
  ]) {
    const sectionStart=app.indexOf(`id="${id}"`);
    const sectionEnd=app.indexOf("</section>",sectionStart);
    const section=app.slice(sectionStart,sectionEnd);
    assert.ok(sectionStart>=0&&sectionEnd>sectionStart,`${id} should exist`);
    assert.ok(app.lastIndexOf(`id="${pageId}"`,sectionStart)>=0,`${id} should belong to ${pageId}`);
    assert.doesNotMatch(section,/name="perception-/);
  }
  assert.match(app,/01<\/span><h2>时空与环境<\/h2>/);
  assert.match(app,/02<\/span><h2>模型能力<\/h2>/);
  assert.match(app,/03<\/span><h2>现实触及功能<\/h2>/);
  assert.match(app,/04<\/span><h2>健康感知<\/h2>/);
  assert.match(app,/05<\/span><h2>外部与记录<\/h2>/);
});

test("date controls and important day settings stay in the time page",()=>{
  const placeStart=app.indexOf('id="perception-detail-place"');
  const placeEnd=app.indexOf("</details>",placeStart);
  const place=app.slice(placeStart,placeEnd);
  assert.ok(placeStart>=0&&placeEnd>placeStart);
  assert.match(place,/id="nativeEnvironmentWorkspace"/);
  assert.match(place,/id="perceptionRoleWeatherWorkspace"/);
  assert.match(place,/id="perceptionWeatherConfigWorkspace"/);
  const calendarStart=app.indexOf('id="perception-detail-calendar"');
  const calendarEnd=app.indexOf("</details>",calendarStart);
  assert.match(app.slice(calendarStart,calendarEnd),/id="nativeCalendarWorkspace"/);
  const daysStart=app.indexOf('id="perception-detail-important-days"');
  const daysEnd=app.indexOf("</details>",daysStart);
  assert.match(app.slice(daysStart,daysEnd),/id="perceptionDatesWorkspace"/);
  assert.doesNotMatch(place,/perceptionDatesWorkspace|nativeCalendarWorkspace/);
  assert.match(app,/mountConfigEditor\('perceptionDatesWorkspace'\s*,\s*\['proactive\.contact'\][\s\S]*?sectionIds:\['meal','date'\]/);
});

test("the image understanding shortcut opens its real configuration tab",()=>{
  const shortcut=app.match(/data-understanding-tab-target="image"/);
  assert.ok(shortcut);
  assert.match(app,/CompanionUnderstandingPage\?\.select\?\.\(el\.dataset\.understandingTabTarget/);
  const branch=app.slice(app.indexOf("  if(el.dataset.go){"),app.indexOf("  if(el.dataset.life){"));
  const actions=[];
  const context={
    el:{dataset:{go:"manage",manage:"understanding",understandingTabTarget:"image"}},
    navigate(...args){actions.push(["navigate",...args]);},
    requestAnimationFrame(callback){callback();},
    window:{CompanionUnderstandingPage:{select(...args){actions.push(["select",...args]);}}},
  };
  vm.runInNewContext(`(function(){${branch}})()`,context);
  assert.deepEqual(JSON.parse(JSON.stringify(actions)),[["navigate","manage","understanding",null],["select","image",{focus:true}]]);
});

test("model routes still open the requested voice or image tab after navigation",()=>{
  const branch=app.slice(app.indexOf("  if(el.dataset.go){"),app.indexOf("  if(el.dataset.life){"));
  for(const modelTabTarget of ["voice","image"]){
    const actions=[];
    const context={
      el:{dataset:{go:"manage",manage:"models",modelTabTarget}},
      navigate(...args){actions.push(["navigate",...args]);},
      requestAnimationFrame(callback){callback();},
      document:{querySelector(selector){return {click(){actions.push(["tab",selector]);}};}},
    };
    vm.runInNewContext(`(function(){${branch}})()`,context);
    assert.deepEqual(actions,[["navigate","manage","models",undefined],["tab",`#modelWorkspace [data-model-tab="${modelTabTarget}"]`]]);
  }
});

test("a single board item opens only its own configuration panel",()=>{
  const view=harness();
  view.activate("config-screen:perception-detail-device-access",{single:true,itemId:"device-location",itemTitle:"当前定位"});

  assert.equal(view.workspace.hidden,false);
  assert.equal(view.workspace.classList.contains("is-single-item"),true);
  assert.equal(view.pages[2].hidden,false);
  assert.equal(view.devices.details[0].hidden,false);
  assert.equal(view.devices.details[1].hidden,true);
  assert.equal(view.devices.details[2].hidden,true);
  assert.equal(view.singleHead.hidden,false);
  assert.equal(view.singleTitle.textContent,"当前定位");

  view.closeSingle();
  assert.equal(view.workspace.hidden,true);
  assert.equal(view.workspace.classList.contains("is-single-item"),false);
  assert.equal(view.singleHead.hidden,true);
  assert.equal(view.devices.details[0].hidden,false);
  assert.equal(view.devices.details[1].hidden,false);
  assert.equal(view.devices.details[2].hidden,true);
});
