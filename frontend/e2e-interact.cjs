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
    // 前置数据依赖：审批由 Harness 运行时产生，E2E 环境无 approved
    // policy_exception 审批时该成功路径 SKIPPED（不视为回归失败）。
    results.push({ name: 'SKIPPED: Kill Switch 成功路径（需 policy_exception 审批前置数据）', ok: true, detail: 'fail-closed 路径已验证' });
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

  // ===== 4. UI 页面渲染断言（C11 新 IA） =====
  const pages = [
    ['/', '工作台'],
    ['/investigations', '调查'],
    ['/signals', '信号'],
    ['/reports', '报告'],
    ['/admin/approvals', '管理'],
    ['/admin/notifications', '管理'],
    ['/admin/resilience', '管理'],
    ['/admin/security', '管理'],
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

  // ===== 5. Optimization V2 Closure Scenario A-F（C11） =====
  // 自包含：创建调查 -> Finding Review（B）-> Report Publish Gate（C）->
  // Propagation Graph（D）-> Live Data Posts（E）-> Signals（F）->
  // Investigation Shell UI（A）。
  let scenarioCaseId = null;
  try {
    const r = await api.post(BASE_API + '/cases', {
      data: { topic: 'E2E Closure 案例', platforms: ['weibo'] },
    });
    if (r.ok()) {
      scenarioCaseId = (await r.json()).id;
      check('Scenario 前置：创建调查', !!scenarioCaseId, '');
    } else {
      check('Scenario 前置：创建调查', false, 'status ' + r.status());
    }
  } catch (e) { check('Scenario 前置：创建调查', false, String(e).slice(0, 150)); }

  if (scenarioCaseId) {
    const cid = scenarioCaseId;

    // Scenario B - Finding Review
    try {
      const created = await api.post(BASE_API + '/cases/' + cid + '/findings', {
        data: { statement: 'E2E 结论：传播存在协同痕迹' },
      });
      check('B: 创建 candidate Finding', created.ok(), 'status ' + created.status());
      const finding = created.ok() ? await created.json() : null;
      if (finding) {
        const toReview = await api.post(
          BASE_API + '/cases/' + cid + '/findings/' + finding.id + '/status',
          { data: { status: 'under_review' } },
        );
        check('B: candidate -> under_review', toReview.ok(), 'status ' + toReview.status());
        const direct = await api.post(
          BASE_API + '/cases/' + cid + '/findings/' + finding.id + '/status',
          { data: { status: 'verified' } },
        );
        check('B: 普通 API verified 被拒(422)', direct.status() === 422, 'status ' + direct.status());

        const item = await api.post(BASE_API + '/cases/' + cid + '/reviews/items', {
          data: { object_type: 'finding', object_id: finding.id, summary: finding.statement },
        });
        check('B: 创建 Review item', item.ok(), 'status ' + item.status() + ' ' + (await item.text()).slice(0, 120));
        if (item.ok()) {
          const itemBody = await item.json();
          const itemId = itemBody.id || itemBody.item_id;
          const claim = await api.post(BASE_API + '/cases/' + cid + '/reviews/' + itemId + ':claim', { data: {} });
          check('B: claim Review item', claim.ok(), 'status ' + claim.status());
          const decide = await api.post(
            BASE_API + '/cases/' + cid + '/reviews/' + itemId + '/decisions',
            { data: { decision: 'approved', reason: 'e2e', actor: 'e2e' } },
          );
          check('B: Review approve 决策提交', decide.ok(), 'status ' + decide.status() + ' ' + (await decide.text()).slice(0, 150));
          const after = await api.get(BASE_API + '/cases/' + cid + '/findings/' + finding.id);
          if (after.ok()) {
            const body = await after.json();
            const status = (body.finding && body.finding.status) || body.status;
            check('B: Review approve -> Finding verified', status === 'verified', 'status=' + status);
          }
        }
      }
    } catch (e) { check('Scenario B Finding Review', false, String(e).slice(0, 150)); }

    // Scenario C - Report Publish Gate
    try {
      const reports = await api.get(BASE_API + '/reports');
      check('C: GET /reports 可用', reports.ok(), 'status ' + reports.status());
      const imported = await api.post(BASE_API + '/cases/' + cid + '/reports:from-artifact', {
        data: { artifact_id: 'no-such-artifact' },
      });
      check('C: 不存在 artifact import 被拒', imported.status() >= 400 && imported.status() < 500, 'status ' + imported.status());
    } catch (e) { check('Scenario C Report Publish', false, String(e).slice(0, 150)); }

    // Scenario D - Propagation Graph
    try {
      const graph = await api.get(BASE_API + '/cases/' + cid + '/propagation-graph');
      const ok = graph.ok();
      const body = ok ? await graph.json() : null;
      check('D: GET propagation-graph', ok && body && Array.isArray(body.nodes) && Array.isArray(body.edges), 'status ' + graph.status());
    } catch (e) { check('Scenario D Propagation', false, String(e).slice(0, 150)); }

    // Scenario E - Live Data Posts
    try {
      const posts = await api.get(BASE_API + '/cases/' + cid + '/posts', { params: { limit: 10 } });
      const ok = posts.ok();
      const body = ok ? await posts.json() : null;
      check('E: GET posts 分页结构', ok && body && Array.isArray(body.posts) && typeof body.has_more === 'boolean', 'status ' + posts.status());
      const stats = await api.get(BASE_API + '/cases/' + cid + '/posts:stats');
      check('E: GET posts:stats', stats.ok(), 'status ' + stats.status());
    } catch (e) { check('Scenario E Live Data', false, String(e).slice(0, 150)); }

    // Scenario F - Signals Inbox
    try {
      const signals = await api.get(BASE_API + '/signals');
      check('F: GET /signals', signals.ok() && Array.isArray(await signals.json()), 'status ' + signals.status());
    } catch (e) { check('Scenario F Signals', false, String(e).slice(0, 150)); }

    // Scenario A - Investigation Shell UI
    // Investigation Shell 的 h1 是调查标题本身
    const caseTitle = 'E2E Closure';
    const invPages = [
      '/overview',
      '/evidence',
      '/network',
      '/live-data',
      '/findings',
      '/report',
    ].map((tab) => ['/investigations/' + cid + tab, caseTitle]);
    for (const [path, h1] of invPages) {
      try {
        const resp = await page.goto(BASE_UI + path, { waitUntil: 'domcontentloaded', timeout: 20000 });
        await page.waitForTimeout(2000);
        const got = await page.evaluate(() => document.querySelector('h1')?.textContent?.trim() || '(none)');
        check('A: UI ' + path, resp.ok() && got.includes(h1), 'h1=' + got.slice(0, 40));
      } catch (e) { check('A: UI ' + path, false, String(e).slice(0, 150)); }
    }
  }

  await browser.close();
  const report = { total: results.length, passed: results.filter((r) => r.ok).length, results, failures };
  console.log(JSON.stringify(report, null, 2));
  process.exit(failures.length ? 1 : 0);
})();