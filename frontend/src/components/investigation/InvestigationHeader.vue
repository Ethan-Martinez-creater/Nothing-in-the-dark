<script setup lang="ts">
// Optimization V2 (M2.3)：调查头部。标题 / 主题 / 平台 / 状态。
import type { CaseRecord } from '@/types/api'

defineProps<{
  investigation: CaseRecord | null
}>()

const statusLabels: Record<string, string> = {
  draft: '草稿',
  ready: '就绪',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  archived: '已归档',
}
</script>

<template>
  <header class="iheader">
    <div class="iheader__identity">
      <h1 class="iheader__title">
        {{ investigation?.title ?? '加载中…' }}
      </h1>
      <p v-if="investigation" class="iheader__meta">
        <span class="iheader__topic">{{ investigation.topic }}</span>
        <span class="iheader__platforms">{{ investigation.platforms.join(' · ') }}</span>
        <span
          class="iheader__status"
          :data-status="investigation.status"
        >
          {{ statusLabels[investigation.status] ?? investigation.status }}
        </span>
        <span class="iheader__case-id">{{ investigation.id.slice(0, 8).toUpperCase() }}</span>
      </p>
    </div>
    <div class="iheader__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.iheader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px 10px;
  background: var(--surface);
}

.iheader__title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--text);
}

.iheader__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-muted);
  flex-wrap: wrap;
}

.iheader__platforms::before {
  content: '·';
  margin-right: 10px;
  color: var(--text-soft);
}

.iheader__status {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  font-weight: 600;
}

.iheader__status[data-status='running'] {
  background: rgba(37, 99, 235, 0.1);
  color: var(--accent-strong);
}

.iheader__status[data-status='failed'] {
  background: rgba(239, 68, 68, 0.1);
  color: var(--red);
}

.iheader__case-id {
  color: var(--text-soft);
  font-family: monospace;
}
</style>
