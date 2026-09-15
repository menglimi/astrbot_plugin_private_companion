// Disposable API storage shared by browser checks. Never reaches a real bot.
function createRoomLayoutFixture() {
  const layouts = new Map();
  return {
    layouts,
    failSave: false,
    async handle(route) {
      const request = route.request(), url = new URL(request.url());
      if (!/\/home-room\/layout(?:\/update)?$/.test(url.pathname)) return false;
      const body = request.method() === 'POST' ? request.postDataJSON() : null;
      const persona = body?._persona_id || url.searchParams.get('_persona_id') || 'primary';
      if (body && this.failSave) {
        await route.fulfill({ status: 503, json: { success: false, error: '模拟存储失败' } });
      } else {
        if (body) layouts.set(persona, structuredClone(body.layout));
        await route.fulfill({ json: { success: true, data: { layout: layouts.get(persona) || null, ...(body ? { saved: true } : {}) } } });
      }
      return true;
    },
  };
}
module.exports = { createRoomLayoutFixture };
