import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/CaseDashboardView.vue'),
    },
    {
      path: '/cases/:caseId',
      name: 'case-workspace',
      component: () => import('@/views/CaseWorkspaceView.vue'),
      props: true,
    },
    {
      path: '/approvals',
      name: 'approval-inbox',
      component: () => import('@/views/ApprovalInboxView.vue'),
    },
    {
      path: '/reviews',
      name: 'review-workbench',
      component: () => import('@/views/ReviewWorkbenchView.vue'),
    },
    {
      path: '/resilience',
      name: 'resilience-console',
      component: () => import('@/views/ResilienceConsoleView.vue'),
    },
    {
      path: '/memories',
      name: 'memory-governance',
      component: () => import('@/views/MemoryGovernanceView.vue'),
    },
    {
      path: '/observability',
      name: 'observability',
      component: () => import('@/views/ObservabilityView.vue'),
    },
    {
      path: '/goals',
      name: 'goal-planning',
      component: () => import('@/views/GoalPlanningView.vue'),
    },
    {
      path: '/subscriptions',
      name: 'subscriptions',
      component: () => import('@/views/SubscriptionsView.vue'),
    },
    {
      path: '/narratives',
      name: 'narrative-timeline',
      component: () => import('@/views/NarrativeTimelineView.vue'),
    },
    {
      path: '/semantics',
      name: 'semantic-annotations',
      component: () => import('@/views/SemanticAnnotationsView.vue'),
    },
    {
      path: '/security',
      name: 'security-events',
      component: () => import('@/views/SecurityEventsView.vue'),
    },
  ],
})
