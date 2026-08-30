// COIFESP Agent E2E 交互验证（FC5 Final Closure）。
// 三段输出：Smoke checks / Optimization V2 Closure A-F interaction checks /
// Other Harness checks。A-F 全部为真实浏览器交互（点击、输入、断言 DOM 状态），
// fixture 由 backend/scripts/seed_final_closure_e2e.py 通过正常 Repository 写入。
// 用法：后端（DATABASE_URL 指向 E2E SQLite）与前端（VITE_E2E=true npm run dev）
// 启动后执行 node e2e-interact.cjs。任何失败以非零退出码结束。
const { spawnSync } = require('child_process');
const path = require('path');

const PLAYWRIGHT_PATH = process.env.PLAYWRIGHT_PATH || null;
try {
  var { chromium } = PLAYWRIGHT_PATH ? require(PLAYWRIGHT_PATH) : require('playwright');
} catch (e) {
  console.error('E2E FAIL: playwright 未安装。', e.message);
  process.exit(2);
}

const BASE_UI = process.env.E2E_BASE || 'http://127.0.0.1:5173';
const BASE_API = process.env.E2E_API || 'http://127.0.0.1:8000/api/v1';

const results = { smoke: [], harness: [], closure: [] };
const failures = [];

function push(section, name, ok, detail) {
  results[section].push({ name, ok: !!ok, detail: detail || '' });
  if (!ok) failures.push(`[${section}] ` + name + (detail ? ' | ' + detail : ''));
}
const check = (name, ok, detail) => push('smoke', name, ok, detail);
const checkH = (name, ok, detail) => push('harness', name, ok, detail);
const checkC = (name, ok, detail) => push('closure', name, ok, detail);

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

// ---- Fixture: run the deterministic seed script and parse its JSON line. --
function runSeed() {
  const backendDir = path.resolve(__dirname, '../backend');
  const python = process.env.COIFESP_PYTHON || 'python';
  const env = { ...process.env };
  if (!env.DATABASE_URL) {
    env.DATABASE_URL = 'sqlite+aiosqlite:///./data/e2e_closure.db';
  }
  const proc = spawnSync(python, ['scripts/seed_final_closure_e2e.py'], {
    cwd: backendDir,
    env,
    encoding: 'utf8',
  });
  if (proc.status !== 0) {
    throw new Error('seed 脚本失败: ' + (proc.stderr || proc.stdout).slice(-500));
  }
  const lines = proc.stdout.trim().split('\n');
  return JSON.parse(lines[lines.length - 1]);
}

