<script setup lang="ts">
// C9.2: 目标与计划面板（自 GoalPlanningView 抽出；case 由 workspace 提供）。
// 目标列表 + 完成条件 + 计划版本 + 计划 DAG + 完成度评估。
import { GitBranch, ListChecks, Target } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'

import { api } from '@/services/api'
import type { GoalDetail, GoalSummary, PlanDetail } from '@/types/api'

const props = defineProps<{ caseId: string }>()

const loading = ref(false)
const error = ref('')
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
  if (!props.caseId) return
  loading.value = true
  error.value = ''
  try {
    goals.value = await api.listGoals(props.caseId)
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

watch(
  () => props.caseId,
  () => {
    expandedGoalId.value = null
    void load()
  },
)
onMounted(load)
</script>

<template>
  <div class="gpp">
    <div v-if="error" class="gpp__error">{{ error }}</div>

    <div v-if="loading" class="gpp__state">加载中…</div>
    <div v-else-if="goals.length === 0" class="gpp__state">该调查暂无显式目标。</div>

    <div v-else class="gpp__list">
      <article v-for="goal in goals" :key="goal.id" class="gpp__card">
        <button class="gpp__main" type="button" @click="toggleGoal(goal)">
          <div class="gpp__top">
            <Target :size="16" class="gpp__icon" />
            <span class="gpp__badge" :class="`gpp__badge--${goal.status}`">
              {{ STATUS_LABELS[goal.status] || goal.status }}
            </span>
            <span class="gpp__title">{{ goal.title }}</span>
            <span class="gpp__meta">v{{ goal.version }} · {{ goal.priority }} · {{ goal.source }}</span>
          </div>
          <p class="gpp__objective">{{ goal.objective }}</p>
          <p v-if="goal.constraints && goal.constraints.length" class="gpp__constraints">
            约束：{{ goal.constraints.join('；') }}
          </p>
        </button>

        <div v-if="expandedGoalId === goal.id && detail" class="gpp__detail">
          <section class="gpp__section">
            <h4><ListChecks :size="14" /> 完成条件</h4>
            <div v-for="c in detail.criteria" :key="c.id" class="gpp__criterion">
              <span class="gpp__badge">{{ c.status }}</span>
              <span class="gpp__criterion-text">{{ c.description }}</span>
              <span class="gpp__criterion-meta">{{ c.criterion_type }}{{ c.required ? ' · 必需' : '' }}</span>
            </div>
          </section>

          <section class="gpp__section">
            <h4><GitBranch :size="14" /> 计划版本</h4>
            <div class="gpp__versions">
              <button
                v-for="v in detail.plan_versions"
                :key="v.id"
                type="button"
                class="gpp__version-btn"
                :class="{ 'gpp__version-btn--active': selectedPlanVersion === v.id }"
                @click="switchPlan(v.id)"
              >
                v{{ v.version }}（{{ v.status }}）
              </button>
            </div>
          </section>

          <section v-if="plan" class="gpp__section">
            <h4>计划 DAG（拓扑序：{{ plan.topological_order.join(' → ') }}）</h4>
            <div class="gpp__dag">
              <div
                v-for="step in plan.steps"
                :key="step.id"
                class="gpp__node"
                :class="`gpp__node--${step.status}`"
              >
                <div class="gpp__node-head">
                  <span class="gpp__node-key">{{ step.step_key }}</span>
                  <span class="gpp__badge">{{ STEP_STATUS[step.status] || step.status }}</span>
                </div>
                <div class="gpp__node-task">{{ step.task }}</div>
                <div class="gpp__node-meta">
                  {{ step.agent_capability }}{{ step.run_id ? ' · run ' + step.run_id.slice(0, 8) : '' }}
                </div>
                <div v-if="(depsByStep[step.step_key] ?? []).length" class="gpp__node-deps">
                  依赖：{{ (depsByStep[step.step_key] ?? []).join(', ') }}
                </div>
              </div>
            </div>
          </section>

          <section v-if="detail.assessments && detail.assessments.length" class="gpp__section">
            <h4>完成度评估</h4>
            <div v-for="a in detail.assessments ?? []" :key="a.id" class="gpp__history">
              <span class="gpp__badge">{{ a.result }}</span>
              <span class="gpp__history-text">{{ a.verifier }}</span>
              <span class="gpp__history-meta">{{ a.gaps?.join('；') || '无缺口' }}</span>
            </div>
          </section>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.gpp {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.gpp__error {
  background: rgba(239, 68, 68, 0.08);
  color: #b91c1c;
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
}

.gpp__state {
  text-align: center;
  color: var(--text-soft);
  padding: 24px 0;
  font-size: 13px;
}

.gpp__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.gpp__card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}

.gpp__main {
  display: block;
  width: 100%;
  text-align: left;
  padding: 12px 14px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text);
}

.gpp__top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.gpp__icon {
  color: var(--cyan);
}

.gpp__badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--text-muted);
}

.gpp__badge--active,
.gpp__badge--completed {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.gpp__badge--running,
.gpp__badge--proposed {
  background: rgba(245, 158, 11, 0.12);
  color: #b45309;
}

.gpp__badge--failed {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}

.gpp__title {
  font-weight: 600;
  font-size: 13px;
}

.gpp__meta {
  margin-left: auto;
  color: var(--text-soft);
  font-size: 11px;
}

.gpp__objective {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text);
}

.gpp__constraints {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-muted);
}

.gpp__detail {
  border-top: 1px solid var(--border);
  padding: 12px 14px;
}

.gpp__section {
  margin-bottom: 12px;
}

.gpp__section h4 {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0 0 8px;
  font-size: 12px;
}

.gpp__criterion {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}

.gpp__criterion-text {
  flex: 1;
}

.gpp__criterion-meta {
  color: var(--text-soft);
  font-size: 11px;
}

.gpp__versions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.gpp__version-btn {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 4px 10px;
  font-size: 11px;
  cursor: pointer;
  color: var(--text);
}

.gpp__version-btn--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.gpp__dag {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.gpp__node {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px 10px;
  background: var(--surface-muted);
}

.gpp__node--completed { border-left: 3px solid var(--green); }
.gpp__node--running { border-left: 3px solid var(--cyan); }
.gpp__node--failed { border-left: 3px solid var(--red); }
.gpp__node--pending,
.gpp__node--ready { border-left: 3px solid var(--border-strong); }

.gpp__node-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.gpp__node-key {
  font-weight: 700;
  font-family: ui-monospace, monospace;
  font-size: 12px;
}

.gpp__node-task {
  font-size: 12px;
  margin-top: 4px;
}

.gpp__node-meta {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

.gpp__node-deps {
  font-size: 10px;
  color: var(--text-soft);
  margin-top: 3px;
}

.gpp__history {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}

.gpp__history-text {
  flex: 1;
}

.gpp__history-meta {
  color: var(--text-soft);
  font-size: 11px;
}
</style>
