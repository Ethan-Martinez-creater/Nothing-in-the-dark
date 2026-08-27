<script setup lang="ts">
import {
  AlertTriangle,
  Check,
  Eye,
  FileClock,
  GitBranch,
  History,
  RefreshCw,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { MemoryAccessEvent, MemoryConflict, MemoryMutationEntry, MemoryRecord } from '@/types/api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const memories = ref<MemoryRecord[]>([])
const searchQuery = ref('')
const statusFilter = ref('')
const typeFilter = ref('')
const expandedId = ref<string | null>(null)
const busy = ref(false)

const detail = ref<{
  history: MemoryMutationEntry[]
  accesses: MemoryAccessEvent[]
  conflicts: MemoryConflict[]
} | null>(null)
const detailLoading = ref(false)

const TYPE_LABELS: Record<string, string> = {
  conversation_summary: '对话摘要',
  case_fact: '案件事实',
  case_hypothesis: '案件假设',
  operator_preference: '操作者偏好',
  procedural: '程序规则',
  external_excerpt: '外部摘录',
}
const STATUS_LABELS: Record<string, string> = {
  active: '生效',
  pending_review: '待审核',
  superseded: '已取代',
  expired: '已过期',
  disabled: '已停用',
  deleted: '已删除',
}
const TRUST_LABELS: Record<string, string> = {
  verified_fact: '已验证事实',
  operator_input: '操作者输入',
  generated_content: '生成内容',
  external_content: '外部内容',
  llm_inference: '模型推断',
}
const SENSITIVITY_LABELS: Record<string, string> = {
  low: '低敏感',
  medium: '中敏感',
  high: '高敏感',
  secret: '秘密',
}

const filtered = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return memories.value.filter((m) => {
    if (statusFilter.value && (m.status || '') !== statusFilter.value) return false
    if (typeFilter.value && m.memory_type !== typeFilter.value) return false
    if (q && !m.content.toLowerCase().includes(q) && !(m.source_id || '').toLowerCase().includes(q)) return false
    return true
  })
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
    memories.value = await api.listMemories({
      status: statusFilter.value || undefined,
      memory_type: typeFilter.value || undefined,
      limit: 200,
    })
  } catch (e) {
    error.value = '记忆列表加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function toggle(memory: MemoryRecord) {
  expandedId.value = expandedId.value === memory.id ? null : memory.id
  if (expandedId.value === memory.id) {
    detailLoading.value = true
    detail.value = null
    try {
      const [history, accesses, conflicts] = await Promise.all([
        api.getMemoryHistory(memory.id),
        api.getMemoryAccesses(memory.id),
        api.getMemoryConflicts(memory.id),
      ])
      detail.value = { history, accesses, conflicts }
    } catch (e) {
      error.value = '记忆详情加载失败：' + (e instanceof Error ? e.message : String(e))
    } finally {
      detailLoading.value = false
    }
  }
}

