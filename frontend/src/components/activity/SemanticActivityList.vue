<script setup lang="ts">
// Optimization V2 (M2.5)：语义化活动列表（默认视图，隐藏底层技术细节）。
import type { SemanticActivity } from '@/services/activityFormatter'

defineProps<{
  activities: SemanticActivity[]
}>()

const statusLabels: Record<SemanticActivity['status'], string> = {
  pending: '等待中',
  running: '进行中',
  success: '完成',
  warning: '需注意',
  error: '失败',
}
</script>

<template>
  <ol class="sacts">
    <li
      v-for="activity in activities"
      :key="activity.id"
      class="sacts__item"
      :data-status="activity.status"
    >
      <span class="sacts__dot" />
      <div class="sacts__body">
        <span class="sacts__title">{{ activity.title }}</span>
        <span v-if="activity.detail" class="sacts__detail">{{ activity.detail }}</span>
      </div>
      <span class="sacts__status">{{ statusLabels[activity.status] }}</span>
    </li>
  </ol>
</template>

<style scoped>
.sacts {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sacts__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 10px;
}

.sacts__item:hover {
  background: var(--surface-strong);
}

.sacts__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-soft);
  flex-shrink: 0;
}

.sacts__item[data-status='running'] .sacts__dot {
  background: var(--accent);
}

.sacts__item[data-status='success'] .sacts__dot {
  background: var(--green);
}

.sacts__item[data-status='warning'] .sacts__dot {
  background: var(--orange);
}

.sacts__item[data-status='error'] .sacts__dot {
  background: var(--red);
}

.sacts__body {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
}

.sacts__title {
  font-size: 13px;
  color: var(--text);
}

.sacts__detail {
  font-size: 12px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sacts__status {
  font-size: 11px;
  color: var(--text-soft);
  flex-shrink: 0;
}
</style>
