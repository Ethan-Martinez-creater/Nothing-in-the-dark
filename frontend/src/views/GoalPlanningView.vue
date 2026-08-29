<script setup lang="ts">
import { GitBranch, ListChecks, RefreshCw, Target } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { CaseRecord, GoalDetail, GoalSummary, PlanDetail } from '@/types/api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const cases = ref<CaseRecord[]>([])
const selectedCaseId = ref('')
const goals = ref<GoalSummary[]>([])
const detail = ref<GoalDetail | null>(null)
const plan = ref<PlanDetail | null>(null)
const expandedGoalId = ref<string | null>(null)
const selectedPlanVersion = ref('')

const STATUS_LABELS: Record<string, string> = {
  proposed: '提议',
  active: '进行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  superseded: '已取代',
}
const STEP_STATUS: Record<string, string> = {
  pending: '待执行',
  ready: '就绪',
  running: '执行中',
  completed: '完成',
  failed: '失败',
  skipped: '跳过',
}

const depsByStep = computed<Record<string, string[]>>(() => {
  const map: Record<string, string[]> = {}
  for (const edge of plan.value?.edges ?? []) {
    const key = edge.target_step_key
    map[key] = [...(map[key] ?? []), edge.source_step_key]
  }
  return map
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const caseList = await api.listCases()
    cases.value = caseList
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
    goals.value = await api.listGoals(selectedCaseId.value)
  } catch (e) {
    error.value = '目标加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function toggleGoal(goal: GoalSummary) {
  expandedGoalId.value = expandedGoalId.value === goal.id ? null : goal.id
  detail.value = null
  plan.value = null
  if (expandedGoalId.value === goal.id) {
    try {
      detail.value = await api.getGoalDetail(goal.id)
      const activeVersion = detail.value.plan_versions.find((v) => v.status === 'active')
        || detail.value.plan_versions[detail.value.plan_versions.length - 1]
      if (activeVersion) {
        selectedPlanVersion.value = activeVersion.id
        plan.value = await api.getPlan(activeVersion.id)
      }
    } catch (e) {
      error.value = '目标详情加载失败：' + (e instanceof Error ? e.message : String(e))
    }
  }
}

async function switchPlan(versionId: string) {
  selectedPlanVersion.value = versionId
  try {
    plan.value = await api.getPlan(versionId)
  } catch (e) {
    error.value = '计划加载失败：' + (e instanceof Error ? e.message : String(e))
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">显式目标与计划图</h1>
        <p class="page-subtitle">M17：目标、完成条件、计划 DAG 与执行状态。</p>
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
      <span class="filter-count">{{ goals.length }} 个目标</span>
    </div>

    <div v-if="loading" class="empty-state">加载中…</div>
    <div v-else-if="goals.length === 0 && selectedCaseId" class="empty-state">该案件暂无显式目标。</div>

    <div v-else class="goal-list">
      <article v-for="goal in goals" :key="goal.id" class="goal-card">
        <button class="card-main" @click="toggleGoal(goal)">
          <div class="card-top">
            <Target :size="16" class="goal-icon" />
            <span class="badge" :class="goal.status">{{ STATUS_LABELS[goal.status] || goal.status }}</span>
            <span class="card-title">{{ goal.title }}</span>
            <span class="card-meta">v{{ goal.version }} · {{ goal.priority }} · {{ goal.source }}</span>
          </div>
          <p class="card-objective">{{ goal.objective }}</p>
          <p v-if="goal.constraints && goal.constraints.length" class="card-constraints">约束：{{ goal.constraints.join('；') }}</p>
        </button>

        <div v-if="expandedGoalId === goal.id && detail" class="card-detail">
          <section class="sub-panel">
            <h4><ListChecks :size="14" /> 完成条件</h4>
            <div v-for="c in detail.criteria" :key="c.id" class="criterion-row">
              <span class="badge" :class="c.status">{{ c.status }}</span>
              <span class="criterion-text">{{ c.description }}</span>
              <span class="criterion-meta">{{ c.criterion_type }}{{ c.required ? ' · 必需' : '' }}</span>
            </div>
          </section>

          <section class="sub-panel">
            <h4><GitBranch :size="14" /> 计划版本</h4>
            <div class="version-row">
              <button v-for="v in detail.plan_versions" :key="v.id" class="btn small" :class="{ primary: selectedPlanVersion === v.id }" @click="switchPlan(v.id)">
                v{{ v.version }}（{{ v.status }}）
              </button>
            </div>
          </section>

          <section v-if="plan" class="sub-panel">
            <h4>计划 DAG（拓扑序：{{ plan.topological_order.join(' → ') }}）</h4>
            <div class="dag">
              <div v-for="step in plan.steps" :key="step.id" class="dag-node" :class="step.status">
                <div class="node-head">
                  <span class="node-key">{{ step.step_key }}</span>
                  <span class="badge" :class="step.status">{{ STEP_STATUS[step.status] || step.status }}</span>
                </div>
                <div class="node-task">{{ step.task }}</div>
                <div class="node-meta">{{ step.agent_capability }}{{ step.run_id ? ' · run ' + step.run_id.slice(0, 8) : '' }}</div>
                <div v-if="(depsByStep[step.step_key] ?? []).length" class="node-deps">
                  依赖：{{ (depsByStep[step.step_key] ?? []).join(', ') }}
                </div>
              </div>
            </div>
          </section>

          <section v-if="detail.assessments && detail.assessments.length" class="sub-panel">
            <h4>完成度评估</h4>
            <div v-for="a in detail.assessments ?? []" :key="a.id" class="history-row">
              <span class="badge" :class="a.result">{{ a.result }}</span>
              <span class="history-text">{{ a.verifier }}</span>
              <span class="history-meta">{{ a.gaps?.join('；') || '无缺口' }}</span>
            </div>
          </section>
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
.btn.ghost { background: transparent; }
.btn.small { padding: 4px 9px; font-size: 12px; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; color: var(--text); max-width: 340px; }
.filter-count { color: var(--text-muted); font-size: 13px; }
.goal-list { display: flex; flex-direction: column; gap: 12px; }
.goal-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.card-main { display: block; width: 100%; text-align: left; padding: 14px 16px; background: none; border: none; cursor: pointer; }
.card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.goal-icon { color: var(--cyan); }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); }
.badge.active, .badge.completed, .badge.ready, .badge.completed { background: rgba(16, 185, 129, 0.12); color: #047857; }
.badge.running, .badge.proposed { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.badge.failed { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.badge.superseded, .badge.cancelled, .badge.skipped, .badge.pending { background: rgba(100, 116, 139, 0.12); color: #475569; }
.card-title { font-weight: 600; font-size: 14px; }
.card-meta { margin-left: auto; color: var(--text-soft); font-size: 12px; }
.card-objective { margin: 8px 0 0; font-size: 13px; color: var(--text); }
.card-constraints { margin: 4px 0 0; font-size: 12px; color: var(--text-muted); }
.card-detail { border-top: 1px solid var(--border); padding: 14px 16px; }
.sub-panel { margin-bottom: 14px; }
.sub-panel h4 { display: flex; align-items: center; gap: 5px; margin: 0 0 8px; font-size: 13px; }
.criterion-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.criterion-text { flex: 1; }
.criterion-meta { color: var(--text-soft); font-size: 12px; }
.version-row { display: flex; gap: 8px; flex-wrap: wrap; }
.dag { display: flex; flex-direction: column; gap: 8px; }
.dag-node { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--surface-muted); }
.dag-node.completed { border-left: 3px solid var(--green); }
.dag-node.running { border-left: 3px solid var(--cyan); }
.dag-node.failed { border-left: 3px solid var(--red); }
.dag-node.pending, .dag-node.ready { border-left: 3px solid var(--border-strong); }
.node-head { display: flex; align-items: center; gap: 8px; }
.node-key { font-weight: 700; font-family: ui-monospace, monospace; font-size: 13px; }
.node-task { font-size: 13px; margin-top: 4px; }
.node-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.node-deps { font-size: 11px; color: var(--text-soft); margin-top: 4px; }
.history-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
.history-text { flex: 1; }
.history-meta { color: var(--text-soft); font-size: 11px; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
</style>