(async () => {
  let fx;
  try {
    fx = runSeed();
    checkH('E2E fixture seed', !!fx.case_id && !!fx.propagation_edge_id, JSON.stringify(fx).slice(0, 120));
  } catch (e) {
    checkH('E2E fixture seed', false, String(e).slice(0, 300));
    console.log(JSON.stringify({ smoke: results.smoke, harness: results.harness, closure: results.closure, failures }, null, 2));
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext();
  const api = ctx.request;
  const page = await ctx.newPage();
  const consoleErrors = [];
  const badResponses = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + String(e).slice(0, 200)));
  page.on('response', (resp) => {
    if (resp.status() >= 400) badResponses.push(resp.status() + ' ' + resp.url().slice(0, 160));
  });

  const textOf = () => page.evaluate(() => document.body.innerText);
  const h1Of = () => page.evaluate(() => document.querySelector('h1')?.textContent?.trim() || '(none)');

  // ===== 1. Smoke: 读 API 断言 =====
  const endpoints = [
    ['GET /approvals', '/approvals'],
    ['GET /approvals/stats/summary', '/approvals/stats/summary'],
    ['GET /system/resilience/kill-switches', '/system/resilience/kill-switches'],
    ['GET /system/resilience/dead-letters', '/system/resilience/dead-letters'],
    ['GET /memories', '/memories'],
    ['GET /system/sandbox/health', '/system/sandbox/health'],
    ['GET /system/content-security/summary', '/system/content-security/summary'],
  ];
  for (const [name, p] of endpoints) {
    try {
      const r = await api.get(BASE_API + p);
      check(name, r.ok(), 'status ' + r.status());
    } catch (e) { check(name, false, String(e).slice(0, 150)); }
  }

  // ===== 2. Harness: Kill Switch fail-closed（成功路径依赖 policy_exception
  // 审批数据，无法自包含造数，按原限制记录为 unrelated skip，不计入 A-F）。 ==
  let approvedList = [];
  try {
    const r = await api.get(BASE_API + '/approvals?status=approved');
    if (r.ok()) approvedList = await r.json();
  } catch (e) { /* harness only */ }
  const anyApproval = (approvedList || [])[0];
  if (anyApproval) {
    try {
      const body = { scope: 'global', target: '*', reason: 'e2e smoke', actor: 'e2e', approval_id: anyApproval.id };
      const r = await api.post(BASE_API + '/system/resilience/kill-switches', { data: body });
      const text = await r.text();
      const scoped = text.includes('not scoped') || text.includes('not been approved');
      checkH('Kill Switch 作用域不匹配 fail-closed(409)', r.status() === 409 && scoped, 'status ' + r.status());
    } catch (e) { checkH('Kill Switch fail-closed', false, String(e).slice(0, 150)); }
  } else {
    results.harness.push({ name: 'SKIPPED(unrelated): Kill Switch 成功路径需 policy_exception 审批前置数据', ok: true, detail: '与 V2 Closure A-F 无关' });
  }

  // ===== 3. Harness: Incident 创建 → 关闭（自包含） =====
  try {
    const r = await api.post(BASE_API + '/system/resilience/incidents', { data: { title: 'e2e smoke incident', severity: 'warning', impact: 'smoke test' } });
    if (r.ok()) {
      const created = await r.json();
      const incId = created.id || (created.record && created.record.id);
      const c = incId
        ? await api.post(BASE_API + '/system/resilience/incidents/' + incId + ':close', { data: { recovery: { note: 'e2e' }, retro: {} } })
        : null;
      checkH('Incident 创建+关闭', !!incId && !!c && c.ok(), 'status ' + (c ? c.status() : 'n/a'));
    } else {
      checkH('Incident 创建', false, 'status ' + r.status());
    }
  } catch (e) { checkH('Incident 交互', false, String(e).slice(0, 150)); }

  // ===== 4. Smoke: 全局页面 h1（C11 IA） =====
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
  for (const [p, h1] of pages) {
    try {
      const resp = await page.goto(BASE_UI + p, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForTimeout(2000);
      const got = await h1Of();
      check('UI ' + p + ' h1', resp.ok() && got.includes(h1), 'h1=' + got.slice(0, 40));
    } catch (e) { check('UI ' + p, false, String(e).slice(0, 150)); }
  }

  // ======================================================================
  // Optimization V2 Closure A-F（真实浏览器交互）
  // ======================================================================
  const cid = fx.case_id;

  // ---------- 数据前置（fixture 数据面断言） ----------
  try {
    const graph = await api.get(BASE_API + '/cases/' + cid + '/propagation-graph');
    const gbody = graph.ok() ? await graph.json() : null;
    checkC('前置: propagation-graph 数据非空', graph.ok() && gbody && gbody.edges.length >= 1 && gbody.nodes.length >= 2, 'nodes=' + (gbody ? gbody.nodes.length : 'n/a'));
    const posts = await api.get(BASE_API + '/cases/' + cid + '/posts', { params: { limit: 100 } });
    const pbody = posts.ok() ? await posts.json() : null;
    const postCount = pbody && Array.isArray(pbody.posts) ? pbody.posts.length : 0;
    checkC('前置: posts fixture ≥ 51', posts.ok() && postCount >= 51, 'count=' + postCount);
  } catch (e) { checkC('前置 fixture', false, String(e).slice(0, 150)); }

  // ---------- Scenario A: Investigation Shell + Evidence → Copilot Context --
  try {
    await page.goto(BASE_UI + '/investigations/' + cid + '/evidence', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.ecl__claim', { timeout: 15000 });
    checkC('A1: Shell 标题为调查标题', (await h1Of()).includes(fx.case_title), 'h1=' + (await h1Of()).slice(0, 40));
    checkC('A2: Claim 文本真实显示', (await textOf()).includes('E2E 主张：多平台存在协同转发痕迹'), '');

    // 点击 claim 内的关联证据 → Detail 面板 + context chip
    await page.click('.ecl__item');
    await page.waitForFunction(() => document.body.innerText.includes('关联证据摘录'), null, { timeout: 15000 });
    const detailText = await textOf();
    checkC('A3: 点击 Evidence 后 Detail 显示摘录/来源', detailText.includes('关联证据摘录') && detailText.includes('微博') && detailText.includes('E2E账号A'), '');
    const chip1 = await page.evaluate(() => document.querySelector('.copilot__context')?.textContent?.trim() || '');
    checkC('A4: Copilot context chip 含 evidence', chip1.includes('evidence'), 'chip=' + chip1.slice(0, 40));

    // 切换 Unassigned → 点击未归属证据 → Detail + context
    await page.click('button.iev__scope-btn:has-text("Unassigned")');
    await page.waitForSelector('.uev__item', { timeout: 8000 });
    await page.click('.uev__item');
    await page.waitForFunction(() => document.body.innerText.includes('未归属证据摘录'), null, { timeout: 8000 });
    checkC('A5: Unassigned 证据可浏览并进入 Detail', true, '');

    // 发送轻量问题，捕获 /messages 请求 payload 的 ui_context（真实后端请求）
    const msgRequest = page.waitForRequest(
      (req) => req.url().includes('/messages') && req.method() === 'POST',
      { timeout: 15000 },
    );
    await page.fill('.chat-textarea', 'E2E context check');
    await page.click('.send-button');
    const req = await msgRequest;
    const payload = req.postDataJSON();
    const uc = payload && payload.ui_context;
    checkC('A6: 发送请求 ui_context.selected_type=evidence', !!uc && uc.selected_type === 'evidence', JSON.stringify(uc || {}).slice(0, 120));
    checkC('A7: ui_context.selected_id=unassigned_evidence_id', !!uc && uc.selected_id === fx.unassigned_evidence_id, 'expected ' + fx.unassigned_evidence_id);
  } catch (e) { checkC('Scenario A Evidence/Context', false, String(e).slice(0, 200)); }

  // ---------- Scenario B: Finding Review 真闭环 ----------
  try {
    await page.goto(BASE_UI + '/investigations/' + cid + '/findings', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.ifind__card', { timeout: 15000 });
    checkC('B1: candidate Finding 显示', (await textOf()).includes('E2E 结论：首发与转发账号存在协同传播行为'), '');

    await page.click('.ifind__card');
    await page.waitForSelector('.ifind__detail-actions', { timeout: 8000 });
    checkC('B2: 详情显示候选状态', (await textOf()).includes('候选'), '');
    await page.click('.ifind__btn--primary'); // 提交审核
    await page.waitForFunction(() => document.body.innerText.includes('审核中'), null, { timeout: 10000 });
    checkC('B3: UI 提交审核 → under_review', true, '');

    // API negative：普通 status API 不可伪造终审
    const direct = await api.post(BASE_API + '/cases/' + cid + '/findings/' + fx.finding_id + '/status', { data: { status: 'verified' } });
    checkC('B4: 普通 API verified 被拒(422)', direct.status() === 422, 'status ' + direct.status());

    // 创建 Review item，走 Review 工作台 UI 闭环
    const item = await api.post(BASE_API + '/cases/' + cid + '/reviews/items', {
      data: { object_type: 'finding', object_id: fx.finding_id, summary: 'E2E 结论：首发与转发账号存在协同传播行为。' },
    });
    checkC('B5: 创建 Review item', item.ok(), 'status ' + item.status());

    await page.goto(BASE_UI + '/admin/reviews', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.toolbar .filter-select', { timeout: 15000 });
    await page.selectOption('.toolbar .filter-select >> nth=0', { label: fx.case_title });
    await page.waitForSelector('.review-card', { timeout: 15000 });
    await page.click('.review-card .card-main');
    await page.waitForSelector('.decide-box', { timeout: 8000 });
    const claimBtn = page.locator('button:has-text("领取")');
    if (await claimBtn.count()) {
      await claimBtn.first().click();
      await page.waitForFunction(() => document.body.innerText.includes('审核中'), null, { timeout: 8000 });
    }
    await page.click('.decide-box button:has-text("接受")');
    await page.waitForFunction(() => document.body.innerText.includes('已接受'), null, { timeout: 10000 });
    checkC('B6: Review 工作台 claim+approve 完成', true, '');

    await page.goto(BASE_UI + '/investigations/' + cid + '/findings', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.ifind__card', { timeout: 15000 });
    const statusAttr = await page.evaluate(() => {
      const card = [...document.querySelectorAll('.ifind__card')].find((el) => el.textContent.includes('E2E 结论'));
      return card ? card.querySelector('.ifind__status')?.getAttribute('data-status') : null;
    });
    checkC('B7: Finding 显示 verified', statusAttr === 'verified', 'status=' + statusAttr);
  } catch (e) { checkC('Scenario B Finding Review', false, String(e).slice(0, 200)); }

  // ---------- Scenario C: Report Publish Gate 正反两路 ----------
  // seed 顺序保证 invalid 先创建、valid 后创建（updated_at desc → valid 是
  // activeReport）。valid 发布后 load() 会把 selected 切到剩余唯一 draft(invalid)。
  try {
    await page.goto(BASE_UI + '/investigations/' + cid + '/report', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.irep__doc', { timeout: 15000 });
    checkC('C1: 打开合法草稿', (await textOf()).includes('引用关系完整，可以通过发布校验。'), '');

    await page.click('.irep__actions button:has-text("发布")');
    await page.waitForFunction(() => document.body.innerText.includes('已发布'), null, { timeout: 10000 });
    const validAfter = await api.get(BASE_API + '/reports/' + fx.valid_report_id);
    const validBody = validAfter.ok() ? await validAfter.json() : {};
    checkC('C2: valid 发布 → published（API 读取一致）', validBody.status === 'published', 'status=' + validBody.status);

    // 发布后唯一 draft 是 invalid 报告，UI 自动切到它
    await page.waitForFunction(() => document.body.innerText.includes('包含不存在的证据引用'), null, { timeout: 15000 });
    await page.click('.irep__actions button:has-text("发布")');
    await page.waitForFunction(() => document.body.innerText.includes('发布校验失败'), null, { timeout: 10000 });
    checkC('C3: invalid 发布被 UI 拒绝并提示', true, '');
    const invalidAfter = await api.get(BASE_API + '/reports/' + fx.invalid_report_id);
    const invalidBody = invalidAfter.ok() ? await invalidAfter.json() : {};
    checkC('C4: invalid 重新读取仍非 published', invalidBody.status !== 'published', 'status=' + invalidBody.status);
  } catch (e) { checkC('Scenario C Report Gate', false, String(e).slice(0, 200)); }

  // ---------- Scenario D: Propagation Network 三态真实交互 ----------
  try {
    await page.goto(BASE_UI + '/investigations/' + cid + '/network', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.pgraph__canvas', { timeout: 15000 });
    checkC('D1: ECharts canvas 已渲染且 fixture 非空', true, '');

    const selectEdge = () => page.evaluate((edgeId) => {
      const host = document.querySelector('.pgraph__canvas');
      if (!host || typeof host.__e2eSelect !== 'function') {
        throw new Error('E2E hook 不存在：前端需以 VITE_E2E=true 启动');
      }
      host.__e2eSelect('propagation_edge', edgeId);
    }, fx.propagation_edge_id);

    await selectEdge();
    await page.waitForFunction(() => document.body.innerText.includes('传播边详情'), null, { timeout: 8000 });
    let text = await textOf();
    checkC('D2: Detail 显示 relation/confidence', text.includes('copy_spread') && text.includes('82%'), '');
    checkC('D3: 初始为 人工未复核（推断关系）', text.includes('人工未复核（推断关系）'), '');

    await page.click('button:has-text("驳回该关系")');
    await page.waitForFunction(() => document.body.innerText.includes('人工已驳回'), null, { timeout: 10000 });
    checkC('D4: 驳回后显示 人工已驳回', true, '');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.pgraph__canvas', { timeout: 15000 });
    await selectEdge();
    await page.waitForFunction(() => document.body.innerText.includes('传播边详情'), null, { timeout: 8000 });
    checkC('D5: 刷新后仍为 人工已驳回（状态来自数据库）', (await textOf()).includes('人工已驳回'), '');

    await page.click('button:has-text("确认关系成立")');
    await page.waitForFunction(() => document.body.innerText.includes('人工已确认'), null, { timeout: 10000 });
    checkC('D6: 改判确认 → 人工已确认', true, '');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.pgraph__canvas', { timeout: 15000 });
    await selectEdge();
    await page.waitForFunction(() => document.body.innerText.includes('传播边详情'), null, { timeout: 8000 });
    checkC('D7: 再刷新后仍为 人工已确认', (await textOf()).includes('人工已确认'), '');
  } catch (e) { checkC('Scenario D Propagation', false, String(e).slice(0, 200)); }

  // ---------- Scenario E: Live Data Posts UI ----------
  try {
    await page.goto(BASE_UI + '/investigations/' + cid + '/live-data', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.plist__item', { timeout: 15000 });
    let count = await page.locator('.plist__item').count();
    checkC('E1: 默认 Posts tab 首页 50 条（limit=50）', count === 50, 'count=' + count);

    await page.click('button.plist__more');
    await page.waitForFunction(() => document.querySelectorAll('.plist__item').length >= 51, null, { timeout: 10000 });
    count = await page.locator('.plist__item').count();
    checkC('E2: 加载更多 → 51 条（has_more 分页）', count === 51, 'count=' + count);

    await page.fill('.plist__input[type=search]', 'E2E独家');
    await page.click('button.ghost-button:has-text("应用")');
    await page.waitForFunction(() => document.querySelectorAll('.plist__item').length === 1, null, { timeout: 10000 });
    checkC('E3: 关键词过滤只剩目标 post', (await textOf()).includes('E2E独家分析'), '');

    await page.fill('.plist__input[type=search]', '');
    await page.selectOption('.plist__select', 'zhihu');
    await page.waitForFunction(() => document.querySelectorAll('.plist__item').length === 1, null, { timeout: 10000 });
    checkC('E4: 平台过滤 zhihu → 1 条', true, '');

    await page.click('.plist__item');
    const chip = await page.evaluate(() => document.querySelector('.copilot__context')?.textContent?.trim() || '');
    checkC('E5: 点击 post → context chip live_data · social_post', chip.includes('live_data') && chip.includes('social_post'), 'chip=' + chip.slice(0, 40));
  } catch (e) { checkC('Scenario E Live Data', false, String(e).slice(0, 200)); }

  // ---------- Scenario F: Signals 状态机 UI ----------
  try {
    await page.goto(BASE_UI + '/signals', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForSelector('.sigview__card', { timeout: 15000 });
    const cardLocator = page.locator('.sigview__card', { hasText: fx.case_title }).first();
    await cardLocator.click();
    await page.waitForSelector('.sigview__actions', { timeout: 8000 });
    checkC('F1: fixture signal 详情显示 未处理', (await textOf()).includes('未处理'), '');

    await page.click('.sigview__act--primary'); // 确认（acknowledge）
    await page.waitForFunction(() => document.body.innerText.includes('已确认'), null, { timeout: 10000 });
    checkC('F2: UI acknowledge → 已确认', true, '');

    await page.click('button:has-text("解决")');
    await sleep(1500); // act() 后 load() 以当前 filter 重拉（resolved 从默认视图消失）
    await page.selectOption('.sigview__filter', 'resolved');
    await page.waitForSelector('.sigview__card', { timeout: 15000 });
    checkC('F3: UI resolve → 已解决（切换已解决视图可见）', (await textOf()).includes('已解决'), '');

    const illegal = await api.post(BASE_API + '/signals/' + fx.signal_id + ':acknowledge', { data: {} });
    const illegalBody = await illegal.text().catch(() => '');
    checkC('F4: resolved 后 API acknowledge 被拒(400 transition_invalid)', illegal.status() === 400 && illegalBody.includes('alert_status_transition_invalid'), 'status ' + illegal.status() + ' ' + illegalBody.slice(0, 80));

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.sigview__filter', { timeout: 15000 });
    await page.selectOption('.sigview__filter', 'resolved');
    await page.waitForSelector('.sigview__card', { timeout: 15000 });
    checkC('F5: 刷新后仍为 resolved', (await textOf()).includes('已解决'), '');
  } catch (e) { checkC('Scenario F Signals', false, String(e).slice(0, 200)); }

  // ======================================================================
  // Console 卫生检查：Scenario C 的 invalid publish 400 是设计内的负路径
  // （后端 gate 拒绝，浏览器必然记录一条资源错误），与预期能对上的资源错误
  // 不算失败；真正的 JS 错误（pageerror/其他 console error）与任何非预期的
  // 4xx/5xx 响应都算失败。
  // 页面请求经 vite 代理（5173），不能拼 BASE_API 前缀，用路径后缀匹配。
  const expectedBadSuffix = '/cases/' + cid + '/reports/' + fx.invalid_report_id + ':publish';
  const expectedBadPrefixes = [expectedBadSuffix];
  const unexpectedBad = badResponses.filter(
    (line) => !expectedBadPrefixes.some((prefix) => line.includes(prefix)),
  );
  const hardErrors = consoleErrors.filter(
    (line) => !line.startsWith('Failed to load resource'),
  );
  const closureChecks = results.closure.length;
  const closureFailed = results.closure.filter((r) => !r.ok).length;
  check(
    '全程无 JS 错误/非预期失败请求',
    hardErrors.length === 0 && unexpectedBad.length === 0,
    ('unexpected: ' + unexpectedBad.join(' ; ') + ' || js: ' + hardErrors.join(' | ')).slice(0, 400),
  );
  check('Closure A-F 无 skipped', closureChecks > 0 && results.closure.every((r) => !r.name.startsWith('SKIPPED')), 'closure=' + closureChecks);

  await browser.close();
  const summary = {
    smoke: { total: results.smoke.length, passed: results.smoke.filter((r) => r.ok).length, results: results.smoke },
    closure: { total: closureChecks, passed: closureChecks - closureFailed, skipped: 0, results: results.closure },
    harness: {
      total: results.harness.length,
      passed: results.harness.filter((r) => r.ok).length,
      skipped_unrelated: results.harness.filter((r) => r.name.startsWith('SKIPPED')).length,
      results: results.harness,
    },
    console_pageerror_count: hardErrors.length,
    bad_responses: badResponses,
    unexpected_bad_responses: unexpectedBad,
    failures,
  };
  console.log(JSON.stringify(summary, null, 2));
  process.exit(failures.length ? 1 : 0);
})();
