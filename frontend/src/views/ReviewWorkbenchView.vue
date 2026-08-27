<script setup lang="ts">
import {
  Check,
  CheckCircle2,
  FolderSearch,
  Gavel,
  MessageSquare,
  RefreshCw,
  Undo2,
  UserCheck,
  X,
} from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { CaseRecord, ReviewQueueItem } from '@/types/api'

const loading = ref(true)
const error = ref('')
const cases = ref<CaseRecord[]>([])
const selectedCaseId = ref('')
const items = ref<ReviewQueueItem[]>([])
const total = ref(0)
const statusFilter = ref('')
const expandedId = ref<string | null>(null)
const actionError = ref('')
const notice = ref('')
const reason = ref('')
const busy = ref(false)

const OBJECT_LABELS: Record<string, string> = {
  evidence: '证据',
  claim: '主张',
  propagation_edge: '传播边',
  alignment_candidate: '对齐候选',
  risk_assessment: '风险评估',
  hypothesis: '假设',
  report_conclusion: '报告结论',
}
const STATUS_LABELS: Record<string, string> = {
  unreviewed: '未审核',
  in_review: '审核中',
  accepted: '已接受',
  rejected: '已拒绝',
  needs_more_evidence: '待补充证据',
  superseded: '已取代',
}
const DECISION_OPTIONS: { value: string; label: string }[] = [
  { value: 'approved', label: '接受' },
  { value: 'rejected', label: '拒绝' },
  { value: 'more_evidence', label: '需要更多证据' },
]

