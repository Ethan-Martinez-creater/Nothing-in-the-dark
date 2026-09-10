<script setup lang="ts">
// Optimization V2 (M1.2)：全局顶栏。面包屑 + 运行模式徽标 + 管理入口。
// 管理入口在顶部悬停展开菜单（不挤占侧边栏调查列表空间）。
import { ChevronDown } from 'lucide-vue-next'
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

defineProps<{
  caseTitle?: string | null
  caseId?: string | null
  demoMode: boolean
  llmConfigured: boolean
}>()

const route = useRoute()

const adminLinks = [
  { path: '/admin/approvals', label: '审批' },
  { path: '/admin/reviews', label: '审核' },
  { path: '/admin/notifications', label: '通知' },
  { path: '/admin/memories', label: '记忆' },
  { path: '/admin/security', label: '安全' },
  { path: '/admin/observability', label: '可观测' },
  { path: '/admin/resilience', label: '韧性' },
  { path: '/admin/platform-auth', label: '平台账号' },
] as const

const adminActive = computed(() => route.path.startsWith('/admin'))
</script>

<template>
  <header class="gtopbar">
    <div class="gtopbar__breadcrumb">
      <RouterLink to="/" class="gtopbar__home">工作台</RouterLink>
      <template v-if="caseId">
        <span class="gtopbar__sep">/</span>
        <span class="gtopbar__case">{{ caseTitle ?? `调查 ${caseId.slice(0, 8).toUpperCase()}` }}</span>
      </template>
    </div>
    <div class="gtopbar__status">
      <span v-if="demoMode" class="gtopbar__badge">DEMO MODE</span>
      <span v-else class="gtopbar__badge gtopbar__badge--real">REAL CRAWL</span>
      <span v-if="!llmConfigured" class="gtopbar__badge gtopbar__badge--warn">LLM 未配置</span>
      <div class="gtopbar__admin">
        <button
          type="button"
          class="gtopbar__admin-toggle"
          :class="{ 'gtopbar__admin-toggle--active': adminActive }"
          aria-haspopup="true"
          aria-expanded="true"
        >
          <span>管理</span>
          <ChevronDown :size="13" />
        </button>
        <nav class="gtopbar__admin-menu" aria-label="管理">
          <RouterLink
            v-for="link in adminLinks"
            :key="link.path"
            :to="link.path"
            class="gtopbar__admin-link"
            :class="{ 'gtopbar__admin-link--active': route.path === link.path }"
          >
            {{ link.label }}
          </RouterLink>
        </nav>
      </div>
      <span class="gtopbar__version">v0.1.0</span>
    </div>
  </header>
</template>

<style scoped>
.gtopbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 20px;
  height: 48px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.gtopbar__breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 13px;
}

.gtopbar__home {
  color: var(--text-muted);
  text-decoration: none;
}

.gtopbar__home:hover {
  color: var(--accent);
}

.gtopbar__sep {
  color: var(--text-soft);
}

.gtopbar__case {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
  font-weight: 600;
}

.gtopbar__status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-soft);
  flex-shrink: 0;
}

.gtopbar__badge {
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.1);
  color: var(--accent-strong);
  font-weight: 600;
  font-size: 11px;
}

.gtopbar__badge--real {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.gtopbar__badge--warn {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

/* 管理入口：悬停（或键盘聚焦）展开菜单 */
.gtopbar__admin {
  position: relative;
  display: flex;
  align-items: center;
}

.gtopbar__admin-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition:
    border-color 120ms ease,
    color 120ms ease;
}

.gtopbar__admin-toggle:hover,
.gtopbar__admin:hover .gtopbar__admin-toggle,
.gtopbar__admin:focus-within .gtopbar__admin-toggle {
  border-color: var(--accent);
  color: var(--accent-strong);
}

.gtopbar__admin-toggle--active {
  background: rgba(37, 99, 235, 0.1);
  border-color: var(--accent);
  color: var(--accent-strong);
}

.gtopbar__admin-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 60;
  display: none;
  min-width: 148px;
  padding: 6px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

.gtopbar__admin:hover .gtopbar__admin-menu,
.gtopbar__admin:focus-within .gtopbar__admin-menu {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.gtopbar__admin-link {
  display: flex;
  align-items: center;
  padding: 7px 10px;
  border-radius: 7px;
  font-size: 13px;
  color: var(--text-muted);
  text-decoration: none;
  white-space: nowrap;
  transition:
    background 120ms ease,
    color 120ms ease;
}

.gtopbar__admin-link:hover {
  background: var(--surface-strong);
  color: var(--text);
}

.gtopbar__admin-link--active {
  background: rgba(37, 99, 235, 0.1);
  color: var(--accent-strong);
  font-weight: 600;
}

.gtopbar__version {
  color: var(--text-soft);
}
</style>
