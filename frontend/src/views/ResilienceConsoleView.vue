<script setup lang="ts">
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleOff,
  FileClock,
  HeartPulse,
  Plus,
  Power,
  RefreshCw,
  ShieldOff,
  Skull,
} from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type {
  CircuitBreakerState,
  DeadLetterItem,
  DependencyHealth,
  IncidentRecord,
  KillSwitch,
  ResilienceHealthSummary,
} from '@/types/api'

const loading = ref(true)
const error = ref('')
const health = ref<ResilienceHealthSummary | null>(null)
const circuits = ref<CircuitBreakerState[]>([])
interface QueueAdmission {
  queue_capacity?: number
  max_wait_seconds?: number
  db_watermark?: number
  disk_watermark?: number
  budget_exhausted?: boolean
  reserved_slots?: number
}
const queues = ref<{ admission?: QueueAdmission } | null>(null)
const deadLetters = ref<DeadLetterItem[]>([])
const killSwitches = ref<KillSwitch[]>([])
const incidents = ref<IncidentRecord[]>([])
const activeTab = ref<'overview' | 'dead-letters' | 'kill-switches' | 'incidents'>('overview')
const notice = ref('')
const actionError = ref('')

// 审批引用（M21）：开关/死信重放需要 approval_id
const approvalId = ref('')
const actionTarget = ref<string | null>(null)
const actionKind = ref('')
const actionReason = ref('')

const STATUS_LABELS: Record<string, string> = {
  healthy: '健康',
  degraded: '降级',
  outage: '中断',
  auth_required: '需登录',
  policy_denied: '策略阻止',
}
const CIRCUIT_LABELS: Record<string, string> = {
  closed: 'closed',
  open: 'open',
  half_open: 'half_open',
}

const healthCounts = computed(() => {
  const counts = { healthy: 0, degraded: 0, outage: 0, auth_required: 0, policy_denied: 0 }
  for (const d of health.value?.dependencies ?? []) {
    if (d.status in counts) counts[d.status as keyof typeof counts] += 1
  }
  return counts
})

const activeSwitches = computed(() => killSwitches.value.filter((k) => k.status === 'on'))

