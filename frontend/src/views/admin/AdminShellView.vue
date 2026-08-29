<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { RouterView } from 'vue-router'

// Optimization V2 (M1.1)：Administration 外壳。六个治理入口从旧顶级路由迁入，
// 页面实现继续复用原有 View 组件（Part VIII 迁移矩阵）。
const route = useRoute()

const tabs = [
  { path: '/admin/approvals', label: '审批' },
  { path: '/admin/reviews', label: '审核' },
  { path: '/admin/notifications', label: '通知' },
  { path: '/admin/memories', label: '记忆' },
  { path: '/admin/security', label: '安全' },
  { path: '/admin/observability', label: '可观测' },
  { path: '/admin/resilience', label: '韧性' },
] as const

const activePath = computed(() => route.path)
</script>

<template>
  <div class="admin-shell">
    <header class="admin-shell__header">
      <div>
        <h1 class="admin-shell__title">管理</h1>
        <p class="admin-shell__subtitle">系统治理与运维控制台</p>
      </div>
      <nav class="admin-shell__tabs" aria-label="管理导航">
        <RouterLink
          v-for="tab in tabs"
          :key="tab.path"
          :to="tab.path"
          class="admin-shell__tab"
          :class="{ 'admin-shell__tab--active': activePath.startsWith(tab.path) }"
        >
          {{ tab.label }}
        </RouterLink>
      </nav>
    </header>
    <div class="admin-shell__body">
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  padding: 20px 24px 32px;
}

.admin-shell__header {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}

.admin-shell__title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
}

.admin-shell__subtitle {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}

.admin-shell__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.admin-shell__tab {
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  transition: all 0.15s ease;
}

.admin-shell__tab:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.admin-shell__tab--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

.admin-shell__body {
  flex: 1;
  min-width: 0;
}
</style>
