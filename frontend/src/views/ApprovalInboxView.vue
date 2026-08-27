<script setup lang="ts">
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Clock,
  RefreshCw,
  ShieldAlert,
  X,
  XCircle,
} from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { ApprovalInboxItem, ApprovalStats } from '@/types/api'

const loading = ref(true)
const error = ref('')
const items = ref<ApprovalInboxItem[]>([])
const stats = ref<ApprovalStats | null>(null)
const statusFilter = ref('')
const typeFilter = ref('')
const expandedId = ref<string | null>(null)
const decidingId = ref<string | null>(null)
const decisionError = ref('')
const note = ref('')
const editedArguments = ref('')
const editOpen = ref(false)
const notice = ref('')

const RISK_LABELS: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '关键',
}
const TYPE_LABELS: Record<string, string> = {
  tool_execution: '工具执行',
  budget_increase: '预算增额',
  data_access: '数据访问',
  publish_share_notify: '发布/分享',
  policy_exception: '策略例外',
  high_impact_conclusion: '高影响结论',
}
const STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  approved: '已批准',
  approved_with_edits: '已编辑批准',
  rejected: '已拒绝',
  expired: '已过期',
  cancelled: '已取消',
  consumed: '已消费',
}

const filtered = computed(() => {
  return items.value.filter((item) => {
    if (statusFilter.value && item.status !== statusFilter.value) return false
    if (typeFilter.value && item.approval_type !== typeFilter.value) return false
    return true
  })
})

const pendingCount = computed(() => items.value.filter((i) => i.status === 'pending').length)

function fmt(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, stat] = await Promise.all([
      api.listApprovals({ status: statusFilter.value || undefined, approval_type: typeFilter.value || undefined }),
      api.getApprovalStats(),
    ])
    items.value = list
    stats.value = stat
  } catch (e) {
    error.value = '审批列表加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function decide(approval: ApprovalInboxItem, decision: string) {
  decidingId.value = approval.id
  decisionError.value = ''
  try {
    let edited: Record<string, unknown> | undefined
    if (decision === 'edit_and_approve') {
      if (!editOpen.value || !editedArguments.value.trim()) {
        decisionError.value = '编辑批准需要填写 edited_action.arguments（JSON）'
        return
      }
      try {
        const parsed = JSON.parse(editedArguments.value)
        edited = { tool: approval.action, arguments: parsed }
      } catch {
        decisionError.value = 'arguments 必须是合法 JSON 对象'
        return
      }
    }
    await api.decideApproval(approval.id, {
      decision,
      note: note.value || undefined,
      edited_action: edited,
      actor: 'operator',
    })
    notice.value = '决策已提交（' + decision + '）'
    note.value = ''
    editedArguments.value = ''
    editOpen.value = false
    await load()
  } catch (e) {
    decisionError.value = e instanceof Error ? e.message : String(e)
  } finally {
    decidingId.value = null
  }
}

async function expireOverdue() {
  try {
    const result = await api.expireOverdueApprovals()
    notice.value = '已过期 ' + result.expired + ' 条待处理审批'
    await load()
  } catch (e) {
    decisionError.value = e instanceof Error ? e.message : String(e)
  }
}

