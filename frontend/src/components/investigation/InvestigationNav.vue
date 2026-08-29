<script setup lang="ts">
// Optimization V2 (M2.3)：调查内一级导航（8 个工作区 tab）。
// 窄屏横向滚动，不隐藏 tab。
import { RouterLink } from 'vue-router'

defineProps<{ caseId: string }>()

const tabs = [
  { suffix: 'overview', label: '概览' },
  { suffix: 'live-data', label: 'Live Data' },
  { suffix: 'evidence', label: '证据' },
  { suffix: 'network', label: '网络' },
  { suffix: 'timeline', label: '时间线' },
  { suffix: 'findings', label: '结论' },
  { suffix: 'report', label: '报告' },
  { suffix: 'activity', label: '活动' },
] as const
</script>

<template>
  <nav class="inav" aria-label="调查工作区">
    <RouterLink
      v-for="tab in tabs"
      :key="tab.suffix"
      :to="`/investigations/${caseId}/${tab.suffix}`"
      class="inav__tab"
      :class="{ 'inav__tab--active': $route.path.endsWith(`/${tab.suffix}`) }"
    >
      {{ tab.label }}
    </RouterLink>
  </nav>
</template>

<style scoped>
.inav {
  display: flex;
  gap: 2px;
  overflow-x: auto;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  scrollbar-width: thin;
}

.inav__tab {
  padding: 9px 14px;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  text-decoration: none;
  transition:
    color 120ms ease,
    border-color 120ms ease;
}

.inav__tab:hover {
  color: var(--text);
}

.inav__tab--active {
  color: var(--accent-strong);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
</style>
