<script setup lang="ts">
import {
  FileAudio,
  FileVideo,
  Image as ImageIcon,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  X,
} from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { MediaAsset, MediaAssetDetail } from '@/types/api'

const props = defineProps<{
  caseId: string
  open: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const loading = ref(true)
const error = ref('')
const assets = ref<MediaAsset[]>([])
const details = ref<Record<string, MediaAssetDetail>>({})
const expanded = ref<Record<string, boolean>>({})
const backfilling = ref(false)
const actionError = ref('')

const PLATFORM_LABELS: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

const DOWNLOAD_LABELS: Record<string, string> = {
  not_downloaded: '未下载',
  downloading: '下载中',
  downloaded: '已下载',
  failed: '下载失败',
  skipped: '跳过',
}

const ANALYSIS_LABELS: Record<string, string> = {
  pending: '待分析',
  running: '分析中',
  partial: '部分完成',
  succeeded: '已完成',
  failed: '分析失败',
}

const C2PA_LABELS: Record<string, string> = {
  valid: '有效',
  invalid: '无效',
  not_present: '未检测到',
  unsupported: '无法验证',
  error: '检测错误',
}

function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] || platform
}

function typeIcon(mediaType: string) {
  if (mediaType === 'video') return FileVideo
  if (mediaType === 'audio') return FileAudio
  return ImageIcon
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    assets.value = await api.listMediaAssets(props.caseId)
  } catch {
    error.value = '加载媒体资产失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function toggle(asset: MediaAsset) {
  const next = !expanded.value[asset.id]
  expanded.value = { ...expanded.value, [asset.id]: next }
  if (next && !details.value[asset.id]) {
    try {
      details.value = {
        ...details.value,
        [asset.id]: await api.getMediaAsset(props.caseId, asset.id),
      }
    } catch {
      actionError.value = '加载资产详情失败。'
    }
  }
}

async function backfill() {
  if (backfilling.value) return
  backfilling.value = true
  actionError.value = ''
  try {
    await api.backfillMedia(props.caseId)
    await load()
  } catch {
    actionError.value = '回填失败。'
  } finally {
    backfilling.value = false
  }
}

onMounted(load)
</script>

<template>
  <aside v-if="open" class="media-panel" aria-label="媒体查看器">
    <header class="panel-header">
      <div class="panel-title">
        <ImageIcon :size="16" />
        <span>媒体查看器</span>
      </div>
      <button type="button" class="icon-button" aria-label="关闭" @click="emit('close')">
        <X :size="16" />
      </button>
    </header>

    <div class="panel-body">
      <div v-if="loading" class="state">
        <LoaderCircle :size="18" class="spin" />
        <span>加载中…</span>
      </div>
      <div v-else-if="error" class="state error">
        <span>{{ error }}</span>
        <button type="button" class="ghost-button" @click="load">重试</button>
      </div>
      <template v-else>
        <div class="toolbar">
          <button type="button" class="ghost-button" :disabled="backfilling" @click="backfill">
            <RefreshCw :size="14" :class="{ spin: backfilling }" />
            回填分析
          </button>
          <span class="count">{{ assets.length }} 项</span>
        </div>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>

        <ul v-if="assets.length" class="asset-list">
          <li v-for="asset in assets" :key="asset.id" class="asset-item">
            <button type="button" class="asset-head" @click="toggle(asset)">
              <component :is="typeIcon(asset.media_type)" :size="15" class="type-icon" />
              <span class="asset-meta">
                {{ platformLabel(asset.platform) }} · {{ asset.media_type }} ·
                {{ asset.mime_type || '未知类型' }}
              </span>
              <span class="status-badge" :class="asset.analysis_status">
                {{ ANALYSIS_LABELS[asset.analysis_status] || asset.analysis_status }}
              </span>
            </button>

            <div class="asset-info">
              <span class="status-badge" :class="asset.download_status">
                {{ DOWNLOAD_LABELS[asset.download_status] || asset.download_status }}
              </span>
              <span v-if="asset.byte_size" class="info-text">{{ asset.byte_size }} B</span>
              <span v-if="asset.width && asset.height" class="info-text">
                {{ asset.width }}×{{ asset.height }}
              </span>
              <span v-if="asset.c2pa_status" class="c2pa-badge" :class="asset.c2pa_status">
                <ShieldCheck :size="12" />
                C2PA {{ C2PA_LABELS[asset.c2pa_status] || asset.c2pa_status }}
              </span>
            </div>

            <div v-if="expanded[asset.id]" class="asset-detail">
              <div v-if="details[asset.id]?.transcripts.length">
                <h4>OCR / 字幕文本</h4>
                <p v-for="t in details[asset.id]!.transcripts" :key="t.id" class="transcript">
                  <span class="transcript-kind">{{ t.kind }}</span>
                  {{ t.full_text }}
                </p>
              </div>
              <div v-if="details[asset.id]?.ocr_text" class="detail-block">
                <h4>帖内 OCR</h4>
                <p>{{ details[asset.id]!.ocr_text }}</p>
              </div>
              <div class="detail-block">
                <h4>文件哈希</h4>
                <code class="hash">{{ asset.actual_sha256 || '（未下载，无真实哈希）' }}</code>
                <span class="info-text">（{{ asset.hash_kind }}）</span>
              </div>
              <div v-if="asset.error_code" class="detail-block error">
                错误：{{ asset.error_code }}
              </div>
              <p v-if="asset.c2pa_status === 'not_present'" class="c2pa-note">
                C2PA 未检测到只表示无来源完整性声明，不构成真实性负面推断。
              </p>
            </div>
          </li>
        </ul>
        <div v-else class="state">
          <ImageIcon :size="18" />
          <span>暂无媒体资产。</span>
        </div>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.media-panel {
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
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.count {
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.action-error {
  margin-bottom: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 13px;
}
.ghost-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid var(--color-border, #e2e8f0);
  background: transparent;
}
.ghost-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.asset-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.asset-item {
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 8px;
}
.asset-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 0;
  text-align: left;
}
.type-icon {
  color: var(--color-muted, #64748b);
}
.asset-meta {
  flex: 1;
  font-size: 13px;
}
.asset-info {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.status-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: var(--color-muted, #64748b);
}
.status-badge.downloaded,
.status-badge.succeeded {
  background: #dcfce7;
  color: #166534;
}
.status-badge.failed {
  background: #fee2e2;
  color: #991b1b;
}
.status-badge.partial,
.status-badge.downloading,
.status-badge.running {
  background: #fef9c3;
  color: #854d0e;
}
.info-text {
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.c2pa-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: var(--color-muted, #64748b);
}
.c2pa-badge.valid {
  background: #dcfce7;
  color: #166534;
}
.c2pa-badge.invalid {
  background: #fee2e2;
  color: #991b1b;
}
.asset-detail {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--color-border, #e2e8f0);
  font-size: 13px;
}
.asset-detail h4 {
  font-size: 12px;
  margin: 8px 0 4px;
  color: var(--color-muted, #64748b);
}
.detail-block.error {
  color: #dc2626;
}
.transcript {
  margin: 0 0 4px;
  line-height: 1.5;
}
.transcript-kind {
  display: inline-block;
  padding: 0 4px;
  border-radius: 3px;
  background: #f1f5f9;
  font-size: 11px;
  margin-right: 4px;
}
.hash {
  font-size: 11px;
  word-break: break-all;
  color: var(--color-muted, #64748b);
}
.c2pa-note {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--color-muted, #64748b);
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
