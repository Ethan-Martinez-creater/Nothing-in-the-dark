<script setup lang="ts">
import { Activity, RefreshCw, Search } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { AgentRun, CaseRecord, MonitorAlert, RunTrace, SloResult, TelemetryHealth } from '@/types/api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const health = ref<TelemetryHealth | null>(null)
const cases = ref<CaseRecord[]>([])
const selectedCaseId = ref('')
const alerts = ref<MonitorAlert[]>([])
const runs = ref<AgentRun[]>([])
const selectedRunId = ref('')
const trace = ref<RunTrace | null>(null)
const traceLoading = ref(false)
const activeTab = ref<'slo' | 'alerts' | 'trace'>('slo')

const ALERT_LABELS: Record<string, string> = {
  open: '未处理',
  acknowledged: '已确认',
  resolved: '已解决',
  suppressed: '已抑制',
}

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [h, caseList] = await Promise.all([api.getTelemetryHealth(), api.listCases()])
    health.value = h
    cases.value = caseList
  } catch (e) {
    error.value = '加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function selectCase() {
  if (!selectedCaseId.value) return
  try {
    const [alertList, runList] = await Promise.all([
      api.listAlerts(selectedCaseId.value),
      api.listCaseRuns(selectedCaseId.value),
    ])
    alerts.value = alertList
    runs.value = runList
  } catch (e) {
    error.value = '告警/运行加载失败：' + (e instanceof Error ? e.message : String(e))
  }
}

async function loadTrace() {
  if (!selectedRunId.value) return
  traceLoading.value = true
  trace.value = null
  try {
    trace.value = await api.getRunTrace(selectedRunId.value)
  } catch (e) {
    error.value = 'Trace 加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    traceLoading.value = false
  }
}

