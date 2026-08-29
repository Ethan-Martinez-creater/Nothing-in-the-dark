<script setup lang="ts">
import {
  AlertTriangle,
  Bell,
  BellOff,
  Check,
  Clock,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  X,
} from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import { collectionApi, type CollectionDefinition } from '@/services/api/collections'
import type {
  AlertRule,
  MonitorAlert,
  MonitorDefinition,
  MonitorExecution,
} from '@/types/api'

const props = defineProps<{
  caseId: string
  open: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const loading = ref(true)
const error = ref('')
const monitors = ref<MonitorDefinition[]>([])
const alerts = ref<MonitorAlert[]>([])
const rules = ref<Record<string, AlertRule[]>>({})
const executions = ref<Record<string, MonitorExecution[]>>({})

const showCreate = ref(false)
const creating = ref(false)
const newName = ref('')
const newInterval = ref(3600)
const newPlatforms = ref('weibo')

const running = ref<Record<string, boolean>>({})
const actionError = ref('')
const expanded = ref<Record<string, boolean>>({})

const PLATFORM_LABELS: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

const SEVERITY_LABELS: Record<string, string> = {
  info: '提示',
  warning: '警告',
  critical: '严重',
}

const ALERT_STATUS_LABELS: Record<string, string> = {
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已解决',
  suppressed: '已抑制',
}

const RULE_TYPE_LABELS: Record<string, string> = {
  absolute_volume: '绝对量',
  rate_growth: '增长率',
  anomaly: '异常',
  key_account: '关键账号',
  narrative: '新叙事',
}

const openAlerts = computed(() => alerts.value.filter((a) => a.status === 'open'))

function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] || platform
}

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [monitorList, alertList] = await Promise.all([
      api.listMonitors(props.caseId),
      api.listAlerts(props.caseId),
    ])
    monitors.value = monitorList
    alerts.value = alertList
    for (const monitor of monitorList) {
      if (monitor.enabled && !expanded.value[monitor.id]) {
        void loadMonitorDetails(monitor.id)
      }
    }
  } catch {
    error.value = '加载监测数据失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function loadMonitorDetails(monitorId: string) {
  try {
    const [ruleList, execList] = await Promise.all([
      api.listMonitorRules(props.caseId, monitorId),
      api.listMonitorExecutions(props.caseId, monitorId),
    ])
    rules.value = { ...rules.value, [monitorId]: ruleList }
    executions.value = { ...executions.value, [monitorId]: execList }
  } catch {
    // 详情加载失败不阻断列表。
  }
}

// M3.8: 创建 Monitor 时从 Active Collection Definition 预填并保存
// snapshot（后端不动态追踪 active version，保证历史执行可复现）。
const activeCollection = ref<CollectionDefinition | null>(null)

onMounted(async () => {
  try {
    activeCollection.value = await collectionApi.getActive(props.caseId)
  } catch {
    activeCollection.value = null
  }
})

async function createMonitor() {
  if (!newName.value.trim() || creating.value) return
  creating.value = true
  actionError.value = ''
  try {
    const collection = activeCollection.value
    const querySpec: Record<string, unknown> | undefined = collection
      ? {
          collection_definition_id: collection.id,
          collection_definition_version: collection.version,
          platform_queries: collection.platform_queries,
          exclusions: collection.exclusions,
        }
      : undefined
    await api.createMonitor(props.caseId, {
      name: newName.value.trim(),
      schedule_type: 'interval',
      interval_seconds: newInterval.value,
      platforms: newPlatforms.value.split(',').map((s) => s.trim()).filter(Boolean),
      ...(querySpec ? { query_spec: querySpec } : {}),
    })
    newName.value = ''
    showCreate.value = false
    await loadAll()
  } catch {
    actionError.value = '创建监测失败，请检查参数。'
  } finally {
    creating.value = false
  }
}

async function toggleMonitor(monitor: MonitorDefinition) {
  if (running.value[monitor.id]) return
  running.value = { ...running.value, [monitor.id]: true }
  actionError.value = ''
  try {
    const updated = monitor.enabled
      ? await api.pauseMonitor(props.caseId, monitor.id)
      : await api.resumeMonitor(props.caseId, monitor.id)
    monitors.value = monitors.value.map((m) => (m.id === monitor.id ? updated : m))
  } catch {
    actionError.value = '切换监测状态失败。'
  } finally {
    running.value = { ...running.value, [monitor.id]: false }
  }
}

