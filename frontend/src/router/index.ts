import { createRouter, createWebHistory } from 'vue-router'

// Optimization V2 (M1.1)：Investigation-centric 路由骨架。
// - 新一级路由：Home / Investigations / Signals / Reports / Administration
// - legacy 路由全部 redirect 保留，不删除（E-07：新行为接管后才允许删除）
// - /investigations/:caseId/overview 过渡期直接复用 CaseWorkspaceView（M2 拆分）
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/investigations',
      name: 'investigations',
      component: () => import('@/views/InvestigationsView.vue'),
    },
    {
      path: '/investigations/:caseId',
      component: () => import('@/views/investigation/InvestigationShellView.vue'),
      children: [
        {
          path: '',
          redirect: (to) => `/investigations/${String(to.params.caseId)}/overview`,
        },
        {
          path: 'overview',
          name: 'investigation-overview',
          // M3.9：Overview 正式页面（Scope/Collection + 状态）；旧工作台
          // CaseWorkspaceView 保留至 M8 删除（Part VIII 迁移矩阵）。
          component: () => import('@/views/investigation/InvestigationOverviewView.vue'),
        },
        {
          path: 'live-data',
          name: 'investigation-live-data',
          component: () => import('@/views/investigation/InvestigationLiveDataView.vue'),
        },
        {
          path: 'evidence',
          name: 'investigation-evidence',
          component: () => import('@/views/investigation/InvestigationEvidenceView.vue'),
        },
        {
          path: 'network',
          name: 'investigation-network',
          component: () => import('@/views/investigation/InvestigationNetworkView.vue'),
        },
        {
          path: 'timeline',
          name: 'investigation-timeline',
          component: () => import('@/views/investigation/InvestigationTimelineView.vue'),
        },
        {
          path: 'findings',
          name: 'investigation-findings',
          component: () => import('@/views/investigation/InvestigationFindingsView.vue'),
        },
        {
          path: 'report',
          name: 'investigation-report',
          component: () => import('@/views/investigation/InvestigationReportView.vue'),
        },
        {
          path: 'activity',
          name: 'investigation-activity',
          component: () => import('@/views/investigation/InvestigationActivityView.vue'),
        },
      ],
    },
    {
      path: '/signals',
      name: 'signals',
      component: () => import('@/views/SignalsView.vue'),
    },
    {
      path: '/reports',
      name: 'reports',
      component: () => import('@/views/ReportsView.vue'),
    },
    {
      path: '/admin',
      redirect: '/admin/approvals',
    },
    {
      path: '/admin',
      component: () => import('@/views/admin/AdminShellView.vue'),
      children: [
        {
          path: 'approvals',
          name: 'admin-approvals',
          component: () => import('@/views/ApprovalInboxView.vue'),
        },
        {
          path: 'reviews',
          name: 'admin-reviews',
          component: () => import('@/views/ReviewWorkbenchView.vue'),
        },
        {
          path: 'memories',
          name: 'admin-memories',
          component: () => import('@/views/MemoryGovernanceView.vue'),
        },
        {
          path: 'security',
          name: 'admin-security',
          component: () => import('@/views/SecurityEventsView.vue'),
        },
        {
          path: 'observability',
          name: 'admin-observability',
          component: () => import('@/views/ObservabilityView.vue'),
        },
        {
          path: 'resilience',
          name: 'admin-resilience',
          component: () => import('@/views/ResilienceConsoleView.vue'),
        },
        {
          // C9.3: 订阅/端点/投递记录迁入 Administration → Notifications
          path: 'notifications',
          name: 'admin-notifications',
          component: () => import('@/views/admin/AdministrationNotificationsView.vue'),
        },
      ],
    },
    // ---- Legacy redirects（保留兼容，不删除旧路径）----
    {
      path: '/cases/:caseId',
      redirect: (to) => ({
        path: `/investigations/${String(to.params.caseId)}/overview`,
        query: to.query,
      }),
    },
    {
      path: '/cases',
      redirect: '/investigations',
    },
    {
      path: '/approvals',
      redirect: '/admin/approvals',
    },
    {
      path: '/reviews',
      redirect: '/admin/reviews',
    },
    {
      path: '/memories',
      redirect: '/admin/memories',
    },
    {
      path: '/security',
      redirect: '/admin/security',
    },
    {
      path: '/observability',
      redirect: '/admin/observability',
    },
    {
      path: '/resilience',
      redirect: '/admin/resilience',
    },
    // ---- 旧独立页面路由（C9 分流完成：semantics→Evidence/Semantics 子 tab、
    // goals→Overview Plan、subscriptions→Administration/Notifications；
    // share → Reports 卡片）。旧路径保留兼容重定向。----
    {
      path: '/goals',
      redirect: '/investigations',
    },
    {
      path: '/subscriptions',
      redirect: '/admin/notifications',
    },
    {
      path: '/narratives',
      name: 'narrative-timeline',
      component: () => import('@/views/NarrativeTimelineView.vue'),
    },
    {
      path: '/semantics',
      redirect: '/investigations',
    },
    {
      path: '/dashboard',
      redirect: '/',
    },
  ],
})
