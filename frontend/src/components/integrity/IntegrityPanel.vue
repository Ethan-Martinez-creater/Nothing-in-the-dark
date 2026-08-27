<script setup lang="ts">
import { ChevronDown, ChevronRight, LoaderCircle, RefreshCw, ShieldAlert, ShieldCheck, X } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { CoordinationCluster, CoordinationMember, IntegrityViews, RiskAssessment } from '@/types/api'

const props = defineProps<{ caseId: string; open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const loading = ref(true)
const error = ref('')
const assessments = ref<RiskAssessment[]>([])
const clusters = ref<CoordinationCluster[]>([])
const views = ref<IntegrityViews | null>(null)
const members = ref<Record<string, CoordinationMember[]>>({})
const expandedCluster = ref<string | null>(null)
const reviewNotes = ref<Record<string, string>>({})
const analyzing = ref(false)
const actionError = ref('')
const busy = ref<Record<string, boolean>>({})

const RISK_LABELS: Record<string, string> = { automation: '自动化', marketing: '营销导流', inauthenticity: '不真实' }
const BAND_LABELS: Record<string, string> = { low: '低', medium: '中', high: '高' }
const STATUS_LABELS: Record<string, string> = { signal_only: '仅信号', reviewed_likely: '疑似成立', reviewed_unlikely: '疑似排除', inconclusive: '证据不足' }
const highRisk = computed(() => assessments.value.filter((a) => a.band === 'high'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [assessmentList, clusterList, viewData] = await Promise.all([
      api.listRiskAssessments(props.caseId),
      api.listCoordinationClusters(props.caseId),
      api.getIntegrityViews(props.caseId),
    ])
    assessments.value = assessmentList
    clusters.value = clusterList
    views.value = viewData
  } catch {
    error.value = '加载完整性数据失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function analyze() {
  if (analyzing.value) return
  analyzing.value = true
  actionError.value = ''
  try {
    const { job_id } = await api.analyzeIntegrity(props.caseId)
    await waitForJob(job_id)
    await load()
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : '分析失败。'
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

async function review(assessment: RiskAssessment, status: 'reviewed_likely' | 'reviewed_unlikely' | 'inconclusive') {
  if (busy.value[assessment.id]) return
  busy.value = { ...busy.value, [assessment.id]: true }
  actionError.value = ''
  try {
    const updated = await api.reviewRiskAssessment(props.caseId, assessment.id, status, reviewNotes.value[assessment.id] || '')
    assessments.value = assessments.value.map((a) => (a.id === assessment.id ? updated : a))
    views.value = await api.getIntegrityViews(props.caseId)
  } catch {
    actionError.value = '提交审核失败。'
  } finally {
    busy.value = { ...busy.value, [assessment.id]: false }
  }
}

async function toggleCluster(cluster: CoordinationCluster) {
  if (expandedCluster.value === cluster.id) {
    expandedCluster.value = null
    return
  }
  expandedCluster.value = cluster.id
  if (!members.value[cluster.id]) {
    try {
      members.value = { ...members.value, [cluster.id]: await api.listCoordinationMembers(props.caseId, cluster.id) }
    } catch {
      actionError.value = '加载协同证据失败。'
    }
  }
}

onMounted(load)
</script>

<template>
  <aside v-if="open" class="integrity-panel" aria-label="完整性风险面板">
    <header class="panel-header">
      <div class="panel-title"><ShieldAlert :size="16" /><span>完整性风险</span></div>
      <button type="button" class="icon-button" aria-label="关闭" @click="emit('close')"><X :size="16" /></button>
    </header>
    <div class="panel-body">
      <div v-if="loading" class="state"><LoaderCircle :size="18" class="spin" /><span>加载中…</span></div>
      <div v-else-if="error" class="state error"><span>{{ error }}</span><button type="button" class="ghost-button" @click="load">重试</button></div>
      <template v-else>
        <div class="toolbar">
          <button type="button" class="ghost-button" :disabled="analyzing" @click="analyze"><RefreshCw :size="14" :class="{ spin: analyzing }" />分析完整性</button>
          <span v-if="highRisk.length" class="count">{{ highRisk.length }} 高风险</span>
        </div>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>
        <p class="disclaimer">算法输出为可复核的风险信号，不直接等同于“水军/机器人”事实；原始数据永远保留。</p>

        <section v-if="views" class="section">
          <h3>风险视图对比</h3>
          <div class="view-grid">
            <div><strong>{{ views.raw.post_count }}</strong><span>原始帖子</span></div>
            <div><strong>{{ views.downweighted.post_count.toFixed(1) }}</strong><span>风险降权</span></div>
            <div><strong>{{ views.excluded.post_count }}</strong><span>排除后</span></div>
          </div>
          <p v-if="views.delta.post_count" class="delta-warning">排除视图减少 {{ views.delta.post_count }} 条帖子；这只是派生视图，不删除原始数据。</p>
        </section>

        <section v-if="clusters.length" class="section">
          <h3>疑似协同群体</h3>
          <ul class="cluster-list">
            <li v-for="cluster in clusters" :key="cluster.id" class="cluster-item">
              <button type="button" class="cluster-toggle" @click="toggleCluster(cluster)">
                <ChevronDown v-if="expandedCluster === cluster.id" :size="14" /><ChevronRight v-else :size="14" />
                <span>{{ cluster.size }} 个账号</span><span class="badge">{{ (cluster.score * 100).toFixed(0) }}%</span>
              </button>
              <p class="cluster-explanation">{{ cluster.explanation }}</p>
              <ul v-if="expandedCluster === cluster.id" class="member-list">
                <li v-for="member in members[cluster.id] || []" :key="member.id">
                  <strong>{{ member.account_id }}</strong><span>成员分 {{ (member.membership_score * 100).toFixed(0) }}%</span>
                  <code>{{ JSON.stringify(member.evidence) }}</code>
                </li>
              </ul>
            </li>
          </ul>
        </section>

        <section class="section">
          <h3>单账号风险信号</h3>
          <ul v-if="assessments.length" class="assessment-list">
            <li v-for="assessment in assessments" :key="assessment.id" class="assessment-item">
              <div class="assessment-head"><span class="subject">{{ assessment.subject_id }}</span><span class="badge" :class="assessment.band">{{ RISK_LABELS[assessment.risk_type] }} {{ BAND_LABELS[assessment.band] }}</span></div>
              <div v-if="assessment.reason_codes.length" class="reason-codes">{{ assessment.reason_codes.join(' / ') }}</div>
              <input v-model="reviewNotes[assessment.id]" class="review-note" type="text" maxlength="500" placeholder="审核备注（建议填写）" />
              <div class="assessment-actions">
                <span class="status">{{ STATUS_LABELS[assessment.status] || assessment.status }}</span>
                <button type="button" class="ghost-button" :disabled="busy[assessment.id]" @click="review(assessment, 'inconclusive')">证据不足</button>
                <button type="button" class="ghost-button" :disabled="busy[assessment.id]" @click="review(assessment, 'reviewed_unlikely')">排除</button>
                <button type="button" class="ghost-button" :disabled="busy[assessment.id]" @click="review(assessment, 'reviewed_likely')"><ShieldCheck :size="13" />确认</button>
              </div>
              <small v-if="assessment.reviewed_at" class="review-history">最近审核：{{ new Date(assessment.reviewed_at).toLocaleString() }} {{ assessment.review_note }}</small>
            </li>
          </ul>
          <div v-else class="state"><span>暂无风险信号。点击「分析完整性」扫描。</span></div>
        </section>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.integrity-panel { display: flex; flex-direction: column; width: 380px; border-left: 1px solid var(--color-border, #e2e8f0); background: var(--color-bg, #fff); }
.panel-header, .cluster-toggle, .assessment-head, .assessment-actions { display: flex; align-items: center; }
.panel-header { justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid var(--color-border, #e2e8f0); }
.panel-title { display: flex; align-items: center; gap: 6px; font-weight: 600; }
.icon-button { display: inline-flex; border: none; background: transparent; cursor: pointer; color: var(--color-muted, #64748b); }
.panel-body { flex: 1; overflow-y: auto; padding: 12px 14px; }
.state { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 24px 0; color: var(--color-muted, #64748b); text-align: center; }
.state.error, .action-error { color: #dc2626; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.count, .status, .review-history { font-size: 12px; color: var(--color-muted, #64748b); }
.action-error { margin-bottom: 8px; padding: 8px 10px; border-radius: 6px; background: #fef2f2; font-size: 13px; }
.disclaimer, .cluster-explanation { margin: 0 0 12px; font-size: 12px; color: var(--color-muted, #64748b); line-height: 1.5; }
.section { margin-bottom: 16px; }
.section h3 { font-size: 13px; font-weight: 600; margin: 0 0 8px; }
.ghost-button { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; border-radius: 6px; font-size: 12px; cursor: pointer; border: 1px solid var(--color-border, #e2e8f0); background: transparent; }
.ghost-button:disabled { opacity: .5; cursor: not-allowed; }
.cluster-list, .assessment-list, .member-list { list-style: none; margin: 0; padding: 0; }
.cluster-item, .assessment-item { border: 1px solid var(--color-border, #e2e8f0); border-radius: 8px; padding: 10px; margin-bottom: 8px; }
.cluster-toggle { width: 100%; gap: 6px; padding: 0; border: 0; background: transparent; cursor: pointer; text-align: left; }
.cluster-toggle .badge { margin-left: auto; }
.assessment-head { justify-content: space-between; gap: 8px; }
.subject { font-size: 13px; font-weight: 500; }
.badge { font-size: 11px; padding: 1px 6px; border-radius: 4px; background: #f1f5f9; color: var(--color-muted, #64748b); white-space: nowrap; }
.badge.high { background: #fee2e2; color: #991b1b; }.badge.medium { background: #fef9c3; color: #854d0e; }.badge.low { background: #dcfce7; color: #166534; }
.reason-codes { margin: 6px 0; font-size: 12px; color: var(--color-muted, #64748b); }
.review-note { box-sizing: border-box; width: 100%; margin: 6px 0; padding: 6px 8px; border: 1px solid var(--color-border, #e2e8f0); border-radius: 6px; font-size: 12px; }
.assessment-actions { flex-wrap: wrap; gap: 6px; }.status { flex: 1; }
.view-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }.view-grid div { display: flex; flex-direction: column; padding: 8px; border-radius: 6px; background: #f8fafc; font-size: 11px; }.view-grid strong { font-size: 16px; }.delta-warning { margin: 6px 0 0; font-size: 11px; color: #92400e; }
.member-list li { display: grid; gap: 3px; margin-top: 6px; padding: 6px; background: #f8fafc; font-size: 11px; }.member-list code { overflow-wrap: anywhere; white-space: normal; }.review-history { display: block; margin-top: 6px; }
.spin { animation: spin 1s linear infinite; } @keyframes spin { to { transform: rotate(360deg); } }
</style>