async function runNow(monitor: MonitorDefinition) {
  if (running.value[monitor.id]) return
  running.value = { ...running.value, [monitor.id]: true }
  actionError.value = ''
  try {
    await api.runMonitorNow(props.caseId, monitor.id)
    await loadMonitorDetails(monitor.id)
    await loadAll()
  } catch {
    actionError.value = '立即运行失败。'
  } finally {
    running.value = { ...running.value, [monitor.id]: false }
  }
}

async function setAlertStatus(alert: MonitorAlert, action: 'acknowledge' | 'resolve') {
  try {
    const updated = action === 'acknowledge'
      ? await api.acknowledgeAlert(props.caseId, alert.id)
      : await api.resolveAlert(props.caseId, alert.id)
    alerts.value = alerts.value.map((a) => (a.id === alert.id ? updated : a))
  } catch {
    actionError.value = '告警状态更新失败。'
  }
}

function toggleExpand(monitorId: string) {
  const next = !expanded.value[monitorId]
  expanded.value = { ...expanded.value, [monitorId]: next }
  if (next) void loadMonitorDetails(monitorId)
}

function execList(monitorId: string): MonitorExecution[] {
  return executions.value[monitorId]?.slice(0, 5) ?? []
}

function execPostCount(exec: MonitorExecution): number {
  const stats = exec.platform_stats as { totals?: Record<string, number> }
  return stats.totals?.post_count ?? 0
}

onMounted(loadAll)
</script>