function fmt(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function loadCases() {
  try {
    cases.value = await api.listCases()
    if (cases.value.length > 0 && !selectedCaseId.value) {
      selectedCaseId.value = cases.value[0]?.id ?? ''
      await loadQueue()
    }
  } catch (e) {
    error.value = '案件列表加载失败：' + (e instanceof Error ? e.message : String(e))
  }
}

async function loadQueue() {
  if (!selectedCaseId.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await api.listReviewQueue(
      selectedCaseId.value,
      statusFilter.value || undefined,
    )
    items.value = result.items
    total.value = result.total
  } catch (e) {
    error.value = '审核队列加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function claim(item: ReviewQueueItem) {
  await runAction(() => api.reviewClaimItem(selectedCaseId.value, item.id), '已领取')
}
async function release(item: ReviewQueueItem) {
  await runAction(() => api.reviewReleaseItem(selectedCaseId.value, item.id), '已释放')
}
async function reopen(item: ReviewQueueItem) {
  await runAction(() => api.reviewReopen(selectedCaseId.value, item.id), '已重开')
}
async function decide(item: ReviewQueueItem, decision: string) {
  await runAction(
    () =>
      api.reviewDecide(selectedCaseId.value, item.id, {
        decision,
        reason: reason.value || undefined,
      }),
    '裁决已提交：' + decision,
  )
  reason.value = ''
}

async function runAction(fn: () => Promise<unknown>, okMessage: string) {
  busy.value = true
  actionError.value = ''
  try {
    await fn()
    notice.value = okMessage
    await loadQueue()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

function toggle(item: ReviewQueueItem) {
  expandedId.value = expandedId.value === item.id ? null : item.id
}

onMounted(loadCases)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">分层人工调查与裁决工作台</h1>
        <p class="page-subtitle">M9：领取、评论、差异对比与裁决历史；调查对象裁决与 M21 审批语义分离。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="loadQueue"><RefreshCw :size="15" /> 刷新</button>
      </div>
    </header>

    <div v-if="notice" class="notice">{{ notice }}</div>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="actionError" class="error-box">{{ actionError }}</div>

    <div class="toolbar">
      <select v-model="selectedCaseId" class="filter-select" @change="loadQueue">
        <option value="">选择案件…</option>
        <option v-for="c in cases" :key="c.id" :value="c.id">{{ c.title }}</option>
      </select>
      <select v-model="statusFilter" class="filter-select" @change="loadQueue">
        <option value="">全部状态</option>
        <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <span class="filter-count">共 {{ total }} 条</span>
    </div>

    <div v-if="loading" class="empty-state">加载中…</div>
    <div v-else-if="!selectedCaseId" class="empty-state"><FolderSearch :size="28" /> 请选择一个案件查看审核队列。</div>
    <div v-else-if="items.length === 0" class="empty-state">该案件暂无审核项。</div>

    <div v-else class="review-list">
      <article v-for="item in items" :key="item.id" class="review-card" :class="'risk-' + item.risk_level">
        <button class="card-main" @click="toggle(item)">
          <div class="card-top">
            <span class="badge status" :class="item.status">{{ STATUS_LABELS[item.status] || item.status }}</span>
            <span class="badge">{{ OBJECT_LABELS[item.object_type] || item.object_type }}</span>
            <span class="badge risk">{{ item.risk_level }}</span>
            <span class="card-title">#{{ item.object_id }}</span>
            <span class="card-version">版本 v{{ item.current_version }}</span>
          </div>
          <p class="card-summary">{{ item.summary || '（无摘要）' }}</p>
        </button>

        <div v-if="expandedId === item.id" class="card-detail">
          <div class="detail-actions">
            <button v-if="item.status === 'unreviewed'" class="btn small primary" :disabled="busy" @click="claim(item)">
              <UserCheck :size="14" /> 领取
            </button>
            <button v-if="item.status === 'in_review'" class="btn small" :disabled="busy" @click="release(item)">
              <Undo2 :size="14" /> 释放
            </button>
            <button v-if="item.status === 'accepted' || item.status === 'rejected' || item.status === 'needs_more_evidence'" class="btn small" :disabled="busy" @click="reopen(item)">
              <Undo2 :size="14" /> 重开
            </button>
          </div>

          <div v-if="item.decisions && item.decisions.length" class="history">
            <h4><Gavel :size="14" /> 裁决历史</h4>
            <div v-for="d in item.decisions" :key="d.id" class="history-row">
              <span class="badge">{{ d.decision }}</span>
              <span class="history-text">{{ d.reason || '（无理由）' }}</span>
              <span class="history-meta">{{ d.actor }} · {{ fmt(d.created_at) }}</span>
            </div>
          </div>

          <div v-if="item.comments && item.comments.length" class="history">
            <h4><MessageSquare :size="14" /> 评论</h4>
            <div v-for="c in item.comments" :key="c.id" class="history-row">
              <span class="history-text">{{ c.text }}</span>
              <span class="history-meta">{{ c.actor }} · {{ fmt(c.created_at) }}</span>
            </div>
          </div>

          <div v-if="item.status === 'unreviewed' || item.status === 'in_review'" class="decide-box">
            <input v-model="reason" class="text-input wide" placeholder="裁决理由（可选）" />
            <div class="decide-actions">
              <button v-for="opt in DECISION_OPTIONS" :key="opt.value" class="btn small" :class="{ primary: opt.value === 'approved', danger: opt.value === 'rejected' }" :disabled="busy" @click="decide(item, opt.value)">
                {{ opt.label }}
              </button>
            </div>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 28px 32px 60px; max-width: 1080px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.page-title { font-size: 24px; font-weight: 700; margin: 0 0 4px; }
.page-subtitle { color: var(--text-muted); margin: 0; font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px; cursor: pointer; color: var(--text);
}
.btn.primary { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.btn.danger { background: var(--red); border-color: var(--red); color: #fff; }
.btn.ghost { background: transparent; }
.btn.small { padding: 4px 9px; font-size: 12px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; color: var(--text); }
.filter-count { color: var(--text-muted); font-size: 13px; }
.review-list { display: flex; flex-direction: column; gap: 12px; }
.review-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.review-card.risk-high { border-left: 3px solid var(--orange); }
.review-card.risk-medium { border-left: 3px solid var(--cyan); }
.review-card.risk-low { border-left: 3px solid var(--green); }
.card-main { display: block; width: 100%; text-align: left; padding: 14px 16px; background: none; border: none; cursor: pointer; }
.card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); }
.badge.status.unreviewed { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.badge.status.in_review { background: rgba(37, 99, 235, 0.12); color: #1d4ed8; }
.badge.status.accepted { background: rgba(16, 185, 129, 0.12); color: #047857; }
.badge.status.rejected { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.badge.risk { background: var(--surface-strong); }
.card-title { font-weight: 600; font-size: 14px; }
.card-version { margin-left: auto; color: var(--text-soft); font-size: 12px; }
.card-summary { margin: 8px 0 0; font-size: 13px; color: var(--text-muted); }
.card-detail { border-top: 1px solid var(--border); padding: 14px 16px; }
.detail-actions { display: flex; gap: 8px; margin-bottom: 12px; }
.history { margin-bottom: 12px; }
.history h4 { display: flex; align-items: center; gap: 5px; margin: 0 0 8px; font-size: 13px; }
.history-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.history-text { flex: 1; }
.history-meta { color: var(--text-soft); font-size: 12px; }
.decide-box { display: flex; flex-direction: column; gap: 8px; }
.text-input { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: 13px; background: var(--surface); color: var(--text); }
.text-input.wide { width: 100%; }
.decide-actions { display: flex; gap: 8px; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
</style>
