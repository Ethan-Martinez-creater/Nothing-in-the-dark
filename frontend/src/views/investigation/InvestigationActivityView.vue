<script setup lang="ts">
// Optimization V2 (M2.5)：Activity 工作区。
// 默认显示语义化活动（formatRunEvent 映射）；「查看技术轨迹」才展开
// Advanced Trace（model/tool/approval/cost/raw events）。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AdvancedTraceDrawer from '@/components/activity/AdvancedTraceDrawer.vue'
import SemanticActivityList from '@/components/activity/SemanticActivityList.vue'
import {
  formatRunEvent,
  type SemanticActivity,
} from '@/services/activityFormatter'
import { api } from '@/services/api'
import type { AgentRun } from '@/types/api'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const runs = ref<AgentRun[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const expandedRunId = ref<string | null>(null)
const activitiesByRun = ref<Map<string, SemanticActivity[]>>(new Map())
const traceRunId = ref<string | null>(null)

const statusLabels: Record<string, string> = {
  pending: '排队中',
  running: '进行中',
  waiting_approval: '等待批准',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

async function load() {
  loading.value = true
  error.value = null
  try {
    runs.value = await api.listCaseRuns(caseId.value)
  } catch {
    error.value = '活动加载失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function toggleRun(runId: string) {
  expandedRunId.value = expandedRunId.value === runId ? null : runId
  if (expandedRunId.value && !activitiesByRun.value.has(runId)) {
    try {
      const events = await api.listRunEvents(runId, 0)
      activitiesByRun.value.set(runId, events.map(formatRunEvent))
    } catch {
      activitiesByRun.value.set(runId, [])
    }
  }
}

onMounted(load)
</script>

<template>
  <div class="iact">
    <p v-if="error" class="iact__error">{{ error }}</p>
    <p v-else-if="loading" class="iact__hint">正在加载…</p>
    <p v-else-if="runs.length === 0" class="iact__hint">
      尚无分析活动 — 在 Copilot 中发送分析指令开始。
    </p>

    <section v-for="run in runs" :key="run.id" class="iact__run">
      <header class="iact__run-head">
        <div class="iact__run-title">
          <span class="iact__run-status" :data-status="run.status">
            {{ statusLabels[run.status] ?? run.status }}
          </span>
          <span class="iact__run-objective">{{ run.objective }}</span>
        </div>
        <div class="iact__run-actions">
          <button type="button" class="iact__expand" @click="toggleRun(run.id)">
            {{ expandedRunId === run.id ? '收起活动' : '查看活动' }}
          </button>
          <button type="button" class="iact__trace" @click="traceRunId = run.id">
            查看技术轨迹
          </button>
        </div>
      </header>

      <SemanticActivityList
        v-if="expandedRunId === run.id"
        :activities="activitiesByRun.get(run.id) ?? []"
      />
    </section>

    <div v-if="traceRunId" class="iact__trace-wrap">
      <AdvancedTraceDrawer :run-id="traceRunId" @close="traceRunId = null" />
    </div>
  </div>
</template>

<style scoped>
.iact {
  position: relative;
  max-width: 880px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.iact__error {
  color: var(--red);
  font-size: 13px;
}

.iact__hint {
  color: var(--text-muted);
  font-size: 13px;
}

.iact__run {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  padding: 12px 14px;
}

.iact__run-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.iact__run-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.iact__run-objective {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--text);
}

.iact__run-status {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}

.iact__run-status[data-status='running'] {
  background: rgba(37, 99, 235, 0.1);
  color: var(--accent-strong);
}

.iact__run-status[data-status='waiting_approval'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.iact__run-status[data-status='failed'] {
  background: rgba(239, 68, 68, 0.1);
  color: var(--red);
}

.iact__run-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.iact__expand,
.iact__trace {
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.iact__trace:hover,
.iact__expand:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.iact__trace-wrap {
  position: fixed;
  inset: 0 0 0 auto;
  width: min(520px, 90vw);
  z-index: 60;
  box-shadow: -12px 0 32px rgba(15, 23, 42, 0.12);
}
</style>
