// COIFESP Agent 前端 E2E 冒烟：断言每页特有标题 + 无控制台错误 + 非零退出码。
// 用法：先运行 scripts/dev-backend.cmd 与 scripts/dev-frontend.cmd，再执行 node e2e-smoke.cjs。
// 依赖：frontend 本地 playwright 包；可用 PLAYWRIGHT_PATH 指向任意 playwright 安装。
const PLAYWRIGHT_PATH = process.env.PLAYWRIGHT_PATH || null;
try {
  var { chromium } = PLAYWRIGHT_PATH ? require(PLAYWRIGHT_PATH) : require('playwright');
} catch (e) {
  console.error('E2E FAIL: playwright 未安装。请运行 npm i -D playwright@1.60.0 或设置 PLAYWRIGHT_PATH 指向 playwright 包目录。', e.message);
  process.exit(2);
}

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:5173';
const routes = [
  // Optimization V2 (C11): 新 IA 冒烟 —— Workspace / Investigations / Signals /
  // Reports / Administration 子页 + 旧路径兼容重定向。
  { path: '/', name: 'workspace-home', expectH1: '工作台' },
  { path: '/investigations', name: 'investigations', expectH1: '调查' },
  { path: '/signals', name: 'signals', expectH1: '信号' },
  { path: '/reports', name: 'reports', expectH1: '报告' },
  { path: '/admin/approvals', name: 'admin-approvals', expectH1: '管理' },
  { path: '/admin/reviews', name: 'admin-reviews', expectH1: '管理' },
  { path: '/admin/notifications', name: 'admin-notifications', expectH1: '管理' },
  { path: '/admin/memories', name: 'admin-memories', expectH1: '管理' },
  { path: '/admin/security', name: 'admin-security', expectH1: '管理' },
  { path: '/admin/observability', name: 'admin-observability', expectH1: '管理' },
  { path: '/admin/resilience', name: 'admin-resilience', expectH1: '管理' },
  { path: '/narratives', name: 'narrative-timeline', expectH1: '叙事生命周期' },
  { path: '/semantics', name: 'legacy-semantics-redirect', expectH1: '调查' },
  { path: '/goals', name: 'legacy-goals-redirect', expectH1: '调查' },
  { path: '/subscriptions', name: 'legacy-subscriptions-redirect', expectH1: '管理' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const results = [];
  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 200));
  });
  page.on('pageerror', (err) => consoleErrors.push('PAGEERROR: ' + String(err).slice(0, 200)));

  for (const r of routes) {
    const rec = { ...r, status: null, h1: '', h1Match: false, errors: [] };
    try {
      const resp = await page.goto(BASE + r.path, { waitUntil: 'domcontentloaded', timeout: 20000 });
      rec.status = resp ? resp.status() : null;
      await page.waitForTimeout(2500);
      await page.waitForSelector('h1', { timeout: 5000 }).catch(() => {});
      rec.h1 = (await page.evaluate(() => document.querySelector('h1')?.textContent?.trim() || '(none)')).slice(0, 60);
      rec.h1Match = rec.h1.includes(r.expectH1);
    } catch (e) {
      rec.errors.push(String(e).slice(0, 250));
    }
    results.push(rec);
  }
  await browser.close();

  // 判定：HTTP 200 + h1 匹配 + 无导航错误 + 全程无 console/pageerror。
  const pageFails = results.filter(r => !(r.status === 200 && r.h1Match && r.errors.length === 0));
  const failed = pageFails.length > 0 || consoleErrors.length > 0;
  const report = {
    total: results.length,
    passed: results.length - pageFails.length,
    failedRoutes: pageFails.map(r => ({ name: r.name, status: r.status, h1: r.h1, h1Match: r.h1Match, errors: r.errors })),
    consoleErrors,
  };
  console.log(JSON.stringify(report, null, 2));
  process.exit(failed ? 1 : 0);
})();