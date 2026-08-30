<script setup lang="ts">
// FC3: 未归属证据（unassigned evidence）列表内容组件。
// 展示 summary.unassigned 的真实数据（stance / excerpt / source_type /
// platform / author / relevance），点击上抛选择（不伪造 Claim 或来源标题）。
import type { EvidenceItem } from '@/types/api'

defineProps<{ items: EvidenceItem[] }>()

const emit = defineEmits<{ select: [item: EvidenceItem] }>()

const STANCE_LABELS: Record<string, string> = {
  support: '支持',
  oppose: '反驳',
  context: '背景',
}

const PLATFORM_LABELS: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

function stanceLabel(stance: string): string {
  return STANCE_LABELS[stance] || stance
}

// platform / author 来自采集时写入的 metadata（有则显示，无则不伪造）
function sourceLabel(item: EvidenceItem): string {
  const meta = (item.metadata_json ?? {}) as Record<string, unknown>
  const platform = String(meta.platform || '')
  const platformLabel = PLATFORM_LABELS[platform] || platform
  const author = String(meta.author || '')
  if (platformLabel && author) return `${platformLabel} · ${author}`
  if (platformLabel) return platformLabel
  if (author) return author
  return item.source_type
}
</script>

<template>
  <div class="uev">
    <ul v-if="items.length" class="uev__list">
      <li
        v-for="item in items"
        :key="item.id"
        class="uev__item"
        @click="emit('select', item)"
      >
        <div class="uev__head">
          <span class="uev__stance" :class="`stance-${item.stance}`">
            {{ stanceLabel(item.stance) }}
          </span>
          <span class="uev__source">{{ sourceLabel(item) }}</span>
        </div>
        <p class="uev__excerpt">{{ item.excerpt }}</p>
        <div class="uev__meta">
          <span class="uev__type">{{ item.source_type }}</span>
          <em class="uev__relevance">相关度 {{ item.relevance.toFixed(2) }}</em>
        </div>
      </li>
    </ul>
    <p v-else class="uev__empty">暂无未归属证据；可切回 Claims 查看主张分组。</p>
  </div>
</template>

<style scoped>
.uev__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.uev__item {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  cursor: pointer;
}

.uev__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.uev__stance {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--text-muted);
}

.uev__source {
  font-size: 11px;
  color: var(--text-soft);
}

.uev__excerpt {
  margin: 6px 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
}

.uev__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.uev__type {
  font-size: 11px;
  color: var(--text-soft);
  font-family: ui-monospace, monospace;
}

.uev__relevance {
  font-size: 11px;
  color: var(--text-soft);
}

.uev__empty {
  margin: 0;
  color: var(--text-soft);
  font-size: 12px;
}
</style>