async function act(memory: MemoryRecord, action: 'correct' | 'disable' | 'restore' | 'delete' | 'review', extra?: { content?: string; accept?: boolean }) {
  busy.value = true
  error.value = ''
  try {
    if (action === 'correct') {
      await api.correctMemory(memory.id, {
        content: extra?.content || memory.content,
        actor: 'operator',
        reason: 'operator correction',
      })
    } else if (action === 'disable') {
      await api.disableMemory(memory.id, { actor: 'operator', reason: 'operator action' })
    } else if (action === 'restore') {
      await api.restoreMemory(memory.id, { actor: 'operator', reason: 'operator action' })
    } else if (action === 'delete') {
      await api.deleteMemory(memory.id, { actor: 'operator', reason: 'operator action' })
    } else if (action === 'review') {
      await api.reviewMemory(memory.id, { accept: extra?.accept ?? true, actor: 'operator', reason: 'operator review' })
    }
    notice.value = '操作成功：' + action
    await load()
    if (expandedId.value === memory.id) await toggle(memory)
  } catch (e) {
    error.value = '操作失败：' + (e instanceof Error ? e.message : String(e))
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
        <h1 class="page-title">记忆安全与用户可控治理</h1>
        <p class="page-subtitle">M23：查看、修正、停用、删除、审核记忆；冲突与访问审计不静默覆盖。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="load"><RefreshCw :size="15" /> 刷新</button>
      </div>
    </header>

    <div v-if="notice" class="notice">{{ notice }}</div>
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="toolbar">
      <input v-model="searchQuery" class="text-input wide" placeholder="搜索记忆内容 / 来源 ID…" />
      <select v-model="statusFilter" class="filter-select" @change="load">
        <option value="">全部状态</option>
        <option v-for="(label, key) in STATUS_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <select v-model="typeFilter" class="filter-select" @change="load">
        <option value="">全部类型</option>
        <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
      </select>
      <span class="filter-count">{{ filtered.length }} 条</span>
    </div>

    <div v-if="loading" class="empty-state">加载中…</div>
    <div v-else-if="filtered.length === 0" class="empty-state">没有符合条件的记忆。</div>

    <div v-else class="memory-list">
      <article v-for="memory in filtered" :key="memory.id" class="memory-card" :class="'status-' + (memory.status || 'active')">
        <button class="card-main" @click="toggle(memory)">
          <div class="card-top">
            <span class="badge status" :class="memory.status || 'active'">{{ STATUS_LABELS[memory.status || 'active'] || memory.status }}</span>
            <span class="badge type">{{ TYPE_LABELS[memory.memory_type || ''] || memory.memory_type || '—' }}</span>
            <span v-if="memory.trust_level" class="badge trust">{{ TRUST_LABELS[memory.trust_level] || memory.trust_level }}</span>
            <span class="card-title">{{ memory.content.slice(0, 80) }}{{ memory.content.length > 80 ? '…' : '' }}</span>
            <span class="card-meta">v{{ memory.version }} · {{ memory.sensitivity ? (SENSITIVITY_LABELS[memory.sensitivity] || memory.sensitivity) : '' }} · {{ memory.source_type }}</span>
          </div>
        </button>

        <div v-if="expandedId === memory.id" class="card-detail">
          <div v-if="detailLoading" class="muted">加载详情…</div>
          <template v-else-if="detail">
            <p class="full-content">{{ memory.content }}</p>
            <div class="meta-grid">
              <div><span class="m-label">信任等级</span>{{ TRUST_LABELS[memory.trust_level || ''] || memory.trust_level || '—' }}</div>
              <div><span class="m-label">审核状态</span>{{ memory.review_state || '—' }}</div>
              <div><span class="m-label">置信水平</span>{{ memory.confidence_level || '—' }}（{{ memory.confidence }}）</div>
              <div><span class="m-label">敏感度</span>{{ SENSITIVITY_LABELS[memory.sensitivity || ''] || memory.sensitivity || '—' }}</div>
              <div><span class="m-label">来源</span>{{ memory.source_type }} / {{ memory.source_id }}</div>
              <div><span class="m-label">索引</span>{{ memory.index_status }}（{{ memory.embedding_version || '—' }}）</div>
              <div><span class="m-label">内容哈希</span><span class="mono">{{ memory.content_hash?.slice(0, 16) }}…</span></div>
              <div><span class="m-label">有效期</span>{{ fmt(memory.valid_from) }} → {{ fmt(memory.expires_at) }}</div>
            </div>

            <div class="ops">
              <button class="btn small" :disabled="busy" @click="act(memory, 'review', { accept: true })"><Check :size="14" /> 审核通过</button>
              <button class="btn small danger" :disabled="busy" @click="act(memory, 'review', { accept: false })"><X :size="14" /> 审核拒绝</button>
              <button v-if="memory.status === 'active'" class="btn small" :disabled="busy" @click="act(memory, 'disable')"><X :size="14" /> 停用</button>
              <button v-if="memory.status === 'disabled'" class="btn small" :disabled="busy" @click="act(memory, 'restore')"><Check :size="14" /> 恢复</button>
              <button class="btn small danger" :disabled="busy" @click="act(memory, 'delete')"><Trash2 :size="14" /> 删除</button>
            </div>

            <div class="tabs-inline">
              <h4><History :size="14" /> 变更历史（{{ detail.history.length }}）</h4>
              <div v-for="m in detail.history" :key="m.id" class="history-row">
                <span class="badge">{{ m.action }}</span>
                <span class="history-text">{{ m.from_status || '—' }} → {{ m.to_status || '—' }} v{{ m.version_before }}→v{{ m.version_after }} · {{ m.actor }}</span>
                <span class="history-meta">{{ fmt(m.created_at) }}</span>
              </div>
              <div v-if="detail.history.length === 0" class="muted">无变更记录</div>
            </div>

            <div class="tabs-inline">
              <h4><Eye :size="14" /> 访问审计（{{ detail.accesses.length }}）</h4>
              <div v-for="a in detail.accesses" :key="a.id" class="history-row">
                <span class="history-text">{{ a.purpose }}（命中 {{ a.result_count }}）</span>
                <span class="history-meta">{{ a.run_id || '—' }} · {{ fmt(a.created_at) }}</span>
              </div>
              <div v-if="detail.accesses.length === 0" class="muted">无访问记录</div>
            </div>

            <div class="tabs-inline">
              <h4><GitBranch :size="14" /> 冲突（{{ detail.conflicts.length }}）</h4>
              <div v-for="c in detail.conflicts" :key="c.id" class="history-row">
                <AlertTriangle :size="14" class="warn-icon" />
                <span class="history-text">冲突记忆 {{ c.conflicting_memory_id.slice(0, 8) }}… · {{ c.resolved ? '已解决' : '未解决' }}</span>
                <span class="history-meta">{{ c.resolution || '' }}</span>
              </div>
              <div v-if="detail.conflicts.length === 0" class="muted">无冲突记录</div>
            </div>
          </template>
        </div>
      </article>
    </div>
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
.btn.danger { background: var(--red); border-color: var(--red); color: #fff; }
.btn.ghost { background: transparent; }
.btn.small { padding: 4px 9px; font-size: 12px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
.text-input { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: 13px; background: var(--surface); color: var(--text); }
.text-input.wide { flex: 1; min-width: 200px; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; color: var(--text); }
.filter-count { color: var(--text-muted); font-size: 13px; }
.memory-list { display: flex; flex-direction: column; gap: 10px; }
.memory-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.memory-card.status-disabled, .memory-card.status-deleted, .memory-card.status-expired { opacity: 0.72; }
.memory-card.status-pending_review { border-left: 3px solid var(--orange); }
.memory-card.status-active { border-left: 3px solid var(--green); }
.card-main { display: block; width: 100%; text-align: left; padding: 13px 16px; background: none; border: none; cursor: pointer; }
.card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); white-space: nowrap; }
.badge.status.active { background: rgba(16, 185, 129, 0.12); color: #047857; }
.badge.status.pending_review { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.badge.status.disabled, .badge.status.deleted, .badge.status.expired { background: rgba(100, 116, 139, 0.12); color: #475569; }
.badge.type { background: var(--surface-strong); }
.badge.trust { background: rgba(124, 108, 246, 0.1); color: #6d28d9; }
.card-title { font-weight: 500; font-size: 13px; }
.card-meta { margin-left: auto; color: var(--text-soft); font-size: 12px; }
.card-detail { border-top: 1px solid var(--border); padding: 14px 16px; }
.full-content { font-size: 13px; color: var(--text); background: var(--surface-muted); border-radius: 8px; padding: 10px; white-space: pre-wrap; word-break: break-all; }
.meta-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 12px 0; font-size: 12px; }
.m-label { display: block; color: var(--text-soft); font-size: 11px; margin-bottom: 2px; }
.mono { font-family: ui-monospace, monospace; }
.ops { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.tabs-inline { margin-bottom: 12px; }
.tabs-inline h4 { display: flex; align-items: center; gap: 5px; margin: 0 0 6px; font-size: 13px; }
.history-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
.history-text { flex: 1; }
.history-meta { color: var(--text-soft); font-size: 11px; }
.warn-icon { color: var(--orange); flex-shrink: 0; }
.muted { color: var(--text-soft); font-size: 13px; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
</style>
