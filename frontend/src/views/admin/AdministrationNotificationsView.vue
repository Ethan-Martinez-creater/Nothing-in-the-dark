<script setup lang="ts">
// C9.3: Administration → Notifications。
// 订阅 / Webhook 端点 / 投递记录（自旧 SubscriptionsView 分流；share 移至
// Reports）。保留 case selector，后端 API 不变。
import { Bell, MailCheck, RefreshCw, Send } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type {
  CaseRecord,
  DeliveryAttempt,
  NotificationEndpoint,
  NotificationEvent,
  Subscription,
} from '@/types/api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const cases = ref<CaseRecord[]>([])
const selectedCaseId = ref('')
const subscriptions = ref<Subscription[]>([])
const endpoints = ref<NotificationEndpoint[]>([])
const deliveries = ref<DeliveryAttempt[]>([])
const events = ref<NotificationEvent[]>([])
const activeTab = ref<'subscriptions' | 'endpoints' | 'deliveries'>('subscriptions')

const newSubName = ref('')
const newSubSeverity = ref('warning')
const newSubChannel = ref('inbox')
const newEndpointName = ref('')
const newEndpointUrl = ref('')

const CHANNEL_LABELS: Record<string, string> = { inbox: '站内信', webhook: 'Webhook' }

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    cases.value = await api.listCases()
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
    const [subs, eps, dels, evts] = await Promise.all([
      api.listSubscriptions(selectedCaseId.value),
      api.listNotificationEndpoints(selectedCaseId.value),
      api.listDeliveries(selectedCaseId.value),
      api.listNotifications(selectedCaseId.value),
    ])
    subscriptions.value = subs.subscriptions
    endpoints.value = eps.endpoints
    deliveries.value = dels.deliveries
    events.value = evts.events
  } catch (e) {
    error.value = '订阅数据加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function createSubscription() {
  try {
    await api.createSubscription(selectedCaseId.value, {
      name: newSubName.value || undefined,
      severity: newSubSeverity.value,
      channel: newSubChannel.value,
    })
    notice.value = '订阅已创建'
    newSubName.value = ''
    await selectCase()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function toggleSubscription(sub: Subscription) {
  try {
    await api.setSubscriptionEnabled(selectedCaseId.value, sub.id, !sub.enabled)
    notice.value = sub.enabled ? '订阅已暂停' : '订阅已恢复'
    await selectCase()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function createEndpoint() {
  if (!newEndpointUrl.value.trim()) return
  try {
    await api.createNotificationEndpoint(selectedCaseId.value, {
      name: newEndpointName.value || newEndpointUrl.value,
      url: newEndpointUrl.value.trim(),
    })
    notice.value = 'Webhook 端点已创建，请验证'
    newEndpointUrl.value = ''
    await selectCase()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function verifyEndpoint(endpoint: NotificationEndpoint) {
  try {
    const result = await api.verifyNotificationEndpoint(selectedCaseId.value, endpoint.id)
    notice.value = '验证状态：' + result.verification_state
    await selectCase()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(load)
</script>

<template>
  <div class="anot">
    <div v-if="error" class="anot__error">{{ error }}</div>
    <div v-if="notice" class="anot__notice">{{ notice }}</div>

    <div class="anot__toolbar">
      <select v-model="selectedCaseId" class="anot__select" @change="selectCase">
        <option value="">选择调查…</option>
        <option v-for="c in cases" :key="c.id" :value="c.id">{{ c.title }}</option>
      </select>
      <button type="button" class="anot__btn" :disabled="loading" @click="load">
        <RefreshCw :size="14" /> 刷新
      </button>
    </div>

    <nav class="anot__tabs">
      <button
        class="anot__tab"
        :class="{ 'anot__tab--active': activeTab === 'subscriptions' }"
        @click="activeTab = 'subscriptions'"
      >
        订阅
      </button>
      <button
        class="anot__tab"
        :class="{ 'anot__tab--active': activeTab === 'endpoints' }"
        @click="activeTab = 'endpoints'"
      >
        Webhook 端点
      </button>
      <button
        class="anot__tab"
        :class="{ 'anot__tab--active': activeTab === 'deliveries' }"
        @click="activeTab = 'deliveries'"
      >
        投递记录
      </button>
    </nav>

    <div v-if="loading" class="anot__state">加载中…</div>
    <div v-else-if="!selectedCaseId" class="anot__state">请选择调查。</div>

    <!-- 订阅 -->
    <template v-else-if="activeTab === 'subscriptions'">
      <div class="anot__row">
        <input v-model="newSubName" class="anot__input" placeholder="订阅名称（可选）" />
        <select v-model="newSubSeverity" class="anot__input">
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="critical">critical</option>
        </select>
        <select v-model="newSubChannel" class="anot__input">
          <option value="inbox">站内信</option>
          <option value="webhook">Webhook</option>
        </select>
        <button type="button" class="anot__btn anot__btn--primary" :disabled="!selectedCaseId" @click="createSubscription">
          <Send :size="13" /> 创建订阅
        </button>
      </div>
      <section class="anot__panel">
        <table class="anot__table">
          <thead><tr><th>名称</th><th>频道</th><th>严重度</th><th>事件过滤</th><th>启用</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="sub in subscriptions" :key="sub.id">
              <td>{{ sub.name }}</td>
              <td>{{ CHANNEL_LABELS[sub.channel] || sub.channel }}</td>
              <td>{{ sub.severity }}</td>
              <td class="anot__muted">{{ sub.event_filters.join(', ') || '全部' }}</td>
              <td>{{ sub.enabled ? '启用' : '暂停' }}</td>
              <td><button type="button" class="anot__btn" @click="toggleSubscription(sub)">{{ sub.enabled ? '暂停' : '恢复' }}</button></td>
            </tr>
            <tr v-if="subscriptions.length === 0"><td colspan="6" class="anot__muted anot__center">暂无订阅</td></tr>
          </tbody>
        </table>
      </section>
      <section class="anot__panel">
        <h3 class="anot__panel-title"><Bell :size="14" /> 通知事件（{{ events.length }}）</h3>
        <table class="anot__table">
          <thead><tr><th>事件</th><th>类型</th><th>严重度</th><th>发生时间</th></tr></thead>
          <tbody>
            <tr v-for="ev in events.slice(0, 30)" :key="ev.id">
              <td class="anot__mono">{{ ev.event_id }}</td>
              <td>{{ ev.event_type }}</td>
              <td>{{ ev.severity }}</td>
              <td class="anot__muted">{{ fmt(ev.occurred_at) }}</td>
            </tr>
            <tr v-if="events.length === 0"><td colspan="4" class="anot__muted anot__center">暂无通知事件</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- Webhook 端点 -->
    <template v-else-if="activeTab === 'endpoints'">
      <div class="anot__row">
        <input v-model="newEndpointName" class="anot__input" placeholder="端点名称（可选）" />
        <input v-model="newEndpointUrl" class="anot__input anot__input--wide" placeholder="https://example.com/webhook" />
        <button type="button" class="anot__btn anot__btn--primary" @click="createEndpoint"><Send :size="13" /> 创建</button>
      </div>
      <section class="anot__panel">
        <table class="anot__table">
          <thead><tr><th>名称</th><th>URL</th><th>验证状态</th><th>启用</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="ep in endpoints" :key="ep.id">
              <td>{{ ep.name }}</td>
              <td class="anot__mono anot__muted">{{ ep.url }}</td>
              <td>{{ ep.verification_state }}</td>
              <td>{{ ep.enabled ? '是' : '否' }}</td>
              <td><button type="button" class="anot__btn" @click="verifyEndpoint(ep)"><MailCheck :size="13" /> 验证</button></td>
            </tr>
            <tr v-if="endpoints.length === 0"><td colspan="5" class="anot__muted anot__center">暂无 Webhook 端点</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 投递记录 -->
    <template v-else>
      <section class="anot__panel">
        <table class="anot__table">
          <thead><tr><th>订阅</th><th>事件</th><th>状态</th><th>时间</th><th>错误</th></tr></thead>
          <tbody>
            <tr v-for="d in deliveries" :key="d.id">
              <td>{{ d.subscription_id.slice(0, 8) }}</td>
              <td class="anot__mono">{{ d.event_id.slice(0, 8) }}</td>
              <td>{{ d.status }}</td>
              <td class="anot__muted">{{ fmt(d.attempted_at) }}</td>
              <td class="anot__muted">{{ d.last_error || '—' }}</td>
            </tr>
            <tr v-if="deliveries.length === 0"><td colspan="5" class="anot__muted anot__center">暂无投递记录</td></tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<style scoped>
.anot {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px 40px;
  max-width: 1080px;
  margin: 0 auto;
}

.anot__error {
  background: rgba(239, 68, 68, 0.08);
  color: #b91c1c;
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
}

.anot__notice {
  background: rgba(16, 185, 129, 0.1);
  color: #047857;
  border: 1px solid rgba(16, 185, 129, 0.3);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
}

.anot__toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.anot__select {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 6px 10px;
  font-size: 12px;
  color: var(--text);
  max-width: 340px;
}

.anot__row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.anot__input {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  background: var(--surface);
  color: var(--text);
}

.anot__input--wide {
  flex: 1;
  min-width: 200px;
}

.anot__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
}

.anot__btn--primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.anot__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.anot__tabs {
  display: flex;
  gap: 6px;
}

.anot__tab {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text-muted);
}

.anot__tab--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.anot__panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px;
}

.anot__panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 10px;
  font-size: 13px;
}

.anot__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.anot__table th {
  text-align: left;
  color: var(--text-muted);
  font-weight: 600;
  font-size: 11px;
  padding: 7px 8px;
  border-bottom: 1px solid var(--border);
}

.anot__table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--border);
}

.anot__muted { color: var(--text-muted); }
.anot__mono { font-family: ui-monospace, monospace; font-size: 11px; }
.anot__center { text-align: center; }

.anot__state {
  text-align: center;
  color: var(--text-soft);
  padding: 40px 0;
  font-size: 13px;
}
</style>
