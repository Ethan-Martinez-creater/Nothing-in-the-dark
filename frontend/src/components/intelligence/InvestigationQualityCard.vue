<script setup lang="ts">
// V3 §6/§43：Investigation Quality Card（Overview 左上）。
// 展示 6 维度得分 + 总分/等级 + Top gaps + computed_at + disclaimer。
import { computed } from 'vue'
import { RefreshCw } from 'lucide-vue-next'

import type { InvestigationQuality } from '@/services/api/intelligence'

const props = defineProps<{
  quality: InvestigationQuality | null
  loading: boolean
  error: string
  refreshing: boolean
}>()

const emit = defineEmits<{ (e: 'refresh'): void }>()

const GRADE_LABELS: Record<string, string> = {
  strong: '强',
  acceptable: '可接受',
  needs_attention: '需关注',
  weak: '弱',
  insufficient_data: '数据不足',
}

const gradeLabel = computed(() =>
  props.quality ? (GRADE_LABELS[props.quality.grade] ?? props.quality.grade) : '',
)

const topGaps = computed(() => {
  if (!props.quality) return []
  const rank: Record<string, number> = { critical: 0, warning: 1, info: 2 }
  return [...props.quality.gaps]
    .sort((a, b) => (rank[a.severity] ?? 9) - (rank[b.severity] ?? 9))
    .slice(0, 5)
})

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN')
}

function scoreWidth(score: number | null): string {
  return `${Math.max(4, Math.min(100, Math.round((score ?? 0) * 100)))}%`
}
</script>

<template>
  <section class="iqcard" aria-label="调查质量">
    <div class="iqcard__head">
      <div class="iqcard__head-copy">
        <h3 class="iqcard__title">调查质量</h3>
        <span class="iqcard__version">算法 {{ quality?.algorithm_version ?? '' }}</span>
      </div>
      <button
        type="button"
        class="iqcard__refresh"
        :disabled="refreshing"
        aria-label="重新评估质量"
        @click="emit('refresh')"
      >
        <RefreshCw :size="14" />
        {{ refreshing ? '评估中…' : '重新评估' }}
      </button>
    </div>

    <p v-if="loading" class="iqcard__hint">正在评估…</p>
    <p v-else-if="error" class="iqcard__error">{{ error }}</p>

    <template v-else-if="quality">
      <div class="iqcard__score">
        <span class="iqcard__score-value">{{ quality.overall_score?.toFixed(1) ?? '—' }}</span>
        <span class="iqcard__score-grade" :data-grade="quality.grade">{{ gradeLabel }}</span>
      </div>

      <ul class="iqcard__dims">
        <li v-for="dim in quality.dimensions" :key="dim.key" class="iqcard__dim">
          <span class="iqcard__dim-label">{{ dim.label }}</span>
          <span class="iqcard__dim-bar">
            <span
              class="iqcard__dim-fill"
              :class="{ 'iqcard__dim-fill--unavailable': !dim.available }"
              :style="{ width: scoreWidth(dim.score) }"
            ></span>
          </span>
          <span class="iqcard__dim-score">{{ dim.score?.toFixed(1) ?? '—' }}</span>
        </li>
      </ul>

      <div v-if="topGaps.length" class="iqcard__gaps">
        <h4 class="iqcard__gaps-title">需要处理</h4>
        <ul class="iqcard__gap-list">
          <li v-for="gap in topGaps" :key="`${gap.code}-${gap.object_id ?? ''}`">
            <span class="iqcard__gap-sev" :data-severity="gap.severity">{{ gap.severity }}</span>
            <span class="iqcard__gap-msg">{{ gap.message }}</span>
          </li>
        </ul>
      </div>

      <p class="iqcard__meta">
        评估于 {{ formatTime(quality.computed_at) }} · {{ quality.input_fingerprint.slice(0, 10) }}…
      </p>
      <p class="iqcard__disclaimer">{{ quality.disclaimer }}</p>
    </template>
  </section>
</template>

<style scoped>
.iqcard {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.iqcard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.iqcard__head-copy {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.iqcard__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.iqcard__version {
  font-size: 11px;
  color: var(--text-soft);
}

.iqcard__refresh {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
}

.iqcard__refresh:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.iqcard__refresh:disabled {
  opacity: 0.6;
  cursor: default;
}

.iqcard__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.iqcard__error {
  margin: 0;
  color: var(--red);
  font-size: 13px;
}

.iqcard__score {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.iqcard__score-value {
  font-size: 28px;
  font-weight: 700;
}

.iqcard__score-grade {
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 700;
}

.iqcard__score-grade[data-grade='strong'] {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.iqcard__score-grade[data-grade='acceptable'] {
  background: rgba(37, 99, 235, 0.1);
  color: var(--accent-strong);
}

.iqcard__score-grade[data-grade='needs_attention'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.iqcard__score-grade[data-grade='weak'],
.iqcard__score-grade[data-grade='insufficient_data'] {
  background: rgba(239, 68, 68, 0.12);
  color: var(--red);
}

.iqcard__dims {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.iqcard__dim {
  display: grid;
  grid-template-columns: 110px 1fr 42px;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.iqcard__dim-label {
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.iqcard__dim-bar {
  height: 7px;
  border-radius: 999px;
  background: var(--surface-strong);
  overflow: hidden;
}

.iqcard__dim-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
}

.iqcard__dim-fill--unavailable {
  background: var(--border);
}

.iqcard__dim-score {
  text-align: right;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.iqcard__gaps {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}

.iqcard__gaps-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-soft);
}

.iqcard__gap-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.iqcard__gap-list li {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12px;
}

.iqcard__gap-sev {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
}

.iqcard__gap-sev[data-severity='critical'] {
  background: rgba(239, 68, 68, 0.12);
  color: var(--red);
}

.iqcard__gap-sev[data-severity='warning'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.iqcard__gap-msg {
  color: var(--text);
  line-height: 1.45;
}

.iqcard__meta {
  margin: 0;
  font-size: 11px;
  color: var(--text-soft);
}

.iqcard__disclaimer {
  margin: 0;
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
}
</style>