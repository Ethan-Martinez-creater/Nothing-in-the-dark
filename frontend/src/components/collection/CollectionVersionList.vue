<script setup lang="ts">
// Optimization V2 (M3.9)：采集定义历史版本列表（最近版本优先）。
import type { CollectionDefinition } from '@/services/api/collections'

defineProps<{ versions: CollectionDefinition[] }>()

const emit = defineEmits<{ (e: 'activate', definition: CollectionDefinition): void }>()

const statusLabels: Record<string, string> = {
  draft: 'DRAFT',
  active: 'ACTIVE',
  superseded: 'SUPERSEDED',
}
</script>

<template>
  <div class="cvers">
    <h3 class="cvers__title">历史版本</h3>
    <ul class="cvers__list">
      <li
        v-for="version in versions"
        :key="version.id"
        class="cvers__item"
        :data-status="version.status"
      >
        <span class="cvers__badge">{{ statusLabels[version.status] ?? version.status }}</span>
        <span class="cvers__version">v{{ version.version }}</span>
        <span class="cvers__goal">{{ version.goal }}</span>
        <button
          v-if="version.status === 'draft'"
          type="button"
          class="cvers__activate"
          @click="emit('activate', version)"
        >
          激活
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.cvers__title {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
}

.cvers__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cvers__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--surface-muted);
  font-size: 12px;
}

.cvers__badge {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-soft);
  font-size: 10px;
  font-weight: 700;
}

.cvers__item[data-status='active'] .cvers__badge {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.cvers__version {
  font-weight: 700;
  color: var(--text);
}

.cvers__goal {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-muted);
}

.cvers__activate {
  padding: 3px 10px;
  border: 1px solid var(--accent);
  border-radius: 6px;
  background: var(--surface);
  color: var(--accent);
  font-size: 11px;
  cursor: pointer;
}
</style>
