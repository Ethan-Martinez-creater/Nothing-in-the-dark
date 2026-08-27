<script setup lang="ts">
import { GitBranch, RefreshCw, Send, TrendingUp } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { CaseRecord, CorrectionEvent, Narrative, NarrativeDetail, NarrativeTimeline } from '@/types/api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const cases = ref<CaseRecord[]>([])
const selectedCaseId = ref('')
const narratives = ref<Narrative[]>([])
const corrections = ref<CorrectionEvent[]>([])
const detail = ref<NarrativeDetail | null>(null)
const timeline = ref<NarrativeTimeline | null>(null)
const expandedId = ref<string | null>(null)
const correctionText = ref('')
const correctionType = ref('clarification')
const busy = ref(false)

const STATUS_LABELS: Record<string, string> = {
  active: '活跃',
  archived: '已归档',
  superseded: '已取代',
}
const REVIEW_LABELS: Record<string, string> = {
  unreviewed: '未审核',
  accepted: '已接受',
  rejected: '已拒绝',
}

const maxVolume = computed(() => {
  const values = (detail.value?.timeline ?? []).map((t) => t.volume)
  return Math.max(...values, 1)
})

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    cases.value = await api.listCases()
  } catch (e) {
    error.value = '案件加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function selectCase() {
  if (!selectedCaseId.value) return
  loading.value = true
  error.value = ''
  try {
    const [narr, corr] = await Promise.all([
      api.listNarratives(selectedCaseId.value),
      api.listCorrections(selectedCaseId.value),
    ])
    narratives.value = narr
    corrections.value = corr
  } catch (e) {
    error.value = '叙事加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function toggleNarrative(narrative: Narrative) {
  expandedId.value = expandedId.value === narrative.id ? null : narrative.id
  detail.value = null
  timeline.value = null
  if (expandedId.value === narrative.id) {
    try {
      detail.value = await api.getNarrative(selectedCaseId.value, narrative.id)
      timeline.value = await api.getNarrativeTimeline(selectedCaseId.value, narrative.id)
    } catch (e) {
      error.value = '叙事详情加载失败：' + (e instanceof Error ? e.message : String(e))
    }
  }
}

async function submitCorrection() {
  if (!correctionText.value.trim()) return
  busy.value = true
  try {
    await api.addCorrection(selectedCaseId.value, {
      content: correctionText.value.trim(),
      correction_type: correctionType.value,
      target_narrative_id: expandedId.value || undefined,
    })
    notice.value = '纠错已提交，等待审核'
    correctionText.value = ''
    const corr = await api.listCorrections(selectedCaseId.value)
    corrections.value = corr
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">叙事生命周期与纠错传播</h1>
        <p class="page-subtitle">M10：叙事时间线、版本、合并拆分后的成员与纠错事件。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="load"><RefreshCw :size="15" /> 刷新</button>
      </div>
    </header>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="notice">{{ notice }}</div>

    <div class="toolbar">
      <select v-model="selectedCaseId" class="filter-select" @change="selectCase">
        <option value="">选择案件…</option>
        <option v-for="c in cases" :key="c.id" :value="c.id">{{ c.title }}</option>
      </select>
      <span class="filter-count">{{ narratives.length }} 个叙事 · {{ corrections.length }} 条纠错</span>
    </div>

    <div v-if="loading" class="empty-state">加载中…</div>
    <div v-else-if="narratives.length === 0 && selectedCaseId" class="empty-state">该案件暂无叙事。</div>

    <div v-else class="narrative-list">
      <article v-for="n in narratives" :key="n.id" class="narrative-card">
        <button class="card-main" @click="toggleNarrative(n)">
          <div class="card-top">
            <GitBranch :size="16" class="n-icon" />
            <span class="badge" :class="n.status">{{ STATUS_LABELS[n.status] || n.status }}</span>
            <span class="badge" :class="n.review_state">{{ REVIEW_LABELS[n.review_state] || n.review_state }}</span>
            <span class="card-title">{{ n.title }}</span>
            <span class="card-date">{{ fmt(n.created_at) }}</span>
          </div>
          <p class="card-summary">{{ n.canonical_summary }}</p>
        </button>

        <div v-if="expandedId === n.id && detail" class="card-detail">
          <section class="sub-panel">
            <h4><TrendingUp :size="14" /> 时间线（{{ detail.timeline.length }} 桶）</h4>
            <div v-if="detail.timeline.length" class="bars">
              <div v-for="(t, i) in detail.timeline" :key="i" class="bar-row">
                <span class="bar-label">{{ t.bucket }} {{ t.platform }}</span>
                <div class="bar-track">
                  <div class="bar-fill" :class="'stage-' + t.stage" :style="{ width: Math.max((t.volume / maxVolume) * 100, 2) + '%' }"></div>
                </div>
                <span class="bar-value">{{ t.volume }}（{{ t.unique_accounts }} 账号）</span>
              </div>
            </div>
            <div v-else class="muted">无时间线数据</div>
          </section>

          <section class="sub-panel">
            <h4>版本（{{ detail.versions.length }}）与成员</h4>
            <div v-for="v in detail.versions" :key="v.id" class="history-row">
              <span class="badge">{{ v.algorithm_version }}</span>
              <span class="history-text">关键词：{{ v.keywords.join(', ') || '—' }}</span>
              <span class="history-meta">{{ fmt(v.created_at) }}</span>
            </div>
            <div class="history-row">
              <span class="history-text">主张 {{ detail.members.claims.length }} 条 · 帖子 {{ detail.members.posts.length }} 条</span>
            </div>
          </section>
        </div>
      </article>
    </div>

    <section v-if="selectedCaseId" class="panel">
      <h3 class="panel-title"><Send :size="15" /> 提交纠错</h3>
      <div class="create-row">
        <select v-model="correctionType" class="filter-select">
          <option value="clarification">澄清</option>
          <option value="correction">更正</option>
          <option value="retraction">撤回</option>
        </select>
        <input v-model="correctionText" class="text-input wide" placeholder="纠错内容…" />
        <button class="btn primary small" :disabled="!correctionText.trim() || busy" @click="submitCorrection">提交</button>
      </div>
    </section>

    <section v-if="selectedCaseId && corrections.length" class="panel">
      <h3 class="panel-title">纠错事件（{{ corrections.length }}）</h3>
      <table class="table">
        <thead><tr><th>类型</th><th>内容</th><th>发布者</th><th>审核</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="c in corrections" :key="c.id">
            <td>{{ c.correction_type }}</td>
            <td class="muted">{{ c.content }}</td>
            <td>{{ c.publisher_class }}</td>
            <td><span class="badge" :class="c.review_state">{{ c.review_state }}</span></td>
            <td class="muted">{{ fmt(c.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.page { padding: 28px 32px 60px; max-width: 1100px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.page-title { font-size: 24px; font-weight: 700; margin: 0 0 4px; }
.page-subtitle { color: var(--text-muted); margin: 0; font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px; cursor: pointer; color: var(--text);
}
.btn.primary { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.btn.ghost { background: transparent; }
.btn.small { padding: 4px 9px; font-size: 12px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; color: var(--text); max-width: 340px; }
.filter-count { color: var(--text-muted); font-size: 13px; }
.narrative-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 16px; }
.narrative-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.card-main { display: block; width: 100%; text-align: left; padding: 14px 16px; background: none; border: none; cursor: pointer; }
.card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.n-icon { color: var(--violet); }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); }
.badge.active, .badge.accepted { background: rgba(16, 185, 129, 0.12); color: #047857; }
.badge.archived, .badge.superseded { background: rgba(100, 116, 139, 0.12); color: #475569; }
.badge.unreviewed { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.badge.rejected { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.card-title { font-weight: 600; font-size: 14px; }
.card-date { margin-left: auto; color: var(--text-soft); font-size: 12px; }
.card-summary { margin: 8px 0 0; font-size: 13px; color: var(--text-muted); }
.card-detail { border-top: 1px solid var(--border); padding: 14px 16px; }
.sub-panel { margin-bottom: 14px; }
.sub-panel h4 { display: flex; align-items: center; gap: 5px; margin: 0 0 8px; font-size: 13px; }
.bars { display: flex; flex-direction: column; gap: 6px; }
.bar-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.bar-label { width: 150px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar-track { flex: 1; background: var(--surface-strong); border-radius: 6px; height: 12px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 6px; background: var(--cyan); }
.bar-fill.stage-early { background: var(--cyan); }
.bar-fill.stage-peak { background: var(--orange); }
.bar-fill.stage-decline { background: var(--violet); }
.bar-value { width: 120px; color: var(--text-soft); }
.history-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
.history-text { flex: 1; }
.history-meta { color: var(--text-soft); font-size: 11px; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; align-items: center; gap: 6px; margin: 0 0 12px; font-size: 14px; font-weight: 600; }
.create-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.text-input { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: 13px; background: var(--surface); color: var(--text); }
.text-input.wide { flex: 1; min-width: 200px; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.muted { color: var(--text-muted); }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
</style>
