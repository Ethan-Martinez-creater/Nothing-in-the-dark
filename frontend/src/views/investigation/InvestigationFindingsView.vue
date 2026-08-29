<script setup lang="ts">
// Optimization V2 (M4.9)：Findings 工作区（左列表 + 右详情）。
// 状态机：candidate→提交审核；verified/rejected 只来自 Review（不提供快捷按钮）。
// 「挑战此结论」（M4.10）复用既有 Debate API，不恢复全 Case 辩论模式。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { RefreshCw, ShieldQuestion } from 'lucide-vue-next'

import { api } from '@/services/api'
import {
  findingApi,
  type Finding,
  type FindingDetail,
  type FindingStatus,
} from '@/services/api/findings'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const findings = ref<Finding[]>([])
const selected = ref<FindingDetail | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const statusFilter = ref<FindingStatus | ''>('')
const syncing = ref(false)
const challenging = ref(false)

const statusLabels: Record<FindingStatus, string> = {
  candidate: '候选',
  under_review: '审核中',
  verified: '已核实',
  rejected: '已否决',
  superseded: '已替代',
}

const kindLabels: Record<string, string> = {
  opinion: '观点',
  verification: '核查',
  propagation: '传播',
  narrative: '叙事',
  integrity: '完整性',
  manual: '人工',
}

const filtered = computed(() =>
  statusFilter.value
    ? findings.value.filter((item) => item.status === statusFilter.value)
    : findings.value,
)