function fmt(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function load() {
  loading.value = true
  error.value = ''
  actionError.value = ''
  try {
    const [h, c, q, dl, ks, inc] = await Promise.all([
      api.getResilienceHealth(),
      api.listCircuitBreakers(),
      api.getQueueBackpressure(),
      api.listDeadLetters(),
      api.listKillSwitches(),
      api.listIncidents(),
    ])
    health.value = h
    circuits.value = c
    queues.value = q
    deadLetters.value = dl
    killSwitches.value = ks
    incidents.value = inc
  } catch (e) {
    error.value = '韧性状态加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

// ---- Kill Switch ----
function openKillSwitchDialog(kind: 'enable' | 'disable', target: string, scope = 'tool') {
  actionTarget.value = target
  actionKind.value = kind
  actionReason.value = ''
  approvalId.value = ''
  actionError.value = ''
}

async function submitKillSwitch() {
  if (!actionTarget.value) return
  actionError.value = ''
  try {
    if (actionKind.value === 'enable') {
      const scope = actionTarget.value === '*' ? 'global' : 'tool'
      await api.enableKillSwitch({
        scope,
        target: actionTarget.value,
        reason: actionReason.value || 'operator action',
        approval_id: approvalId.value || null,
      })
      notice.value = 'Kill Switch 已开启：' + actionTarget.value
    } else {
      const ks = killSwitches.value.find((k) => k.target === actionTarget.value)
      if (ks) {
        await api.disableKillSwitch(ks.id, {
          actor: 'operator',
          reason: actionReason.value || 'recovered',
          approval_id: approvalId.value,
        })
        notice.value = 'Kill Switch 已关闭：' + actionTarget.value
      }
    }
    actionTarget.value = null
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  }
}

// ---- 死信 ----
async function retryDeadLetter(item: DeadLetterItem) {
  actionError.value = ''
  try {
    await api.retryDeadLetter(item.id, {
      actor: 'operator',
      reason: 'manual replay',
      approval_id: approvalId.value || null,
    })
    notice.value = '死信已重放：' + item.operation_key
    await load()
  } catch (e) {
    actionError.value = '重放失败（可能需要有效审批）：' + (e instanceof Error ? e.message : String(e))
  }
}

async function resolveDeadLetter(item: DeadLetterItem) {
  try {
    await api.resolveDeadLetter(item.id, {
      actor: 'operator',
      reason: 'resolved by operator',
    })
    notice.value = '死信已解决：' + item.operation_key
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  }
}

// ---- 事故 ----
const newIncidentTitle = ref('')
const newIncidentSeverity = ref<'info' | 'warning' | 'critical'>('warning')
const newIncidentImpact = ref('')
const incidentFormOpen = ref(false)

async function createIncident() {
  if (!newIncidentTitle.value.trim()) return
  try {
    await api.createIncident({
      title: newIncidentTitle.value.trim(),
      severity: newIncidentSeverity.value,
      impact: newIncidentImpact.value,
    })
    notice.value = '事故已创建'
    newIncidentTitle.value = ''
    newIncidentImpact.value = ''
    incidentFormOpen.value = false
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  }
}

async function closeIncident(incident: IncidentRecord) {
  try {
    await api.closeIncident(incident.id, {})
    notice.value = '事故已关闭：' + incident.title
    await load()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">事故处置台</h1>
        <p class="page-subtitle">故障隔离、降级与事故处置（M22）：健康矩阵、熔断、背压、死信、Kill Switch 与事故记录。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="load"><RefreshCw :size="15" /> 刷新</button>
        <button class="btn primary" @click="incidentFormOpen = !incidentFormOpen"><Plus :size="15" /> 新建事故</button>
      </div>
    </header>

    <div v-if="notice" class="notice">{{ notice }}</div>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="actionError" class="error-box">{{ actionError }}</div>

    <div v-if="incidentFormOpen" class="incident-form">
      <input v-model="newIncidentTitle" class="text-input" placeholder="事故标题（必填）" />
      <select v-model="newIncidentSeverity" class="filter-select">
        <option value="info">info</option>
        <option value="warning">warning</option>
        <option value="critical">critical</option>
      </select>
      <input v-model="newIncidentImpact" class="text-input wide" placeholder="影响描述（可选）" />
      <button class="btn primary small" @click="createIncident">创建</button>
    </div>

    <nav class="tabs">
      <button class="tab" :class="{ active: activeTab === 'overview' }" @click="activeTab = 'overview'">
        <HeartPulse :size="15" /> 总览（健康/熔断/队列）
      </button>
      <button class="tab" :class="{ active: activeTab === 'dead-letters' }" @click="activeTab = 'dead-letters'">
        <FileClock :size="15" /> 死信（{{ deadLetters.length }}）
      </button>
      <button class="tab" :class="{ active: activeTab === 'kill-switches' }" @click="activeTab = 'kill-switches'">
        <Power :size="15" /> Kill Switch（{{ activeSwitches.length }} 开启）
      </button>
      <button class="tab" :class="{ active: activeTab === 'incidents' }" @click="activeTab = 'incidents'">
        <Skull :size="15" /> 事故（{{ incidents.length }}）
      </button>
    </nav>

    <div v-if="loading" class="empty-state">加载中…</div>

    <!-- ============ 总览 ============ -->
    <template v-if="activeTab === 'overview' && health">
      <div class="health-summary">
        <div class="health-pill healthy">{{ healthCounts.healthy }} 健康</div>
        <div class="health-pill degraded">{{ healthCounts.degraded }} 降级</div>
        <div class="health-pill outage">{{ healthCounts.outage }} 中断</div>
        <div class="health-pill auth">{{ healthCounts.auth_required }} 需登录</div>
        <div class="health-pill policy">{{ healthCounts.policy_denied }} 策略阻止</div>
      </div>

      <section class="panel">
        <h3 class="panel-title"><Activity :size="15" /> 依赖健康</h3>
        <table class="table">
          <thead>
            <tr><th>依赖</th><th>作用域</th><th>状态</th><th>熔断</th><th>连续失败</th><th>最后失败</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in health.dependencies" :key="d.dependency + ':' + d.scope">
              <td>{{ d.dependency }}</td>
              <td>{{ d.scope }}</td>
              <td><span class="status-badge" :class="d.status">{{ STATUS_LABELS[d.status] || d.status }}</span></td>
              <td>{{ CIRCUIT_LABELS[d.circuit_state] || d.circuit_state }}</td>
              <td>{{ d.consecutive_failures }}</td>
              <td class="muted">{{ fmt(d.last_failure_at) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="panel">
        <h3 class="panel-title"><CircleOff :size="15" /> 熔断器</h3>
        <table class="table">
          <thead><tr><th>依赖</th><th>作用域</th><th>状态</th><th>失败/成功</th><th>配置版本</th><th>开启时间</th></tr></thead>
          <tbody>
            <tr v-for="c in circuits" :key="c.dependency + ':' + c.scope">
              <td>{{ c.dependency }}</td>
              <td>{{ c.scope }}</td>
              <td><span class="status-badge" :class="c.state">{{ CIRCUIT_LABELS[c.state] }}</span></td>
              <td>{{ c.failure_count }} / {{ c.success_count }}</td>
              <td class="muted">{{ c.config_version }}</td>
              <td class="muted">{{ fmt(c.opened_at) }}</td>
            </tr>
            <tr v-if="circuits.length === 0"><td colspan="6" class="muted center">暂无熔断记录</td></tr>
          </tbody>
        </table>
      </section>

      <section class="panel">
        <h3 class="panel-title"><ShieldOff :size="15" /> 背压与准入</h3>
        <div v-if="queues" class="queue-grid">
          <div class="queue-item"><span class="q-label">队列容量</span><span class="q-value">{{ queues.admission?.queue_capacity }}</span></div>
          <div class="queue-item"><span class="q-label">最大等待(s)</span><span class="q-value">{{ queues.admission?.max_wait_seconds }}</span></div>
          <div class="queue-item"><span class="q-label">DB 水位</span><span class="q-value">{{ queues.admission?.db_watermark }}</span></div>
          <div class="queue-item"><span class="q-label">磁盘水位</span><span class="q-value">{{ queues.admission?.disk_watermark }}</span></div>
          <div class="queue-item"><span class="q-label">预算耗尽</span><span class="q-value">{{ queues.admission?.budget_exhausted ? '是' : '否' }}</span></div>
          <div class="queue-item"><span class="q-label">保留配额</span><span class="q-value">{{ queues.admission?.reserved_slots }}</span></div>
        </div>
      </section>
    </template>

    <!-- ============ 死信 ============ -->
    <template v-if="activeTab === 'dead-letters'">
      <div class="approval-hint">死信重放需有效 M21 审批（approval_id）。</div>
      <input v-model="approvalId" class="text-input approval-input" placeholder="审批 ID（重放时必填）" />
      <section class="panel">
        <table class="table">
          <thead>
            <tr><th>操作键</th><th>依赖</th><th>分类</th><th>尝试</th><th>状态</th><th>策略/代码版本</th><th>创建</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in deadLetters" :key="item.id">
              <td class="mono">{{ item.operation_key }}</td>
              <td>{{ item.dependency }}</td>
              <td>{{ item.error_classification }}</td>
              <td>{{ item.attempts }}</td>
              <td><span class="status-badge" :class="item.status">{{ item.status }}</span></td>
              <td class="muted">{{ item.policy_version }} / {{ item.code_version }}</td>
              <td class="muted">{{ fmt(item.created_at) }}</td>
              <td class="actions">
                <button class="btn small" :disabled="item.status === 'retrying' || !approvalId" @click="retryDeadLetter(item)">重放</button>
                <button class="btn small" :disabled="item.status === 'resolved'" @click="resolveDeadLetter(item)">解决</button>
              </td>
            </tr>
            <tr v-if="deadLetters.length === 0"><td colspan="8" class="muted center">暂无死信</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- ============ Kill Switch ============ -->
    <template v-if="activeTab === 'kill-switches'">
      <div class="approval-hint">开启/关闭 Kill Switch 需有效 M21 审批（approval_id）；同一审批只能用一次。</div>
      <input v-model="approvalId" class="text-input approval-input" placeholder="审批 ID（开关操作必填）" />
      <section class="panel">
        <table class="table">
          <thead><tr><th>作用域</th><th>目标</th><th>状态</th><th>原因</th><th>操作者</th><th>创建</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="ks in killSwitches" :key="ks.id">
              <td>{{ ks.scope }}</td>
              <td class="mono">{{ ks.target }}</td>
              <td><span class="status-badge" :class="ks.status">{{ ks.status === 'on' ? '开启' : '关闭' }}</span></td>
              <td class="muted">{{ ks.reason }}</td>
              <td class="muted">{{ ks.actor }}</td>
              <td class="muted">{{ fmt(ks.created_at) }}</td>
              <td class="actions">
                <button v-if="ks.status === 'off'" class="btn small danger" @click="openKillSwitchDialog('enable', ks.target)">开启</button>
                <button v-else class="btn small" @click="openKillSwitchDialog('disable', ks.target)">关闭</button>
              </td>
            </tr>
            <tr v-if="killSwitches.length === 0"><td colspan="7" class="muted center">暂无 Kill Switch 记录</td></tr>
          </tbody>
        </table>
      </section>

      <div v-if="actionTarget" class="modal-backdrop">
        <div class="modal">
          <h3>{{ actionKind === 'enable' ? '开启 Kill Switch' : '关闭 Kill Switch' }}：{{ actionTarget }}</h3>
          <input v-model="approvalId" class="text-input" placeholder="审批 ID（必填）" />
          <input v-model="actionReason" class="text-input" placeholder="原因（可选）" />
          <div v-if="actionError" class="error-box small">{{ actionError }}</div>
          <div class="modal-actions">
            <button class="btn" @click="actionTarget = null">取消</button>
            <button class="btn primary" @click="submitKillSwitch">确认</button>
          </div>
        </div>
      </div>
    </template>

    <!-- ============ 事故 ============ -->
    <template v-if="activeTab === 'incidents'">
      <section class="panel">
        <div v-for="incident in incidents" :key="incident.id" class="incident-card" :class="incident.severity">
          <div class="incident-top">
            <AlertTriangle :size="16" />
            <span class="incident-title">{{ incident.title }}</span>
            <span class="badge" :class="incident.severity">{{ incident.severity }}</span>
            <span class="badge" :class="incident.status">{{ incident.status === 'open' ? '进行中' : '已关闭' }}</span>
            <span class="incident-date">{{ fmt(incident.created_at) }}</span>
            <button v-if="incident.status === 'open'" class="btn small primary" @click="closeIncident(incident)">关闭</button>
          </div>
          <p v-if="incident.impact" class="incident-impact">{{ incident.impact }}</p>
          <p v-if="incident.timeline && incident.timeline.length" class="incident-meta">时间线 {{ incident.timeline.length }} 条 · 动作 {{ incident.actions?.length || 0 }} 条</p>
        </div>
        <div v-if="incidents.length === 0" class="muted center" style="padding: 24px;">暂无事故记录</div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 28px 32px 60px; max-width: 1200px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.page-title { font-size: 24px; font-weight: 700; margin: 0 0 4px; }
.page-subtitle { color: var(--text-muted); margin: 0; font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px;
  cursor: pointer; color: var(--text);
}
.btn.primary { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.btn.danger { background: var(--red); border-color: var(--red); color: #fff; }
.btn.ghost { background: transparent; }
.btn.small { padding: 4px 9px; font-size: 12px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.error-box.small { margin: 8px 0 0; }
.incident-form { display: flex; gap: 10px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
.text-input { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: 13px; background: var(--surface); color: var(--text); }
.text-input.wide { flex: 1; min-width: 200px; }
.approval-input { width: 320px; margin-bottom: 12px; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; }
.tabs { display: flex; gap: 8px; margin-bottom: 18px; flex-wrap: wrap; }
.tab {
  display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px;
  cursor: pointer; color: var(--text-muted);
}
.tab.active { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.health-summary { display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
.health-pill { font-size: 13px; padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border); }
.health-pill.healthy { background: rgba(16, 185, 129, 0.12); color: #047857; }
.health-pill.degraded { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.health-pill.outage { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.health-pill.auth { background: rgba(124, 108, 246, 0.12); color: #6d28d9; }
.health-pill.policy { background: rgba(100, 116, 139, 0.12); color: #475569; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; align-items: center; gap: 6px; margin: 0 0 12px; font-size: 14px; font-weight: 600; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.status-badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); }
.status-badge.healthy, .status-badge.closed, .status-badge.resolved { background: rgba(16, 185, 129, 0.12); color: #047857; }
.status-badge.degraded, .status-badge.half_open, .status-badge.retrying { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.status-badge.outage, .status-badge.open, .status-badge.pending { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.status-badge.auth_required, .status-badge.policy_denied { background: rgba(124, 108, 246, 0.12); color: #6d28d9; }
.status-badge.on { background: rgba(239, 68, 68, 0.15); color: #b91c1c; }
.status-badge.off { background: rgba(100, 116, 139, 0.12); color: #475569; }
.muted { color: var(--text-muted); }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.center { text-align: center; }
.actions { display: flex; gap: 6px; }
.queue-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.queue-item { background: var(--surface-muted); border-radius: 8px; padding: 10px 12px; }
.q-label { display: block; font-size: 12px; color: var(--text-muted); }
.q-value { font-size: 16px; font-weight: 600; }
.approval-hint { color: var(--text-muted); font-size: 12px; margin-bottom: 8px; }
.incident-card { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; margin-bottom: 10px; }
.incident-card.critical { border-left: 3px solid var(--red); }
.incident-card.warning { border-left: 3px solid var(--orange); }
.incident-card.info { border-left: 3px solid var(--cyan); }
.incident-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.incident-title { font-weight: 600; }
.incident-date { margin-left: auto; color: var(--text-soft); font-size: 12px; }
.incident-impact { margin: 8px 0 0; font-size: 13px; color: var(--text-muted); }
.incident-meta { margin: 4px 0 0; font-size: 12px; color: var(--text-soft); }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); }
.badge.critical { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.badge.warning { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.badge.info { background: rgba(37, 99, 235, 0.12); color: #1d4ed8; }
.badge.open { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.badge.closed { background: rgba(16, 185, 129, 0.12); color: #047857; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.4); display: grid; place-items: center; z-index: 50; }
.modal { background: var(--surface); border-radius: 14px; padding: 20px; width: 400px; display: flex; flex-direction: column; gap: 10px; }
.modal h3 { margin: 0; font-size: 16px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
