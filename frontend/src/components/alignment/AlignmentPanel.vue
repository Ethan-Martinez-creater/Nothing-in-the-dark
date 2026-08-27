<script setup lang="ts">
import { Check, GitCompare, LoaderCircle, RefreshCw, X } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { AlignmentCandidate } from '@/types/api'

const props = defineProps<{
  caseId: string
  open: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const loading = ref(true)
const error = ref('')
const candidates = ref<AlignmentCandidate[]>([])
const analyzing = ref(false)
const actionError = ref('')
const busy = ref<Record<string, boolean>>({})

const DECISION_LABELS: Record<string, string> = {
  pending: '待审',
  confirmed: '已确认',
  probable: '很可能',
  possible: '可能',
  rejected: '已拒绝',
}

const pendingCount = computed(
  () => candidates.value.filter((c) => c.decision === 'pending' || c.decision === 'possible').length,
)

function featureLabel(key: string): string {
  const labels: Record<string, string> = {
    sha256_match: '文件哈希',
    phash_match: '感知哈希',
    text_similarity: '文本相似',
    name_similarity: '名称相似',
    avatar_phash_match: '头像哈希',
    verified_consistent: '认证一致',
  }
  return labels[key] || key
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    candidates.value = await api.listAlignmentCandidates(props.caseId)
  } catch {
    error.value = '加载对齐候选失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function analyze() {
  if (analyzing.value) return
  analyzing.value = true
  actionError.value = ''
  try {
    const { job_id } = await api.analyzeAlignments(props.caseId)
    await waitForJob(job_id)
    await load()
  } catch {
    actionError.value = '分析失败。'
  } finally {
    analyzing.value = false
  }
}

async function waitForJob(jobId: string) {
  for (let i = 0; i < 60; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000))
    const job = await api.getAnalysisJob(props.caseId, jobId)
    if (job.status === 'succeeded') return
    if (job.status === 'failed_terminal' || job.status === 'cancelled') {
      throw new Error(job.error_code ? `分析失败：${job.error_code}` : '分析未完成。')
    }
  }
  throw new Error('分析超时，任务仍可能在后台运行。')
}

async function review(candidate: AlignmentCandidate, action: 'confirm' | 'reject' | 'reopen') {
  if (busy.value[candidate.id]) return
  busy.value = { ...busy.value, [candidate.id]: true }
  actionError.value = ''
  try {
    const updated = await api.reviewAlignmentCandidate(props.caseId, candidate.id, action)
    candidates.value = candidates.value.map((c) => (c.id === candidate.id ? updated : c))
  } catch {
    actionError.value = '提交审核失败。'
  } finally {
    busy.value = { ...busy.value, [candidate.id]: false }
  }
}

onMounted(load)
</script>

<template>
  <aside v-if="open" class="alignment-panel" aria-label="跨平台对齐工作台">
    <header class="panel-header">
      <div class="panel-title">
        <GitCompare :size="16" />
        <span>跨平台对齐</span>
      </div>
      <button type="button" class="icon-button" aria-label="关闭" @click="emit('close')">
        <X :size="16" />
      </button>
    </header>

    <div class="panel-body">
      <div v-if="loading" class="state">
        <LoaderCircle :size="18" class="spin" />
        <span>加载中…</span>
      </div>
      <div v-else-if="error" class="state error">
        <span>{{ error }}</span>
        <button type="button" class="ghost-button" @click="load">重试</button>
      </div>
      <template v-else>
        <div class="toolbar">
          <button type="button" class="ghost-button" :disabled="analyzing" @click="analyze">
            <RefreshCw :size="14" :class="{ spin: analyzing }" />
            分析对齐
          </button>
          <span v-if="pendingCount" class="count">{{ pendingCount }} 待审</span>
        </div>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>

        <ul v-if="candidates.length" class="candidate-list">
          <li v-for="candidate in candidates" :key="candidate.id" class="candidate-item">
            <div class="candidate-head">
              <span class="relation">{{ candidate.left_id }} ⇄ {{ candidate.right_id }}</span>
              <span class="decision-badge" :class="candidate.decision">
                {{ DECISION_LABELS[candidate.decision] || candidate.decision }}
              </span>
            </div>
            <div class="score-bar">
              <span class="score">{{ (candidate.combined_score * 100).toFixed(0) }}%</span>
              <span class="score-label">综合相似度</span>
            </div>
            <ul class="features">
              <li v-for="(value, key) in candidate.feature_scores" :key="key">
                {{ featureLabel(key) }}：
                <strong>{{ typeof value === 'number' ? value.toFixed(2) : value }}</strong>
              </li>
            </ul>
            <div class="candidate-actions">
              <button
                v-if="candidate.decision !== 'confirmed'"
                type="button"
                class="ghost-button"
                :disabled="busy[candidate.id]"
                @click="review(candidate, 'confirm')"
              >
                <Check :size="14" /> 确认
              </button>
              <button
                v-if="candidate.decision !== 'rejected'"
                type="button"
                class="ghost-button"
                :disabled="busy[candidate.id]"
                @click="review(candidate, 'reject')"
              >
                拒绝
              </button>
              <button
                v-if="candidate.decision === 'rejected'"
                type="button"
                class="ghost-button"
                :disabled="busy[candidate.id]"
                @click="review(candidate, 'reopen')"
              >
                重开
              </button>
            </div>
          </li>
        </ul>
        <div v-else class="state">
          <GitCompare :size="18" />
          <span>暂无对齐候选。点击「分析对齐」扫描跨平台内容与账号。</span>
        </div>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.alignment-panel {
  display: flex;
  flex-direction: column;
  width: 340px;
  border-left: 1px solid var(--color-border, #e2e8f0);
  background: var(--color-bg, #fff);
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border, #e2e8f0);
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.icon-button {
  display: inline-flex;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--color-muted, #64748b);
}
.panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
}
.state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px 0;
  color: var(--color-muted, #64748b);
  text-align: center;
}
.state.error {
  color: var(--color-danger, #dc2626);
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.count {
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.action-error {
  margin-bottom: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 13px;
}
.ghost-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid var(--color-border, #e2e8f0);
  background: transparent;
}
.ghost-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.candidate-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.candidate-item {
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 8px;
}
.candidate-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.relation {
  font-size: 13px;
  word-break: break-all;
}
.decision-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: var(--color-muted, #64748b);
  white-space: nowrap;
}
.decision-badge.confirmed {
  background: #dcfce7;
  color: #166534;
}
.decision-badge.probable {
  background: #dbeafe;
  color: #1e40af;
}
.decision-badge.possible {
  background: #fef9c3;
  color: #854d0e;
}
.decision-badge.rejected {
  background: #fee2e2;
  color: #991b1b;
}
.score-bar {
  margin: 8px 0;
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.score {
  font-size: 16px;
  font-weight: 600;
}
.score-label {
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.features {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.features li {
  padding: 2px 0;
}
.candidate-actions {
  display: flex;
  gap: 6px;
}
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