function toggle(item: ApprovalInboxItem) {
  expandedId.value = expandedId.value === item.id ? null : item.id
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">审批箱</h1>
        <p class="page-subtitle">统一人工介入与反馈闭环（M21）：敏感工具、预算、发布与策略例外在执行前暂停审批。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="load">
          <RefreshCw :size="15" /> 刷新
        </button>
        <button class="btn ghost" :disabled="loading || pendingCount === 0" @click="expireOverdue">
          <Clock :size="15" /> 清理过期
        </button>
      </div>
    </header>

    <div v-if="stats" class="stat-grid">
      <div class="stat-card">
        <div class="stat-value">{{ stats.total }}</div>
        <div class="stat-label">总计</div>
      </div>
      <div class="stat-card pending">
        <div class="stat-value">{{ stats.approved + stats.approved_with_edits }}</div>
        <div class="stat-label">已批准</div>
      </div>
      <div class="stat-card rejected">
        <div class="stat-value">{{ stats.rejected }}</div>
        <div class="stat-label">已拒绝</div>
      </div>
      <div class="stat-card expired">
        <div class="stat-value">{{ stats.expired }}</div>
        <div class="stat-label">已过期</div>
      </div>
      <div class="stat-card rate">
        <div class="stat-value">{{ (stats.approval_rate * 100).toFixed(1) }}%</div>
        <div class="stat-label">批准率</div>
      </div>
      <div class="stat-card rate">
        <div class="stat-value">{{ (stats.edit_rate * 100).toFixed(1) }}%</div>
        <div class="stat-label">编辑率</div>
      </div>
    </div>

    <div v-if="notice" class="notice">{{ notice }}</div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="filters">
      <select v-model="statusFilter" class="filter-select" @change="load">
        <option value="">全部状态</option>
        <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-model="typeFilter" class="filter-select" @change="load">
        <option value="">全部类型</option>
        <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <span class="filter-count">共 {{ filtered.length }} 条</span>
    </div>

    <div v-if="loading" class="empty-state">加载中…</div>
    <div v-else-if="filtered.length === 0" class="empty-state">没有符合筛选条件的审批。</div>

    <div v-else class="approval-list">
      <article v-for="item in filtered" :key="item.id" class="approval-card" :class="'risk-' + item.risk_level">
        <button class="card-main" @click="toggle(item)">
          <div class="card-top">
            <span class="badge status" :class="item.status">{{ STATUS_LABELS[item.status] || item.status }}</span>
            <span class="badge risk">{{ RISK_LABELS[item.risk_level] || item.risk_level }}风险</span>
            <span class="badge type">{{ TYPE_LABELS[item.approval_type] || item.approval_type }}</span>
            <span class="card-title">{{ item.action }}</span>
            <span class="card-expires">到期：{{ fmt(item.expires_at) }}</span>
          </div>
          <p class="card-reason">{{ item.reason }}</p>
          <p v-if="item.request_summary" class="card-summary">{{ item.request_summary }}</p>
        </button>

        <div v-if="expandedId === item.id" class="card-detail">
          <h4>脱敏预览</h4>
          <pre class="preview">{{ item.redacted_preview || '（无）' }}</pre>
          <p v-if="item.created_at" class="meta">创建于 {{ fmt(item.created_at) }}，所属运行 {{ item.run_id }}</p>
          <p v-if="item.decided_at" class="meta">已由 {{ item.actor || '未知' }} 于 {{ fmt(item.decided_at) }} 决策</p>

          <template v-if="item.status === 'pending'">
            <div v-if="decisionError" class="error-box small">{{ decisionError }}</div>
            <textarea v-model="note" class="note-input" rows="2" placeholder="决策备注（可选）" />
            <div class="decision-actions">
              <button class="btn primary" :disabled="decidingId === item.id" @click="decide(item, 'approve')">
                <Check :size="15" /> 批准
              </button>
              <button class="btn" :disabled="decidingId === item.id" @click="editOpen = !editOpen">
                <ShieldAlert :size="15" /> 编辑后批准
              </button>
              <button class="btn danger" :disabled="decidingId === item.id" @click="decide(item, 'reject')">
                <X :size="15" /> 拒绝
              </button>
              <button class="btn ghost" :disabled="decidingId === item.id" @click="decide(item, 'cancel')">
                <XCircle :size="15" /> 取消
              </button>
            </div>
            <div v-if="editOpen" class="edit-panel">
              <label class="edit-label">edited_action.arguments（JSON，工具：{{ item.action }}）</label>
              <textarea v-model="editedArguments" class="note-input mono" rows="4"
                placeholder='{"platforms": ["weibo"], "limit_per_platform": 50}' />
              <button class="btn primary small" :disabled="decidingId === item.id" @click="decide(item, 'edit_and_approve')">
                提交编辑后批准
              </button>
            </div>
          </template>
          <div v-else class="terminal-note">
            <CheckCircle2 :size="15" /> 终态（{{ STATUS_LABELS[item.status] || item.status }}），只读。
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.page {
  padding: 28px 32px 60px;
  max-width: 1080px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
}
.page-title { font-size: 24px; font-weight: 700; margin: 0 0 4px; }
.page-subtitle { color: var(--text-muted); margin: 0; font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--surface); padding: 7px 14px; font-size: 13px;
  cursor: pointer; color: var(--text);
}
.btn:hover { border-color: var(--border-strong); }
.btn.primary { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.btn.danger { background: var(--red); border-color: var(--red); color: #fff; }
.btn.ghost { background: transparent; }
.btn.small { padding: 5px 10px; font-size: 12px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.stat-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 20px; }
.stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.stat-value { font-size: 22px; font-weight: 700; }
.stat-label { color: var(--text-muted); font-size: 12px; margin-top: 2px; }
.stat-card.pending .stat-value { color: var(--cyan); }
.stat-card.rejected .stat-value { color: var(--red); }
.stat-card.expired .stat-value { color: var(--text-soft); }
.stat-card.rate .stat-value { color: var(--violet); }
.filters { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.filter-select {
  border: 1px solid var(--border); border-radius: 8px; background: var(--surface);
  padding: 7px 10px; font-size: 13px; color: var(--text);
}
.filter-count { color: var(--text-muted); font-size: 13px; }
.approval-list { display: flex; flex-direction: column; gap: 12px; }
.approval-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.approval-card.risk-critical { border-left: 3px solid var(--red); }
.approval-card.risk-high { border-left: 3px solid var(--orange); }
.approval-card.risk-medium { border-left: 3px solid var(--cyan); }
.card-main { display: block; width: 100%; text-align: left; padding: 14px 16px; background: none; border: none; cursor: pointer; }
.card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); }
.badge.status.pending { background: rgba(245, 158, 11, 0.12); color: #b45309; border-color: rgba(245, 158, 11, 0.3); }
.badge.status.approved, .badge.status.approved_with_edits { background: rgba(16, 185, 129, 0.12); color: #047857; border-color: rgba(16, 185, 129, 0.3); }
.badge.status.rejected { background: rgba(239, 68, 68, 0.12); color: #b91c1c; border-color: rgba(239, 68, 68, 0.3); }
.badge.risk { background: var(--surface-strong); }
.card-title { font-weight: 600; font-size: 14px; }
.card-expires { margin-left: auto; color: var(--text-soft); font-size: 12px; }
.card-reason { margin: 8px 0 0; font-size: 13px; color: var(--text); }
.card-summary { margin: 4px 0 0; font-size: 12px; color: var(--text-muted); }
.card-detail { border-top: 1px solid var(--border); padding: 14px 16px; }
.card-detail h4 { margin: 0 0 8px; font-size: 13px; }
.preview {
  background: var(--surface-muted); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 220px; overflow: auto;
}
.meta { color: var(--text-soft); font-size: 12px; margin: 6px 0 0; }
.note-input {
  width: 100%; margin-top: 10px; border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 10px; font-size: 13px; resize: vertical; background: var(--surface);
}
.note-input.mono { font-family: 'DM Mono', ui-monospace, monospace; }
.decision-actions { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.edit-panel { margin-top: 12px; padding: 12px; background: var(--surface-muted); border-radius: 10px; }
.edit-label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.terminal-note { display: flex; align-items: center; gap: 6px; color: var(--text-muted); font-size: 13px; margin-top: 8px; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.error-box.small { margin: 8px 0 0; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
</style>
