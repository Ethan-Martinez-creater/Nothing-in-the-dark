<script setup lang="ts">
// Optimization V2 (M1.2)：全局顶栏。面包屑 + 运行模式徽标。
import { RouterLink } from 'vue-router'

defineProps<{
  caseTitle?: string | null
  caseId?: string | null
  demoMode: boolean
  llmConfigured: boolean
}>()
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

.gtopbar__version {
  color: var(--text-soft);
}
</style>
