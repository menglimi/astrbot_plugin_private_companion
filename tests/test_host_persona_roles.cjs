"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "宿主管理", "app.js"), "utf8");
const keySource = source.slice(source.indexOf("  const nativePersonaKey ="),
  source.indexOf("  function activeRoleAssignments("));
const slice = source.slice(source.indexOf("  function expandPersonaRoles("),
  source.indexOf("  function makeTopologySnapshot("));
const context = vm.createContext({});
vm.runInContext(keySource + "\n" + slice + "\nthis.expandPersonaRoles = expandPersonaRoles;", context);

function target(botId, personaId, extra = {}) {
  return {native: true, bot_id: botId, persona_id: personaId, details_available: true, ...extra};
}

test("a window using an already-mapped persona joins that role whatever the Bot account", () => {
  const data = {
    personas: [
      {bot_id: "bot-main", persona_id: "星缘-私聊", role_id: "role-main"},
      {bot_id: "bot-main", persona_id: "星缘-群聊", role_id: "role-main"},
    ],
    discovered: {targets: [
      target("bot-main", "星缘-私聊"),
      target("bot-test", "星缘-私聊"),
      target("bot-test", "星缘-群聊"),
      target("bot-other", "别的人格"),
    ]},
  };
  const result = context.expandPersonaRoles(data);
  const added = JSON.parse(JSON.stringify(result.personas.slice(2)));
  assert.deepEqual(added, [
    {bot_id: "bot-test", persona_id: "星缘-私聊", role_id: "role-main"},
    {bot_id: "bot-test", persona_id: "星缘-群聊", role_id: "role-main"},
  ]);
  assert.equal(result.personas.filter(row => row.bot_id === "bot-main").length, 2,
    "an existing exact mapping is not duplicated");
});

test("a persona explicitly mapped to two roles stays ambiguous", () => {
  const result = context.expandPersonaRoles({
    personas: [
      {bot_id: "bot-a", persona_id: "星缘-私聊", role_id: "role-a"},
      {bot_id: "bot-b", persona_id: "星缘-私聊", role_id: "role-b"},
    ],
    discovered: {targets: [target("bot-test", "星缘-私聊")]},
  });
  assert.equal(result.personas.length, 2);
});

test("unknown persona details and an empty mapping set add nothing", () => {
  assert.equal(context.expandPersonaRoles({personas: [], discovered: {targets: [
    target("bot-test", "星缘-私聊")]}}).personas.length, 0);
  assert.equal(context.expandPersonaRoles({personas: [
    {bot_id: "bot-main", persona_id: "星缘-私聊", role_id: "role-main"}],
    discovered: {targets: [target("bot-test", "星缘-私聊", {details_available: false})]},
  }).personas.length, 1);
});


test("refresh never restores a removed account mapping just because its window uses the same persona", () => {
  const saved = {accounts: [{bot_id: "bot-native"}],
    personas: [{bot_id: "bot-native", persona_id: "shared", role_id: "role"}],
    discovered: {targets: [target("bot-native", "shared"), target("bot-official", "shared")]}};
  const result = context.expandPersonaRoles(saved);
  assert.deepEqual(JSON.parse(JSON.stringify(result.personas)), saved.personas);
  assert.equal(saved.personas.length, 1, "reading does not mutate the saved mapping");
});

test("persona inheritance still works within the explicitly saved account set", () => {
  const result = context.expandPersonaRoles({accounts: [{bot_id: "bot-main"}, {bot_id: "bot-second"}],
    personas: [{bot_id: "bot-main", persona_id: "shared", role_id: "role"}],
    discovered: {targets: [target("bot-second", "shared"), target("bot-unselected", "shared")]}});
  assert.deepEqual(JSON.parse(JSON.stringify(result.personas.slice(1))),
    [{bot_id: "bot-second", persona_id: "shared", role_id: "role"}]);
});
