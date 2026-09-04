<script setup lang="ts">
// Optimization V2 (M6.3) + V3 §59：全局 Signal Inbox。
// 默认 open + acknowledged；服务端排序 critical 优先；动作委托 Alert 状态机。
// V3：双源合流（Monitor Alert + Derived Signal），Source filter 与
// detector 状态展示；inactive + resolved 显示「条件已消失」。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { signalApi, type Signal } from '@/services/api/signals'

const router = useRouter()

const signals = ref<Signal[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const severityFilter = ref('')
const statusFilter = ref('open,acknowledged')
const sourceFilter = ref('')
const selected = ref<Signal | null>(null)
const acting = ref(false)

const severityLabels: Record<string, string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
}

const statusLabels: Record<string, string> = {
  open: '未处理',
  acknowledged: '已确认',
  resolved: '已解决',
  suppressed: '已抑制',
}

const signalTypeLabels: Record<string, string> = {
  volume_spike: '讨论量激增',
  growth_spike: '增长异常',
  anomaly: '异常活动',
  key_actor: '重点账号',
  narrative_shift: '叙事变化',
  coordination_cluster: '协调行为',
  actor_recurrence: '主体复现',
  media_reuse: '媒体复用',
  cross_case_overlap: '跨调查重叠',
}

// V3 §59 Source filter：All / Monitor / Coordination / Actor recurrence /
// Media reuse / Cross-case overlap
const SOURCE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'monitor_alert', label: 'Monitor' },
  { value: 'coordination_cluster', label: 'Coordination' },
  { value: 'actor_recurrence', label: 'Actor recurrence' },
  { value: 'media_reuse', label: 'Media reuse' },
  { value: 'cross_case_overlap', label: 'Cross-case overlap' },
]

const sorted = computed(() => {
  const order: Record<string, number> = { critical: 0, warning: 1, info: 2 }
  return [...signals.value].sort(
    (a, b) =>
      (order[a.severity] ?? 3) - (order[b.severity] ?? 3) ||
      b.detected_at.localeCompare(a.detected_at),
  )
})

async function load() {
  loading.value = true
  error.value = null
  try {
    signals.value = await signalApi.list({
      status: statusFilter.value || undefined,
      severity: severityFilter.value || undefined,
      source_type: sourceFilter.value || undefined,
    })
  } catch {
    error.value = '信号加载失败，请重试。'
  } finally {
    loading.value = false
  }
}

function detectorCondition(signal: Signal): string | null {
  if (signal.detector_active === false) {
    // §59：inactive + resolved 显示「条件已消失」
    return signal.status === 'resolved' ? '条件已消失' : '检测条件当前不成立'
  }
  return null
}

async function act(signal: Signal, action: 'acknowledge' | 'resolve' | 'suppress') {
  if (acting.value) return
  acting.value = true
  try {
    const updated =
      action === 'acknowledge'
        ? await signalApi.acknowledge(signal.id)
        : action === 'resolve'
          ? await signalApi.resolve(signal.id)
          : await signalApi.suppress(signal.id)
    selected.value = updated
    await load()
  } catch {
    error.value = '操作失败，请重试。'
  } finally {
    acting.value = false
  }
}

function openInvestigation(signal: Signal) {
  router.push(`/investigations/${signal.case_id}/overview`)
}

onMounted(load)
</script>