function sloClass(slo: SloResult): string {
  return slo.violated ? 'violated' : slo.burn_rate > 1 ? 'burning' : 'ok'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">生产可观测性与 SLO</h1>
        <p class="page-subtitle">M19：遥测健康、SLO 合规、告警状态与运行 Trace。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="load"><RefreshCw :size="15" /> 刷新</button>
      </div>
    </header>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="notice">{{ notice }}</div>

    <section class="panel telemetry">
      <h3 class="panel-title"><Activity :size="15" /> 遥测健康</h3>
      <div v-if="health" class="telemetry-grid">
        <div class="t-item"><span class="t-label">Exporter</span><span class="t-value">{{ health.exporter }}</span></div>
        <div class="t-item"><span class="t-label">状态</span><span class="t-value">{{ health.status }}</span></div>
        <div class="t-item"><span class="t-label">Span 数</span><span class="t-value">{{ health.span_count }}</span></div>
        <div class="t-item"><span class="t-label">缺失属性</span><span class="t-value">{{ health.missing_attribute_count }}</span></div>
        <div class="t-item"><span class="t-label">策略版本</span><span class="t-value">{{ health.policy_version }}</span></div>
        <div class="t-item"><span class="t-label">指标摘要</span><span class="t-value mono">{{ JSON.stringify(health.metrics_summary).slice(0, 120) }}</span></div>
      </div>
    </section>

    <nav class="tabs">
      <button class="tab" :class="{ active: activeTab === 'slo' }" @click="activeTab = 'slo'">SLO 合规</button>
      <button class="tab" :class="{ active: activeTab === 'alerts' }" @click="activeTab = 'alerts'">告警状态</button>
      <button class="tab" :class="{ active: activeTab === 'trace' }" @click="activeTab = 'trace'">运行 Trace</button>
    </nav>

    <div v-if="loading" class="empty-state">加载中…</div>

    <!-- SLO -->
    <section v-if="activeTab === 'slo' && health" class="panel">
      <table class="table">
        <thead>
          <tr><th>SLO</th><th>类型</th><th>目标</th><th>实际</th><th>总量/OK</th><th>预算剩余</th><th>燃烧率</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="slo in health.slo" :key="slo.name">
            <td>{{ slo.name }}</td>
            <td class="muted">{{ slo.kind }}</td>
            <td>{{ (slo.target * 100).toFixed(1) }}%</td>
            <td>{{ (slo.actual * 100).toFixed(1) }}%</td>
            <td class="muted">{{ slo.ok }} / {{ slo.total }}</td>
            <td>{{ slo.budget_remaining }}</td>
            <td>{{ slo.burn_rate.toFixed(2) }}</td>
            <td><span class="status-badge" :class="sloClass(slo)">{{ slo.violated ? '违反' : slo.burn_rate > 1 ? '燃烧' : '合规' }}</span></td>
          </tr>
          <tr v-if="!health.slo || health.slo.length === 0"><td colspan="8" class="muted center">暂无 SLO</td></tr>
        </tbody>
      </table>
    </section>

    <!-- 告警 -->
    <section v-if="activeTab === 'alerts'" class="panel">
      <div class="toolbar">
        <select v-model="selectedCaseId" class="filter-select" @change="selectCase">
          <option value="">选择案件…</option>
          <option v-for="c in cases" :key="c.id" :value="c.id">{{ c.title }}</option>
        </select>
        <span class="filter-count">{{ alerts.length }} 条告警</span>
      </div>
      <table class="table">
        <thead><tr><th>告警</th><th>状态</th><th>触发次数</th><th>首次/最后</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="a in alerts" :key="a.id">
            <td class="mono">{{ a.id.slice(0, 8) }}…</td>
            <td><span class="status-badge" :class="a.status">{{ ALERT_LABELS[a.status] || a.status }}</span></td>
            <td>{{ a.trigger_count }}</td>
            <td class="muted">{{ fmt(a.first_seen_at) }}<br />{{ fmt(a.last_seen_at) }}</td>
            <td class="muted">{{ a.explanation }}</td>
          </tr>
          <tr v-if="alerts.length === 0"><td colspan="5" class="muted center">选择案件后显示告警</td></tr>
        </tbody>
      </table>
    </section>

    <!-- Trace -->
    <section v-if="activeTab === 'trace'" class="panel">
      <div class="toolbar">
        <select v-model="selectedCaseId" class="filter-select" @change="selectCase">
          <option value="">选择案件…</option>
          <option v-for="c in cases" :key="c.id" :value="c.id">{{ c.title }}</option>
        </select>
        <select v-model="selectedRunId" class="filter-select">
          <option value="">选择运行…</option>
          <option v-for="r in runs" :key="r.id" :value="r.id">{{ r.objective.slice(0, 40) }}（{{ r.status }}）</option>
        </select>
        <button class="btn primary small" :disabled="!selectedRunId" @click="loadTrace"><Search :size="14" /> 查看 Trace</button>
      </div>
      <div v-if="traceLoading" class="muted">加载 Trace…</div>
      <div v-else-if="trace" class="trace-box">
        <pre class="trace-json">{{ JSON.stringify(trace, null, 2) }}</pre>
      </div>
      <div v-else class="muted center" style="padding: 20px;">选择运行查看 Trace 详情</div>
    </section>
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
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; align-items: center; gap: 6px; margin: 0 0 12px; font-size: 14px; font-weight: 600; }
.telemetry-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.t-item { background: var(--surface-muted); border-radius: 8px; padding: 10px 12px; }
.t-label { display: block; font-size: 12px; color: var(--text-muted); }
.t-value { font-size: 14px; font-weight: 600; word-break: break-all; }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tab { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px; cursor: pointer; color: var(--text-muted); }
.tab.active { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; color: var(--text); max-width: 340px; }
.filter-count { color: var(--text-muted); font-size: 13px; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.status-badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); }
.status-badge.ok, .status-badge.resolved { background: rgba(16, 185, 129, 0.12); color: #047857; }
.status-badge.burning, .status-badge.open { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.status-badge.violated, .status-badge.acknowledged { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.status-badge.suppressed { background: rgba(100, 116, 139, 0.12); color: #475569; }
.muted { color: var(--text-muted); }
.center { text-align: center; }
.trace-box { max-height: 480px; overflow: auto; }
.trace-json { font-size: 12px; background: var(--surface-muted); border: 1px solid var(--border); border-radius: 8px; padding: 12px; white-space: pre-wrap; word-break: break-all; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
</style>
