<script setup lang="ts">
// 平台账号管理：Web 扫码登录（Linux 服务器 headless 场景）、验证、退出。
import { onMounted, onUnmounted, ref } from 'vue'
import {
  CheckCircle2,
  CircleDashed,
  LogOut,
  QrCode,
  RefreshCw,
  XCircle,
  X,
} from 'lucide-vue-next'

import { api } from '@/services/api'
import type {
  PlatformAuthSessionDetail,
  PlatformAuthStatusItem,
} from '@/types/api'

const PLATFORM_LABELS: Record<string, string> = {
  weibo: '微博',
  bilibili: 'Bilibili',
  tieba: '贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

const STATUS_LABELS: Record<string, string> = {
  active: '已登录',
  invalid: '登录已失效',
  revoked: '未登录',
  missing: '未登录',
}

const TERMINAL_SESSION_STATUSES = ['authenticated', 'failed', 'expired', 'cancelled']

const items = ref<PlatformAuthStatusItem[]>([])
const loading = ref(false)
const error = ref('')

// 扫码登录 Modal 状态
const session = ref<PlatformAuthSessionDetail | null>(null)
const sessionPlatform = ref('')
const pollTimer = ref<number | null>(null)
const cancelledOnUnmount = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.listPlatformAuth()
    items.value = res.items
  } catch (e) {
    error.value = '平台状态加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function startLogin(platform: string) {
  error.value = ''
  try {
    const created = await api.createLoginSession(platform)
    sessionPlatform.value = platform
    session.value = { ...created, qr_code: null, error: null }
    startPolling(created.session_id)
  } catch (e) {
    error.value = '发起登录失败：' + (e instanceof Error ? e.message : String(e))
  }
}

function startPolling(sessionId: string) {
  stopPolling()
  pollTimer.value = window.setInterval(async () => {
    try {
      const detail = await api.getLoginSession(sessionId)
      session.value = detail
      if (TERMINAL_SESSION_STATUSES.includes(detail.status)) {
        stopPolling()
        if (detail.status === 'authenticated') {
          // 登录成功：自动关闭 Modal 并刷新列表（文档 Phase 6.5）。
          session.value = null
          await load()
        } else {
          // failed / expired 保留在 Modal 中展示，允许用户重试。
          session.value = detail
        }
      }
    } catch {
      stopPolling()
      session.value = null
    }
  }, 1000)
}

