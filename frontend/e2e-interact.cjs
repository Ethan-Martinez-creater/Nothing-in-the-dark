// COIFESP Agent 前端+API E2E 交互冒烟：读 API 断言 + Kill Switch/Incident 真实
// 交互（M21 授权链路）+ UI 页面渲染断言；任何失败以非零退出码结束。
// 用法：先运行 scripts/dev-backend.cmd 与 scripts/dev-frontend.cmd，再执行 node e2e-interact.cjs
const PLAYWRIGHT_PATH = process.env.PLAYWRIGHT_PATH || null;
try {
  var { chromium } = PLAYWRIGHT_PATH ? require(PLAYWRIGHT_PATH) : require('playwright');
} catch (e) {
  console.error('E2E FAIL: playwright 未安装。', e.message);
  process.exit(2);
}

const BASE_UI = process.env.E2E_BASE || 'http://127.0.0.1:5173';
const BASE_API = process.env.E2E_API || 'http://127.0.0.1:8000/api/v1';
const failures = [];
const results = [];

function check(name, cond, detail) {
  results.push({ name, ok: !!cond, detail: detail || '' });
  if (!cond) failures.push(name + (detail ? ' | ' + detail : ''));
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext();
  const api = ctx.request;
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + String(e).slice(0, 200)));

  // ===== 1. 读 API 断言 =====
  const endpoints = [
    ['GET /approvals', '/approvals'],
    ['GET /approvals/stats/summary', '/approvals/stats/summary'],
    ['GET /system/resilience/kill-switches', '/system/resilience/kill-switches'],
    ['GET /system/resilience/dead-letters', '/system/resilience/dead-letters'],
    ['GET /memories', '/memories'],
    ['GET /system/sandbox/health', '/system/sandbox/health'],
    ['GET /system/content-security/summary', '/system/content-security/summary'],
  ];
  for (const [name, path] of endpoints) {
    try {
      const r = await api.get(BASE_API + path);
      check(name, r.ok(), 'status ' + r.status());
    } catch (e) { check(name, false, String(e).slice(0, 150)); }
  }

  // ===== 2. Kill Switch 交互（M21 授权签发/消费链路） =====
  let approvedList = [];
  try {
    const r = await api.get(BASE_API + '/approvals?status=approved');
    if (r.ok()) {
      approvedList = await r.json();
      check('GET /approvals 返回数组', Array.isArray(approvedList), '');
    } else {
      check('GET /approvals?status=approved', false, 'status ' + r.status());
    }
  } catch (e) { check('approvals 列表解析', false, String(e).slice(0, 150)); }

  // 2a) fail-closed：作用域不匹配的审批启用 Kill Switch 必须被 409 拒绝。
  const anyApproval = (approvedList || [])[0];
  if (anyApproval) {
    try {
      const body = { scope: 'global', target: '*', reason: 'e2e smoke', actor: 'e2e', approval_id: anyApproval.id };
      const r = await api.post(BASE_API + '/system/resilience/kill-switches', { data: body });
      const text = await r.text();
      const scoped = text.includes('not scoped') || text.includes('not been approved');
      check('Kill Switch 作用域不匹配 fail-closed(409)', r.status() === 409 && scoped, 'status ' + r.status() + ' ' + text.slice(0, 120));
    } catch (e) { check('Kill Switch fail-closed 交互', false, String(e).slice(0, 150)); }
  } else {
    check('存在已批准审批供 Kill Switch fail-closed 验证', false, '无 approved 审批');
  }

  // 2b) 成功路径：policy_exception 审批（跳过 action 匹配校验）可用。
  const policyApproval = (approvedList || []).find((a) => a.approval_type === 'policy_exception');
  if (policyApproval) {
    try {
      const body = { scope: 'global', target: '*', reason: 'e2e smoke', actor: 'e2e', approval_id: policyApproval.id };
      const r = await api.post(BASE_API + '/system/resilience/kill-switches', { data: body });
      check('Kill Switch enable(policy_exception)', r.ok(), 'status ' + r.status() + ' ' + (await r.text()).slice(0, 150));
      if (r.ok()) {
        const created = await r.json();
        const ksId = created.id || (created.record && created.record.id) || (created.kill_switch && created.kill_switch.id);
        if (ksId) {
          const d = await api.post(BASE_API + '/system/resilience/kill-switches/' + ksId + ':disable', { data: { actor: 'e2e', reason: 'e2e smoke cleanup' } });
          check('Kill Switch disable', d.ok(), 'status ' + d.status() + ' ' + (await d.text()).slice(0, 150));
          const lst = await api.get(BASE_API + '/system/resilience/kill-switches');
          if (lst.ok()) {
            const arr = await lst.json();
            const rec = (arr || []).find((x) => (x.id || x.kill_switch_id) === ksId);
            check('Kill Switch 状态为 disabled', !!(rec && (rec.enabled === false || rec.status === 'disabled')), JSON.stringify(rec).slice(0, 120));
          }
        } else {
          check('Kill Switch 响应含 id', false, JSON.stringify(created).slice(0, 200));
        }
      }
    } catch (e) { check('Kill Switch 成功路径交互', false, String(e).slice(0, 150)); }
  } else {
    check('存在 policy_exception 审批（Kill Switch 成功路径）', false, '缺少 E2E 前置审批数据，成功路径未执行');
  }

  // ===== 3. Incident 创建 → 关闭（自包含） =====
  try {
    const r = await api.post(BASE_API + '/system/resilience/incidents', { data: { title: 'e2e smoke incident', severity: 'warning', impact: 'smoke test' } });
    check('Incident 创建', r.ok(), 'status ' + r.status() + ' ' + (await r.text()).slice(0, 120));
    if (r.ok()) {
      const created = await r.json();
      const incId = created.id || (created.record && created.record.id);
      if (incId) {
        const c = await api.post(BASE_API + '/system/resilience/incidents/' + incId + ':close', { data: { recovery: { note: 'e2e' }, retro: {} } });
        check('Incident 关闭', c.ok(), 'status ' + c.status());
      }
    }
  } catch (e) { check('Incident 交互', false, String(e).slice(0, 150)); }

  // ===== 4. UI 页面渲染断言 =====
  const pages = [
    ['/approvals', '审批箱'],
    ['/reviews', '分层人工调查'],
    ['/resilience', '事故处置台'],
    ['/memories', '记忆安全'],
    ['/security', '安全治理'],
  ];
  for (const [path, h1] of pages) {
    try {
      const resp = await page.goto(BASE_UI + path, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForTimeout(2500);
      const got = await page.evaluate(() => document.querySelector('h1')?.textContent?.trim() || '(none)');
      check('UI ' + path + ' h1', resp.ok() && got.includes(h1), 'h1=' + got.slice(0, 40));
    } catch (e) { check('UI ' + path, false, String(e).slice(0, 150)); }
  }
  check('全程无 console/pageerror', consoleErrors.length === 0, consoleErrors.join(' | ').slice(0, 300));

  await browser.close();
  const report = { total: results.length, passed: results.filter((r) => r.ok).length, results, failures };
  console.log(JSON.stringify(report, null, 2));
  process.exit(failures.length ? 1 : 0);
})();