<template>
  <div class="sigview">
    <header class="sigview__head">
      <div>
        <h1 class="sigview__title">信号</h1>
        <p class="sigview__subtitle">全局情报信号收件箱</p>
      </div>
      <div class="sigview__filters">
        <select v-model="statusFilter" class="sigview__filter" @change="load">
          <option value="open,acknowledged">未处理 + 已确认</option>
          <option value="open">未处理</option>
          <option value="resolved">已解决</option>
          <option value="">全部状态</option>
        </select>
        <select v-model="severityFilter" class="sigview__filter" @change="load">
          <option value="">全部级别</option>
          <option value="critical">严重</option>
          <option value="warning">警告</option>
          <option value="info">提示</option>
        </select>
        <select v-model="sourceFilter" class="sigview__filter" @change="load">
          <option v-for="option in SOURCE_OPTIONS" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>
    </header>

    <p v-if="error" class="sigview__error">{{ error }}</p>

    <div class="sigview__layout">
      <div class="sigview__feed">
        <p v-if="loading" class="sigview__hint">正在加载…</p>
        <p v-else-if="sorted.length === 0" class="sigview__hint">
          当前没有信号 — 为调查配置持续监测后，异常将出现在这里。
        </p>
        <button
          v-for="signal in sorted"
          :key="signal.id"
          type="button"
          class="sigview__card"
          :class="{ 'sigview__card--active': selected?.id === signal.id }"
          :data-severity="signal.severity"
          @click="selected = signal"
        >
          <span class="sigview__card-top">
            <span class="sigview__severity" :data-severity="signal.severity">
              {{ severityLabels[signal.severity] ?? signal.severity }}
            </span>
            <span class="sigview__type">
              {{ signalTypeLabels[signal.signal_type] ?? signal.signal_type }}
            </span>
            <span class="sigview__status">{{ statusLabels[signal.status] ?? signal.status }}</span>
          </span>
          <span class="sigview__card-title">{{ signal.title }}</span>
          <span class="sigview__card-meta">{{ signal.case_title }}</span>
        </button>
      </div>

      <aside v-if="selected" class="sigview__detail">
        <span class="sigview__severity" :data-severity="selected.severity">
          {{ severityLabels[selected.severity] ?? selected.severity }}
        </span>
        <h2>{{ selected.title }}</h2>
        <p v-if="detectorCondition(selected)" class="sigview__inactive">
          {{ detectorCondition(selected) }}
        </p>
        <p class="sigview__why">{{ selected.why_it_matters }}</p>
        <dl class="sigview__meta">
          <div>
            <dt>调查</dt>
            <dd>
              <button type="button" class="sigview__link" @click="openInvestigation(selected)">
                {{ selected.case_title }}
              </button>
            </dd>
          </div>
          <div><dt>类型</dt><dd>{{ signalTypeLabels[selected.signal_type] ?? selected.signal_type }}</dd></div>
          <div><dt>来源</dt><dd>{{ selected.source_label }}</dd></div>
          <div v-if="selected.confidence != null">
            <dt>置信度</dt>
            <dd>{{ selected.confidence.toFixed(2) }}</dd>
          </div>
          <div v-if="selected.detector_version">
            <dt>检测器</dt>
            <dd>{{ selected.detector_active ? 'active' : 'inactive' }} · {{ selected.detector_version }}</dd>
          </div>
          <div><dt>触发次数</dt><dd>{{ selected.trigger_count }}</dd></div>
          <div>
            <dt>首次发现</dt>
            <dd>{{ new Date(selected.detected_at).toLocaleString('zh-CN') }}</dd>
          </div>
        </dl>
        <div v-if="selected.related_case_ids.length" class="sigview__related">
          <h3>关联调查</h3>
          <button
            v-for="caseId in selected.related_case_ids"
            :key="caseId"
            type="button"
            class="sigview__link"
            @click="router.push(`/investigations/${caseId}/overview`)"
          >
            {{ caseId }}
          </button>
        </div>
        <div class="sigview__actions">
          <button
            v-if="selected.status === 'open'"
            type="button"
            class="sigview__act sigview__act--primary"
            :disabled="acting"
            @click="act(selected, 'acknowledge')"
          >
            确认
          </button>
          <button
            v-if="selected.status === 'open' || selected.status === 'acknowledged'"
            type="button"
            class="sigview__act"
            :disabled="acting"
            @click="act(selected, 'resolve')"
          >
            解决
          </button>
          <button
            v-if="selected.status !== 'suppressed'"
            type="button"
            class="sigview__act sigview__act--warn"
            :disabled="acting"
            @click="act(selected, 'suppress')"
          >
            抑制
          </button>
        </div>
      </aside>
      <aside v-else class="sigview__detail sigview__detail--empty">
        <p class="sigview__hint">从左侧选择一条信号查看详情。</p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.sigview {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sigview__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.sigview__title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.sigview__subtitle {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}

.sigview__filters {
  display: flex;
  gap: 8px;
}

.sigview__filter {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  background: var(--surface);
}

.sigview__error {
  margin: 0;
  color: var(--red);
  font-size: 13px;
}

.sigview__layout {
  display: grid;
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
  gap: 16px;
}

@media (max-width: 900px) {
  .sigview__layout {
    grid-template-columns: 1fr;
  }
}

.sigview__feed {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sigview__card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  cursor: pointer;
}

.sigview__card--active {
  border-color: var(--accent);
}

.sigview__card-top {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}

.sigview__severity {
  padding: 1px 8px;
  border-radius: 999px;
  font-weight: 700;
  background: var(--surface-strong);
  color: var(--text-muted);
}

.sigview__severity[data-severity='critical'] {
  background: rgba(239, 68, 68, 0.12);
  color: var(--red);
}

.sigview__severity[data-severity='warning'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.sigview__type,
.sigview__status {
  color: var(--text-soft);
}

.sigview__card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.sigview__card-meta {
  font-size: 12px;
  color: var(--text-muted);
}

.sigview__detail {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-self: start;
}

.sigview__detail--empty {
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

.sigview__detail h2 {
  margin: 0;
  font-size: 17px;
}

.sigview__why {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-muted);
}

.sigview__inactive {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: #b45309;
  padding: 4px 10px;
  border-radius: 8px;
  background: rgba(245, 158, 11, 0.12);
  align-self: flex-start;
}

.sigview__related {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}

.sigview__related h3 {
  margin: 0;
  font-size: 12px;
  color: var(--text-soft);
}

.sigview__meta {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sigview__meta div {
  display: flex;
  gap: 10px;
  font-size: 12px;
}

.sigview__meta dt {
  min-width: 64px;
  color: var(--text-soft);
}

.sigview__meta dd {
  margin: 0;
  color: var(--text);
}

.sigview__link {
  border: 0;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.sigview__actions {
  display: flex;
  gap: 8px;
}

.sigview__act {
  padding: 7px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
}

.sigview__act--primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.sigview__act--warn {
  border-color: rgba(239, 68, 68, 0.4);
  color: var(--red);
}

.sigview__hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}
</style>