<template>
  <aside v-if="open" class="monitoring-panel" aria-label="监测与告警面板">
    <header class="panel-header">
      <div class="panel-title">
        <Bell :size="16" />
        <span>监测与告警</span>
      </div>
      <button type="button" class="icon-button" aria-label="关闭面板" @click="emit('close')">
        <X :size="16" />
      </button>
    </header>

    <div class="panel-body">
      <div v-if="loading" class="state">
        <LoaderCircle :size="18" class="spin" />
        <span>加载中…</span>
      </div>
      <div v-else-if="error" class="state error">
        <AlertTriangle :size="18" />
        <span>{{ error }}</span>
        <button type="button" class="ghost-button" @click="loadAll">重试</button>
      </div>
      <template v-else>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>

        <section class="section">
          <div class="section-head">
            <h3>持续监测</h3>
            <button type="button" class="ghost-button" @click="showCreate = !showCreate">
              <Plus :size="14" />
              新建
            </button>
          </div>

          <form v-if="showCreate" class="create-form" @submit.prevent="createMonitor">
            <label>
              名称
              <input v-model="newName" type="text" placeholder="例如：每日舆情监测" />
            </label>
            <label>
              间隔（秒）
              <input v-model.number="newInterval" type="number" min="60" />
            </label>
            <label>
              平台（逗号分隔）
              <input v-model="newPlatforms" type="text" />
            </label>
            <div class="form-actions">
              <button type="submit" class="primary-button" :disabled="creating">
                {{ creating ? '创建中…' : '创建' }}
              </button>
              <button type="button" class="ghost-button" @click="showCreate = false">取消</button>
            </div>
          </form>

          <ul v-if="monitors.length" class="monitor-list">
            <li v-for="monitor in monitors" :key="monitor.id" class="monitor-item">
              <button
                type="button"
                class="monitor-head"
                @click="toggleExpand(monitor.id)"
              >
                <span class="monitor-name">{{ monitor.name }}</span>
                <span class="monitor-meta">
                  {{ monitor.enabled ? '运行中' : '已暂停' }} ·
                  每 {{ monitor.interval_seconds }}s ·
                  {{ monitor.platforms.map(platformLabel).join('/') }}
                </span>
              </button>
              <div class="monitor-actions">
                <button
                  type="button"
                  class="ghost-button"
                  :disabled="running[monitor.id]"
                  @click="toggleMonitor(monitor)"
                >
                  <BellOff v-if="monitor.enabled" :size="14" />
                  <Play v-else :size="14" />
                  {{ monitor.enabled ? '暂停' : '恢复' }}
                </button>
                <button
                  type="button"
                  class="ghost-button"
                  :disabled="running[monitor.id]"
                  @click="runNow(monitor)"
                >
                  <RefreshCw :size="14" :class="{ spin: running[monitor.id] }" />
                  立即运行
                </button>
              </div>

              <div v-if="expanded[monitor.id]" class="monitor-detail">
                <div v-if="rules[monitor.id]?.length" class="detail-block">
                  <h4>告警规则</h4>
                  <ul>
                    <li v-for="rule in rules[monitor.id]" :key="rule.id">
                      {{ RULE_TYPE_LABELS[rule.rule_type] || rule.rule_type }}
                      <span class="badge">{{ SEVERITY_LABELS[rule.severity] }}</span>
                      <span v-if="!rule.enabled" class="badge muted">停用</span>
                    </li>
                  </ul>
                </div>
                <div v-if="executions[monitor.id]?.length" class="detail-block">
                  <h4>执行历史</h4>
                  <ul class="exec-list">
                    <li v-for="exec in execList(monitor.id)" :key="exec.id">
                      <span class="exec-status" :class="exec.status">{{ exec.status }}</span>
                      <span class="exec-time">{{ new Date(exec.scheduled_at).toLocaleString() }}</span>
                      <span class="exec-counts">帖 {{ execPostCount(exec) }}</span>
                    </li>
                  </ul>
                </div>
                <div v-if="!rules[monitor.id]?.length && !executions[monitor.id]?.length" class="detail-block">
                  暂无规则或执行记录。
                </div>
              </div>
            </li>
          </ul>
          <div v-else class="state">
            <Clock :size="18" />
            <span>尚未创建监测。</span>
          </div>
        </section>

        <section class="section">
          <div class="section-head">
            <h3>告警收件箱</h3>
            <span v-if="openAlerts.length" class="badge critical">{{ openAlerts.length }} 待处理</span>
          </div>
          <ul v-if="alerts.length" class="alert-list">
            <li v-for="alert in alerts" :key="alert.id" class="alert-item" :class="alert.status">
              <div class="alert-main">
                <div class="alert-explanation">{{ alert.explanation }}</div>
                <div class="alert-meta">
                  {{ ALERT_STATUS_LABELS[alert.status] }} ·
                  触发 {{ alert.trigger_count }} 次 ·
                  {{ new Date(alert.last_seen_at).toLocaleString() }}
                </div>
              </div>
              <div class="alert-actions">
                <button
                  v-if="alert.status === 'open'"
                  type="button"
                  class="ghost-button"
                  @click="setAlertStatus(alert, 'acknowledge')"
                >
                  <Check :size="14" /> 确认
                </button>
                <button
                  v-if="alert.status === 'open' || alert.status === 'acknowledged'"
                  type="button"
                  class="ghost-button"
                  @click="setAlertStatus(alert, 'resolve')"
                >
                  解决
                </button>
              </div>
            </li>
          </ul>
          <div v-else class="state">
            <BellOff :size="18" />
            <span>暂无告警。</span>
          </div>
        </section>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.monitoring-panel {
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
.action-error {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 13px;
}
.section {
  margin-bottom: 18px;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.section-head h3 {
  font-size: 14px;
  font-weight: 600;
}
.ghost-button,
.primary-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
}
.ghost-button {
  border: 1px solid var(--color-border, #e2e8f0);
  background: transparent;
}
.ghost-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.primary-button {
  border: none;
  background: var(--color-primary, #2563eb);
  color: #fff;
}
.primary-button:disabled {
  opacity: 0.5;
}
.create-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
  padding: 10px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
}
.create-form label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.create-form input {
  padding: 6px 8px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 6px;
  font-size: 13px;
}
.form-actions {
  display: flex;
  gap: 8px;
}
.monitor-list,
.alert-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.monitor-item {
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 8px;
}
.monitor-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  padding: 0;
}
.monitor-name {
  font-weight: 600;
}
.monitor-meta {
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.monitor-actions {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.monitor-detail {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--color-border, #e2e8f0);
  font-size: 13px;
}
.detail-block {
  margin-bottom: 8px;
}
.detail-block h4 {
  font-size: 12px;
  margin: 0 0 4px;
  color: var(--color-muted, #64748b);
}
.detail-block ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.detail-block li {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
}
.exec-list li {
  display: flex;
  gap: 8px;
  align-items: center;
}
.exec-status {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f5f9;
}
.exec-status.succeeded {
  background: #dcfce7;
  color: #166534;
}
.exec-status.partial {
  background: #fef9c3;
  color: #854d0e;
}
.exec-status.failed {
  background: #fee2e2;
  color: #991b1b;
}
.exec-time {
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.exec-counts {
  font-size: 12px;
}
.badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: var(--color-muted, #64748b);
}
.badge.critical {
  background: #fee2e2;
  color: #991b1b;
}
.badge.muted {
  opacity: 0.6;
}
.alert-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
  margin-bottom: 8px;
}
.alert-item.open {
  border-left: 3px solid #dc2626;
}
.alert-item.acknowledged {
  border-left: 3px solid #d97706;
}
.alert-item.resolved {
  opacity: 0.7;
}
.alert-explanation {
  font-size: 13px;
}
.alert-meta {
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.alert-actions {
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
