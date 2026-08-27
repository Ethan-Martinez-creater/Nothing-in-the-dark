<script setup lang="ts">
import { Bell, Link2, MailCheck, RefreshCw, Send } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { CaseRecord, DeliveryAttempt, NotificationEndpoint, NotificationEvent, Subscription } from '@/types/api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const cases = ref<CaseRecord[]>([])
const selectedCaseId = ref('')
const subscriptions = ref<Subscription[]>([])
const endpoints = ref<NotificationEndpoint[]>([])
const deliveries = ref<DeliveryAttempt[]>([])
const events = ref<NotificationEvent[]>([])
const activeTab = ref<'subscriptions' | 'endpoints' | 'deliveries' | 'share'>('subscriptions')

const newSubName = ref('')
const newSubSeverity = ref('warning')
const newSubChannel = ref('inbox')
const newEndpointName = ref('')
const newEndpointUrl = ref('')
const shareLink = ref('')
const shareTargetType = ref('report')
const shareTargetId = ref('')

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

async function createShare() {
  if (!shareTargetId.value.trim()) return
  try {
    const result = await api.createShareLink(selectedCaseId.value, {
      target_type: shareTargetType.value,
      target_id: shareTargetId.value.trim(),
      expires_in_hours: 72,
    })
    shareLink.value = result.token
    notice.value = '分享链接已生成'
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">调查结果订阅与外部协作</h1>
        <p class="page-subtitle">M13：订阅、Webhook 验证、投递记录与分享管理。</p>
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
    </div>

    <nav class="tabs">
      <button class="tab" :class="{ active: activeTab === 'subscriptions' }" @click="activeTab = 'subscriptions'">订阅</button>
      <button class="tab" :class="{ active: activeTab === 'endpoints' }" @click="activeTab = 'endpoints'">Webhook 端点</button>
      <button class="tab" :class="{ active: activeTab === 'deliveries' }" @click="activeTab = 'deliveries'">投递记录</button>
      <button class="tab" :class="{ active: activeTab === 'share' }" @click="activeTab = 'share'">分享</button>
    </nav>

    <div v-if="loading" class="empty-state">加载中…</div>
    <div v-else-if="!selectedCaseId" class="empty-state">请选择案件。</div>

    <!-- 订阅 -->
    <template v-if="activeTab === 'subscriptions'">
      <div class="create-row">
        <input v-model="newSubName" class="text-input" placeholder="订阅名称（可选）" />
        <select v-model="newSubSeverity" class="filter-select">
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="critical">critical</option>
        </select>
        <select v-model="newSubChannel" class="filter-select">
          <option value="inbox">站内信</option>
          <option value="webhook">Webhook</option>
        </select>
        <button class="btn primary small" :disabled="!selectedCaseId" @click="createSubscription"><Send :size="14" /> 创建订阅</button>
      </div>
      <section class="panel">
        <table class="table">
          <thead><tr><th>名称</th><th>频道</th><th>严重度</th><th>事件过滤</th><th>启用</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="sub in subscriptions" :key="sub.id">
              <td>{{ sub.name }}</td>
              <td>{{ CHANNEL_LABELS[sub.channel] || sub.channel }}</td>
              <td>{{ sub.severity }}</td>
              <td class="muted">{{ sub.event_filters.join(', ') || '全部' }}</td>
              <td><span class="status-badge" :class="sub.enabled ? 'on' : 'off'">{{ sub.enabled ? '启用' : '暂停' }}</span></td>
              <td><button class="btn small" @click="toggleSubscription(sub)">{{ sub.enabled ? '暂停' : '恢复' }}</button></td>
            </tr>
            <tr v-if="subscriptions.length === 0"><td colspan="6" class="muted center">暂无订阅</td></tr>
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h3 class="panel-title"><Bell :size="15" /> 通知事件（{{ events.length }}）</h3>
        <table class="table">
          <thead><tr><th>事件</th><th>类型</th><th>严重度</th><th>发生时间</th></tr></thead>
          <tbody>
            <tr v-for="ev in events.slice(0, 30)" :key="ev.id">
              <td class="mono">{{ ev.event_id }}</td>
              <td>{{ ev.event_type }}</td>
              <td>{{ ev.severity }}</td>
              <td class="muted">{{ fmt(ev.occurred_at) }}</td>
            </tr>
            <tr v-if="events.length === 0"><td colspan="4" class="muted center">暂无通知事件</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- Webhook 端点 -->
    <template v-if="activeTab === 'endpoints'">
      <div class="create-row">
        <input v-model="newEndpointName" class="text-input" placeholder="端点名称（可选）" />
        <input v-model="newEndpointUrl" class="text-input wide" placeholder="https://example.com/webhook" />
        <button class="btn primary small" @click="createEndpoint"><Send :size="14" /> 创建</button>
      </div>
      <section class="panel">
        <table class="table">
          <thead><tr><th>名称</th><th>URL</th><th>验证状态</th><th>启用</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="ep in endpoints" :key="ep.id">
              <td>{{ ep.name }}</td>
              <td class="mono muted">{{ ep.url }}</td>
              <td><span class="status-badge" :class="ep.verification_state">{{ ep.verification_state }}</span></td>
              <td>{{ ep.enabled ? '是' : '否' }}</td>
              <td><button class="btn small" @click="verifyEndpoint(ep)"><MailCheck :size="14" /> 验证</button></td>
            </tr>
            <tr v-if="endpoints.length === 0"><td colspan="5" class="muted center">暂无 Webhook 端点</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 投递记录 -->
    <template v-if="activeTab === 'deliveries'">
      <section class="panel">
        <table class="table">
          <thead><tr><th>事件</th><th>订阅</th><th>尝试</th><th>状态</th><th>HTTP</th><th>摘要</th><th>下次重试</th></tr></thead>
          <tbody>
            <tr v-for="d in deliveries" :key="d.id">
              <td class="mono muted">{{ d.event_id.slice(0, 8) }}…</td>
              <td class="mono muted">{{ d.subscription_id.slice(0, 8) }}…</td>
              <td>{{ d.attempt }}</td>
              <td><span class="status-badge" :class="d.status">{{ d.status }}</span></td>
              <td>{{ d.http_status ?? '—' }}</td>
              <td class="muted">{{ d.http_summary }}</td>
              <td class="muted">{{ fmt(d.next_retry_at) }}</td>
            </tr>
            <tr v-if="deliveries.length === 0"><td colspan="7" class="muted center">暂无投递记录</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 分享 -->
    <template v-if="activeTab === 'share'">
      <section class="panel">
        <h3 class="panel-title"><Link2 :size="15" /> 生成分享链接</h3>
        <div class="create-row">
          <select v-model="shareTargetType" class="filter-select">
            <option value="report">报告</option>
            <option value="artifact">产物</option>
            <option value="narrative">叙事</option>
          </select>
          <input v-model="shareTargetId" class="text-input wide" placeholder="目标 ID" />
          <button class="btn primary small" @click="createShare">生成</button>
        </div>
        <div v-if="shareLink" class="share-result">
          <span class="mono">分享 token：{{ shareLink }}</span>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 28px 32px 60px; max-width: 1080px; margin: 0 auto; }
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
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; color: var(--text); max-width: 340px; }
.text-input { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: 13px; background: var(--surface); color: var(--text); }
.text-input.wide { flex: 1; min-width: 200px; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tab { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px; cursor: pointer; color: var(--text-muted); }
.tab.active { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.create-row { display: flex; gap: 8px; margin-bottom: 14px; align-items: center; flex-wrap: wrap; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; align-items: center; gap: 6px; margin: 0 0 12px; font-size: 14px; font-weight: 600; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.status-badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); }
.status-badge.on, .status-badge.verified, .status-badge.delivered, .status-badge.completed { background: rgba(16, 185, 129, 0.12); color: #047857; }
.status-badge.off, .status-badge.pending, .status-badge.retrying { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.status-badge.failed { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.muted { color: var(--text-muted); }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.center { text-align: center; }
.share-result { margin-top: 12px; padding: 10px; background: var(--surface-muted); border-radius: 8px; border: 1px solid var(--border); word-break: break-all; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
</style>
