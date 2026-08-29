<script setup lang="ts">
// Optimization V2 (M7.6 + C9.3)：全局报告中心。
// 按状态分组（draft/in_review/published/archived）；跳转调查报告页编辑；
// 分享（share link）从旧 SubscriptionsView 迁入 report 卡片。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/services/api'
import {
  reportApi,
  type ReportDocument,
  type ReportStatus,
} from '@/services/api/reports'

const router = useRouter()

const reports = ref<ReportDocument[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const statusFilter = ref<ReportStatus | ''>('')
const shareLinks = ref<Record<string, string>>({})
const shareBusy = ref<Record<string, boolean>>({})

const statusLabels: Record<ReportStatus, string> = {
  draft: '草稿',
  in_review: '审核中',
  published: '已发布',
  archived: '已归档',
}

const filtered = computed(() =>
  statusFilter.value
    ? reports.value.filter((item) => item.status === statusFilter.value)
    : reports.value,
)

async function load() {
  loading.value = true
  error.value = null
  try {
    reports.value = await reportApi.list()
  } catch {
    error.value = '报告加载失败，请重试。'
  } finally {
    loading.value = false
  }
}

function open(report: ReportDocument) {
  router.push(`/investigations/${report.case_id}/report`)
}

// C9.3: Report 分享 —— 生成 72h share link 并展示
async function share(report: ReportDocument) {
  if (shareBusy.value[report.id]) return
  shareBusy.value = { ...shareBusy.value, [report.id]: true }
  try {
    const result = await api.createShareLink(report.case_id, {
      target_type: 'report',
      target_id: report.id,
      expires_in_hours: 72,
    })
    shareLinks.value = { ...shareLinks.value, [report.id]: result.token }
  } catch {
    error.value = '分享链接生成失败，请重试。'
  } finally {
    shareBusy.value = { ...shareBusy.value, [report.id]: false }
  }
}

onMounted(load)
</script>

<template>
  <div class="rview">
    <header class="rview__head">
      <div>
        <h1 class="rview__title">报告</h1>
        <p class="rview__subtitle">草稿 → 审核 → 发布 → 归档</p>
      </div>
      <select v-model="statusFilter" class="rview__filter">
        <option value="">全部状态</option>
        <option v-for="(label, key) in statusLabels" :key="key" :value="key">
          {{ label }}
        </option>
      </select>
    </header>

    <p v-if="error" class="rview__error">{{ error }}</p>
    <p v-else-if="loading" class="rview__hint">正在加载…</p>
    <p v-else-if="filtered.length === 0" class="rview__hint">
      尚无报告 — 在调查的报告页从 Agent 报告 Artifact 创建草稿。
    </p>
    <ul v-else class="rview__list">
      <li v-for="report in filtered" :key="report.id">
        <button type="button" class="rview__card" @click="open(report)">
          <span class="rview__card-top">
            <span class="rview__status" :data-status="report.status">
              {{ statusLabels[report.status] }}
            </span>
            <span class="rview__version">v{{ report.lock_version }}</span>
          </span>
          <span class="rview__title">{{ report.title }}</span>
          <span class="rview__meta">
            更新于 {{ new Date(report.updated_at).toLocaleString('zh-CN') }}
          </span>
        </button>
        <div class="rview__share">
          <button
            type="button"
            class="rview__share-btn"
            :disabled="!!shareBusy[report.id]"
            @click.stop="share(report)"
          >
            {{ shareLinks[report.id] ? '重新生成分享' : '分享' }}
          </button>
          <code v-if="shareLinks[report.id]" class="rview__share-link">
            {{ shareLinks[report.id] }}
          </code>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.rview {
  max-width: 1080px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.rview__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.rview__title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.rview__subtitle {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}

.rview__filter {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  background: var(--surface);
}

.rview__error {
  margin: 0;
  color: var(--red);
  font-size: 13px;
}

.rview__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.rview__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}

.rview__share {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.rview__share-btn {
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
}

.rview__share-link {
  font-size: 11px;
  font-family: ui-monospace, monospace;
  color: var(--text-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}

.rview__card {
  width: 100%;
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

.rview__card:hover {
  border-color: var(--accent);
}

.rview__card-top {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}

.rview__status {
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  font-weight: 700;
}

.rview__status[data-status='published'] {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.rview__status[data-status='in_review'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.rview__version {
  color: var(--text-soft);
}

.rview__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.rview__meta {
  font-size: 11px;
  color: var(--text-soft);
}
</style>
