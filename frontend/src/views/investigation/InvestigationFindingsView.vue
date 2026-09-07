<script setup lang="ts">
// Optimization V2 (M4.9)：Findings 工作区（左列表 + 右详情）。
// 状态机：candidate→提交审核；verified/rejected 只来自 Review（不提供快捷按钮）。
// 「挑战此结论」（M5.5）改为 createFindingDebate → FindingDebateModal；
// Finding 上下文由后端快照固化，前端不再自然语言拼接。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { Gavel, RefreshCw } from 'lucide-vue-next'

import { api } from '@/services/api'
import FindingDebateModal from '@/components/debate/FindingDebateModal.vue'
import type { Debate } from '@/types/api'
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

// 对抗性审查（M5.4）：该 Finding 的 challenge 历史 / 三态展示 / Modal。
const challengeList = ref<Debate[]>([])
const challengeLoading = ref(false)
const challengeModalOpen = ref(false)
const activeChallengeId = ref<string | null>(null)
const challengeSummary = ref('')

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

const activeChallenge = computed(
  () =>
    challengeList.value.find((item) => item.status === 'in_progress') ?? null,
)

const latestCompletedChallenge = computed(
  () => challengeList.value.find((item) => item.status === 'completed') ?? null,
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

async function loadChallenges(findingId: string) {
  challengeLoading.value = true
  challengeSummary.value = ''
  try {
    challengeList.value = await api.listFindingDebates(
      caseId.value,
      findingId,
    )
    const latest = latestCompletedChallenge.value
    if (latest) {
      const detail = await api.getDebate(latest.id)
      const moderator = detail.messages.find(
        (message) => message.role === 'moderator' && message.round === 4,
      )
      challengeSummary.value = moderator
        ? moderator.content.replace(/[#>\n]+/g, ' ').trim().slice(0, 160)
        : ''
    }
  } catch {
    challengeList.value = []
  } finally {
    challengeLoading.value = false
  }
}

async function open(findingId: string) {
  error.value = null
  try {
    selected.value = await findingApi.get(caseId.value, findingId)
    await loadChallenges(findingId)
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

// M5.5：开始/继续挑战 — create-or-resume，返回现有 active 或新建 debate，
// 直接进入 FindingDebateModal（debateId 隔离加载）。
async function startChallenge(finding: Finding) {
  if (challenging.value) return
  challenging.value = true
  error.value = null
  try {
    const debate = await api.createFindingDebate(caseId.value, finding.id)
    activeChallengeId.value = debate.id
    challengeModalOpen.value = true
    await loadChallenges(finding.id)
  } catch {
    error.value = '发起挑战失败，请稍后重试。'
  } finally {
    challenging.value = false
  }
}

function continueChallenge() {
  if (!activeChallenge.value || !selected.value) return
  activeChallengeId.value = activeChallenge.value.id
  challengeModalOpen.value = true
}

function viewLatestChallenge() {
  if (!latestCompletedChallenge.value || !selected.value) return
  activeChallengeId.value = latestCompletedChallenge.value.id
  challengeModalOpen.value = true
}

function onChallengeCompleted() {
  if (selected.value) void loadChallenges(selected.value.finding.id)
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

        <!-- 对抗性审查（M5.4）：三态 — 未发起 / 进行中 / 已完成。 -->
        <section class="ifind__section ifind__challenge">
          <h4><Gavel :size="13" /> 对抗性审查</h4>
          <p v-if="challengeLoading" class="ifind__hint">审查状态加载中…</p>
          <template v-else>
            <div v-if="!challengeList.length" class="ifind__challenge-row">
              <p class="ifind__hint">尚未进行对抗性审查。</p>
              <button
                type="button"
                class="ifind__btn"
                :disabled="challenging"
                @click="startChallenge(selected.finding)"
              >
                <Gavel :size="14" />
                {{ challenging ? '发起中…' : '开始挑战' }}
              </button>
            </div>
            <div v-else-if="activeChallenge" class="ifind__challenge-row">
              <p class="ifind__hint">
                对抗性审查：进行中 · 第 {{ activeChallenge.round }} 轮 / 4
              </p>
              <button
                type="button"
                class="ifind__btn"
                @click="continueChallenge"
              >
                继续挑战
              </button>
            </div>
            <div v-else class="ifind__challenge-row ifind__challenge-row--done">
              <p class="ifind__hint">
                最近一次挑战：已完成
                <span v-if="challengeSummary"> — {{ challengeSummary }}…</span>
              </p>
              <div class="ifind__challenge-actions">
                <button type="button" class="ifind__btn" @click="viewLatestChallenge">
                  查看结果
                </button>
                <button
                  type="button"
                  class="ifind__btn"
                  :disabled="challenging"
                  @click="startChallenge(selected.finding)"
                >
                  {{ challenging ? '发起中…' : '发起新一轮挑战' }}
                </button>
              </div>
              <p class="ifind__hint ifind__challenge-count">
                历史挑战 {{ challengeList.length }} 次
              </p>
            </div>
          </template>
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
            @click="startChallenge(selected.finding)"
          >
            <Gavel :size="14" />
            {{ challenging ? '发起中…' : '挑战此结论' }}
          </button>
        </div>
      </section>
      <section v-else class="ifind__detail ifind__detail--empty">
        <p class="ifind__hint">从左侧选择一条结论查看详情。</p>
      </section>
    </div>

    <FindingDebateModal
      v-if="selected && challengeModalOpen"
      :case-id="caseId"
      :finding-id="selected.finding.id"
      :finding-title="selected.finding.title"
      :finding-status="statusLabels[selected.finding.status]"
      :debate-id="activeChallengeId"
      @close="challengeModalOpen = false"
      @completed="onChallengeCompleted"
    />
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

.ifind__challenge h4 {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.ifind__challenge-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ifind__challenge-row--done {
  gap: 6px;
}

.ifind__challenge-actions {
  display: flex;
  gap: 8px;
}

.ifind__challenge-count {
  font-size: 11px;
}
</style>