async function load() {
  loading.value = true
  error.value = null
  try {
    findings.value = await findingApi.list(caseId.value)
  } catch {
    error.value = '结论加载失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function open(findingId: string) {
  error.value = null
  try {
    selected.value = await findingApi.get(caseId.value, findingId)
  } catch {
    error.value = '结论详情加载失败。'
  }
}

async function submitForReview(findingId: string) {
  error.value = null
  try {
    await findingApi.updateStatus(caseId.value, findingId, 'under_review')
    await load()
    if (selected.value?.finding.id === findingId) await open(findingId)
  } catch {
    error.value = '提交审核失败。'
  }
}

async function syncHistory() {
  syncing.value = true
  notice.value = null
  try {
    const result = await findingApi.sync(caseId.value)
    notice.value = `历史同步完成：新建 ${result.created}，跳过 ${result.skipped}。`
    await load()
  } catch {
    error.value = '历史同步失败。'
  } finally {
    syncing.value = false
  }
}

// M4.10：挑战此结论 — 创建 Debate 并以结论为第一轮上下文；结果不自动改状态。
async function challenge(finding: Finding) {
  if (challenging.value) return
  challenging.value = true
  error.value = null
  try {
    const debate = await api.createDebate(caseId.value, finding.title)
    await api.addDebateMessage(
      debate.id,
      [
        `请针对 Finding ${finding.id} 进行对抗性审查。`,
        `结论：${finding.statement}`,
        selected.value?.evidence_links.length
          ? `证据引用：${selected.value.evidence_links.map((link) => link.evidence_ref).join('、')}`
          : '当前无证据引用。',
        '重点寻找：1. 过度推断 2. 反例 3. 替代解释。',
      ].join('\n'),
    )
    notice.value = '已创建挑战辩论，可在辩论记录中查看。'
  } catch {
    error.value = '发起挑战失败，请确认该调查已采集数据。'
  } finally {
    challenging.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="ifind">
    <header class="ifind__head">
      <h2 class="ifind__title">调查结论</h2>
      <div class="ifind__actions">
        <select v-model="statusFilter" class="ifind__filter">
          <option value="">全部状态</option>
          <option v-for="(label, key) in statusLabels" :key="key" :value="key">
            {{ label }}
          </option>
        </select>
        <button type="button" class="ifind__btn" :disabled="syncing" @click="syncHistory">
          <RefreshCw :size="14" />
          {{ syncing ? '同步中…' : '同步历史分析' }}
        </button>
      </div>
    </header>

    <p v-if="error" class="ifind__error">{{ error }}</p>
    <p v-if="notice" class="ifind__notice">{{ notice }}</p>

    <div class="ifind__layout">
      <aside class="ifind__list">
        <p v-if="loading" class="ifind__hint">正在加载…</p>
        <p v-else-if="filtered.length === 0" class="ifind__hint">
          尚无结论 — 运行分析后自动产生，或点「同步历史分析」导入既有成果。
        </p>
        <button
          v-for="finding in filtered"
          :key="finding.id"
          type="button"
          class="ifind__card"
          :class="{ 'ifind__card--active': selected?.finding.id === finding.id }"
          @click="open(finding.id)"
        >
          <span class="ifind__card-top">
            <span class="ifind__kind">{{ kindLabels[finding.kind] ?? finding.kind }}</span>
            <span class="ifind__status" :data-status="finding.status">
              {{ statusLabels[finding.status] }}
            </span>
          </span>
          <span class="ifind__statement">{{ finding.statement }}</span>
          <span v-if="finding.confidence !== null" class="ifind__confidence">
            置信度 {{ (finding.confidence * 100).toFixed(0) }}%
          </span>
        </button>
      </aside>

      <section v-if="selected" class="ifind__detail">
        <header class="ifind__detail-head">
          <h3>{{ selected.finding.title }}</h3>
          <span class="ifind__status" :data-status="selected.finding.status">
            {{ statusLabels[selected.finding.status] }}
          </span>
        </header>

        <p class="ifind__statement-full">{{ selected.finding.statement }}</p>

        <div v-if="selected.finding.attributes?.verdict" class="ifind__verdict">
          核查结论：{{ selected.finding.attributes.verdict }}
        </div>

        <section class="ifind__section">
          <h4>证据（{{ selected.evidence_links.length }}）</h4>
          <p v-if="!selected.evidence_links.length" class="ifind__hint">尚无证据引用。</p>
          <ul v-else>
            <li
              v-for="link in selected.evidence_links"
              :key="`${link.evidence_ref}-${link.relation}`"
            >
              <code>{{ link.evidence_ref }}</code>
              <span>{{ link.relation }}</span>
            </li>
          </ul>
        </section>

        <section class="ifind__section">
          <h4>来源</h4>
          <ul>
            <li
              v-for="source in selected.sources"
              :key="`${source.source_type}-${source.source_path}`"
            >
              <code>{{ source.source_type }}</code>
              <span>{{ source.source_id }} / {{ source.source_path }}</span>
            </li>
          </ul>
        </section>

        <section v-if="selected.review" class="ifind__section">
          <h4>人工审核</h4>
          <p>
            状态：{{ selected.review.status }}
            <span v-if="selected.review.summary"> · {{ selected.review.summary }}</span>
          </p>
        </section>

        <div class="ifind__detail-actions">
          <button
            v-if="selected.finding.status === 'candidate'"
            type="button"
            class="ifind__btn ifind__btn--primary"
            @click="submitForReview(selected.finding.id)"
          >
            提交审核
          </button>
          <button
            type="button"
            class="ifind__btn"
            :disabled="challenging"
            @click="challenge(selected.finding)"
          >
            <ShieldQuestion :size="14" />
            {{ challenging ? '发起中…' : '挑战此结论' }}
          </button>
        </div>
      </section>
      <section v-else class="ifind__detail ifind__detail--empty">
        <p class="ifind__hint">从左侧选择一条结论查看详情。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ifind {
  max-width: 1100px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ifind__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.ifind__title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}

.ifind__actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.ifind__filter {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 8px;
  font-size: 12px;
  background: var(--surface);
}

.ifind__btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.ifind__btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.ifind__btn--primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.ifind__error {
  margin: 0;
  color: var(--red);
  font-size: 12px;
}

.ifind__notice {
  margin: 0;
  color: var(--green);
  font-size: 12px;
}

.ifind__layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 14px;
}

@media (max-width: 900px) {
  .ifind__layout {
    grid-template-columns: 1fr;
  }
}

.ifind__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ifind__card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  cursor: pointer;
}

.ifind__card--active {
  border-color: var(--accent);
}

.ifind__card-top {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-size: 11px;
}

.ifind__kind {
  color: var(--text-muted);
}

.ifind__status {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  font-weight: 600;
}

.ifind__status[data-status='verified'] {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.ifind__status[data-status='rejected'] {
  background: rgba(239, 68, 68, 0.1);
  color: var(--red);
}

.ifind__status[data-status='under_review'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.ifind__statement {
  font-size: 13px;
  color: var(--text);
}

.ifind__confidence {
  font-size: 11px;
  color: var(--text-soft);
}

.ifind__detail {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ifind__detail--empty {
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

.ifind__detail-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ifind__detail-head h3 {
  margin: 0;
  font-size: 15px;
}

.ifind__statement-full {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
}

.ifind__verdict {
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(37, 99, 235, 0.06);
  font-size: 12px;
  color: var(--accent-strong);
}

.ifind__section h4 {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--text-muted);
}

.ifind__section ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ifind__section li {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--text-muted);
}

.ifind__detail-actions {
  display: flex;
  gap: 8px;
}

.ifind__hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}
</style>