function stopPolling() {
  if (pollTimer.value !== null) {
    window.clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function closeModal() {
  const current = session.value
  if (
    current &&
    !TERMINAL_SESSION_STATUSES.includes(current.status) &&
    !cancelledOnUnmount.value
  ) {
    try {
      await api.cancelLoginSession(current.session_id)
    } catch {
      // 取消失败不阻塞关闭
    }
  }
  stopPolling()
  session.value = null
}

async function revoke(platform: string) {
  const label = PLATFORM_LABELS[platform] || platform
  if (!window.confirm(`确定退出 ${label} 的登录状态吗？将删除已保存的凭据。`)) return
  error.value = ''
  try {
    await api.revokePlatform(platform)
    await load()
  } catch (e) {
    error.value = '退出登录失败：' + (e instanceof Error ? e.message : String(e))
  }
}

async function validate(platform: string) {
  error.value = ''
  try {
    const res = await api.validatePlatform(platform)
    if (res.result === 'validation_not_implemented') {
      error.value = `${PLATFORM_LABELS[platform] || platform} 的独立验证暂未实现，以真实采集结果为准。`
    }
  } catch (e) {
    error.value = '验证失败：' + (e instanceof Error ? e.message : String(e))
  }
}

onMounted(load)
onUnmounted(() => {
  stopPolling()
  // 组件卸载（路由切换）时取消进行中的扫码会话，避免后端残留浏览器进程。
  if (session.value && !TERMINAL_SESSION_STATUSES.includes(session.value.status)) {
    cancelledOnUnmount.value = true
    void api.cancelLoginSession(session.value.session_id).catch(() => {})
  }
})
</script>

<template>
  <div class="pauth">
    <div class="pauth__head">
      <h2>平台账号</h2>
      <p class="pauth__hint">
        在服务器上直接扫码登录社交平台。登录态加密保存，采集时自动复用；
        Cookie 不会显示在页面上。
      </p>
    </div>

    <div v-if="error" class="pauth__error">{{ error }}</div>

    <div v-if="loading" class="pauth__state">加载中…</div>
    <div v-else class="pauth__list">
      <div v-for="item in items" :key="item.platform" class="pauth__card">
        <div class="pauth__info">
          <span class="pauth__name">{{ PLATFORM_LABELS[item.platform] || item.platform }}</span>
          <span class="pauth__code">{{ item.platform }}</span>
        </div>
        <div class="pauth__status">
          <component
            :is="item.status === 'active' ? CheckCircle2 : item.status === 'invalid' ? XCircle : CircleDashed"
            :size="14"
            class="pauth__status-icon"
            :class="`pauth__status-icon--${item.status}`"
          />
          <span>{{ STATUS_LABELS[item.status] || item.status }}</span>
          <span v-if="item.last_validated_at" class="pauth__meta">
            验证于 {{ item.last_validated_at.slice(0, 10) }}
          </span>
        </div>
        <div class="pauth__actions">
          <button
            v-if="item.status === 'active'"
            type="button"
            class="pauth__btn"
            title="发起轻量验证"
            @click="validate(item.platform)"
          >
            <RefreshCw :size="13" /> 验证
          </button>
          <button
            type="button"
            class="pauth__btn pauth__btn--primary"
            title="扫码登录"
            @click="startLogin(item.platform)"
          >
            <QrCode :size="13" /> {{ item.status === 'active' ? '重新登录' : '扫码登录' }}
          </button>
          <button
            v-if="item.status === 'active'"
            type="button"
            class="pauth__btn pauth__btn--danger"
            title="删除已保存凭据"
            @click="revoke(item.platform)"
          >
            <LogOut :size="13" /> 退出
          </button>
        </div>
      </div>
    </div>

    <!-- 扫码 Modal -->
    <div v-if="session" class="pauth__modal-backdrop" @click.self="closeModal">
      <div class="pauth__modal" role="dialog" aria-label="扫码登录">
        <button type="button" class="pauth__modal-close" aria-label="关闭" @click="closeModal">
          <X :size="16" />
        </button>
        <h3>{{ PLATFORM_LABELS[sessionPlatform] || sessionPlatform }} · 扫码登录</h3>

        <div v-if="session.status === 'starting'" class="pauth__modal-state">
          正在启动浏览器…
        </div>

        <div v-else-if="session.status === 'waiting_scan'" class="pauth__modal-qr">
          <img v-if="session.qr_code" :src="session.qr_code" alt="登录二维码" />
          <p>请使用手机客户端扫码，登录成功后自动继续。</p>
        </div>

        <div v-else-if="session.status === 'authenticated'" class="pauth__modal-state pauth__modal-state--ok">
          <CheckCircle2 :size="18" /> 登录成功，凭据已加密保存。
        </div>

        <div v-else-if="session.status === 'expired'" class="pauth__modal-state pauth__modal-state--bad">
          二维码已过期，请关闭后重新发起登录。
        </div>

        <div v-else class="pauth__modal-state pauth__modal-state--bad">
          登录失败：{{ session.error || session.status }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pauth {
  max-width: 720px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
}

.pauth__head h2 {
  margin: 0 0 4px;
  font-size: 18px;
}

.pauth__hint {
  margin: 0;
  color: var(--text-soft);
  font-size: 12px;
}

.pauth__error {
  background: rgba(239, 68, 68, 0.08);
  color: #b91c1c;
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
}

.pauth__state {
  text-align: center;
  color: var(--text-soft);
  padding: 24px 0;
  font-size: 13px;
}

.pauth__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pauth__card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
}

.pauth__info {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 130px;
}

.pauth__name {
  font-weight: 600;
  font-size: 14px;
}

.pauth__code {
  color: var(--text-soft);
  font-size: 11px;
}

.pauth__status {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  font-size: 12px;
  color: var(--text-muted);
}

.pauth__status-icon--active {
  color: var(--green, #10b981);
}

.pauth__status-icon--invalid {
  color: var(--red, #ef4444);
}

.pauth__meta {
  font-size: 11px;
  color: var(--text-soft);
}

.pauth__actions {
  display: flex;
  gap: 8px;
}

.pauth__btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
}

.pauth__btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.pauth__btn--primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.pauth__btn--primary:hover:not(:disabled) {
  color: #fff;
}

.pauth__btn--danger {
  color: var(--red);
}

.pauth__modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.pauth__modal {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 22px 26px;
  min-width: 320px;
  max-width: 420px;
  text-align: center;
}

.pauth__modal h3 {
  margin: 0 0 14px;
  font-size: 15px;
}

.pauth__modal-close {
  position: absolute;
  top: 10px;
  right: 10px;
  border: none;
  background: none;
  cursor: pointer;
  color: var(--text-muted);
}

.pauth__modal-qr img {
  width: 220px;
  height: 220px;
  border: 1px solid var(--border);
  border-radius: 10px;
}

.pauth__modal-qr p {
  color: var(--text-soft);
  font-size: 12px;
  margin: 10px 0 0;
}

.pauth__modal-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 120px;
  color: var(--text-muted);
  font-size: 13px;
}

.pauth__modal-state--ok {
  color: var(--green, #10b981);
}

.pauth__modal-state--bad {
  color: var(--red, #ef4444);
}
</style>
