<script setup lang="ts">
// Async progressive collection run card（后台采集运行状态）。
// 展示 phase / 总状态 / 帖子评论计数 / 平台级进度 / 错误；支持取消与
// 阶段性分析（复制提示词，用户粘贴到 Copilot 发送，生成正常 User Turn）。
import { computed } from 'vue'
import { ClipboardCopy, LoaderCircle, X } from 'lucide-vue-next'

import type {
  CollectionRun,
  CollectionRunPlatform,
  CollectionRunStatus,
} from '@/services/api/collectionRuns'

const props = defineProps<{
  run: CollectionRun
}>()

const emit = defineEmits<{
  cancel: [runId: string]
  analyze: [prompt: string]
}>()

const PLATFORM_LABELS: Record<string, string> = {
  weibo: '微博',
  douyin: '抖音',
  bilibili: 'B站',
  zhihu: '知乎',
  tieba: '贴吧',
}

const TERMINAL_STATUSES: CollectionRunStatus[] = [
  'completed',
  'completed_with_errors',
  'failed',
  'cancelled',
]

function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] ?? platform
}

const statusLabel = computed(() => {
  const run = props.run
  switch (run.status) {
    case 'queued':
      return '等待采集'
    case 'running':
      return run.posts_collected > 0 ? '已有部分数据，继续采集中' : '正在采集'
    case 'completed':
      return '采集完成'
    case 'completed_with_errors':
      return '采集完成，部分平台失败'
    case 'failed':
      return '采集失败'
    case 'cancelled':
      return '已取消'
    default:
      return run.status
  }
})

const isTerminal = computed(() => TERMINAL_STATUSES.includes(props.run.status))

const elapsedText = computed(() => {
  const run = props.run
  const start = run.started_at ? new Date(run.started_at).getTime() : null
  if (!start) return ''
  const end = run.completed_at
    ? new Date(run.completed_at).getTime()
    : Date.now()
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分`
})

const sortedPlatforms = computed<{ platform: string; state: CollectionRunPlatform }[]>(() => {
  const progress = props.run.platform_progress ?? {}
  return props.run.platforms
    .map((platform) => ({ platform, state: progress[platform] }))
    .filter((item): item is { platform: string; state: CollectionRunPlatform } => !!item.state)
})

const canAnalyzePartial = computed(
  () => props.run.status === 'running' && props.run.posts_collected > 0,
)

const canAnalyzeFull = computed(() =>
  ['completed', 'completed_with_errors'].includes(props.run.status),
)

const PLATFORM_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '采集中',
  completed: '完成',
  failed: '失败',
  cancelled: '已取消',
}

async function copyAnalysisPrompt(prompt: string) {
  try {
    await navigator.clipboard.writeText(prompt)
    emit('analyze', prompt)
  } catch {
    // 剪贴板不可用时仍透出事件，由宿主提示
    emit('analyze', prompt)
  }
}

function promptForPartial(): string {
  return (
    `当前采集仍在进行中（已获取 ${props.run.posts_collected} 条数据，覆盖仍不完整）。` +
    `请基于已有部分数据做阶段性分析，并明确说明覆盖限制：${props.run.case_id}`
  )
}

function promptForFull(): string {
  const suffix =
    props.run.status === 'completed_with_errors'
      ? '（部分平台失败，分析时需说明覆盖限制）'
      : ''
  return `请基于当前采集结果继续分析${suffix}。`
}
</script>

<template>
  <section class="cruncard" aria-label="后台采集运行">
    <div class="cruncard__head">
      <div class="cruncard__title">
        <span class="cruncard__phase">{{ run.phase === 'deep' ? 'Deep' : 'Discovery' }}</span>
        <span class="cruncard__status" :data-status="run.status">{{ statusLabel }}</span>
      </div>
      <span v-if="elapsedText" class="cruncard__elapsed">{{ elapsedText }}</span>
    </div>

    <div class="cruncard__stats">
      <div class="cruncard__stat">
        <span class="cruncard__stat-value">{{ run.posts_collected }}</span>
        <span class="cruncard__stat-label">帖子</span>
      </div>
      <div class="cruncard__stat">
        <span class="cruncard__stat-value">{{ run.comments_collected }}</span>
        <span class="cruncard__stat-label">评论</span>
      </div>
      <div v-if="!isTerminal" class="cruncard__live">
        <LoaderCircle :size="13" class="cruncard__spinner" />
        后台采集中
      </div>
    </div>

    <ul class="cruncard__platforms">
      <li v-for="item in sortedPlatforms" :key="item.platform" class="cruncard__platform">
        <span class="cruncard__platform-name">{{ platformLabel(item.platform) }}</span>
        <span class="cruncard__platform-status" :data-status="item.state.status">
          {{ PLATFORM_STATUS_LABELS[item.state.status] ?? item.state.status }}
        </span>
        <span v-if="item.state.posts_collected > 0" class="cruncard__platform-posts">
          {{ item.state.posts_collected }} 条
        </span>
        <span v-if="item.state.attempts > 1" class="cruncard__platform-attempts">
          尝试 {{ item.state.attempts }} 次
        </span>
        <span
          v-if="item.state.error_message"
          class="cruncard__platform-error"
          :title="item.state.error_message"
        >
          {{ item.state.error_message }}
        </span>
      </li>
    </ul>

    <p v-if="run.error_message" class="cruncard__error">{{ run.error_message }}</p>

    <div class="cruncard__actions">
      <button
        v-if="canAnalyzePartial || canAnalyzeFull"
        type="button"
        class="cruncard__btn"
        @click="
          copyAnalysisPrompt(canAnalyzePartial ? promptForPartial() : promptForFull())
        "
      >
        <ClipboardCopy :size="13" />
        {{ canAnalyzePartial ? '分析已有数据' : '基于当前采集结果继续分析' }}
      </button>
      <button
        v-if="!isTerminal"
        type="button"
        class="cruncard__btn cruncard__btn--danger"
        @click="emit('cancel', run.id)"
      >
        <X :size="13" />
        取消采集
      </button>
    </div>
  </section>
</template>

<style scoped>
.cruncard {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.cruncard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.cruncard__title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cruncard__phase {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 999px;
  padding: 2px 8px;
}

.cruncard__status {
  font-size: 14px;
  font-weight: 600;
}

.cruncard__status[data-status='completed'],
.cruncard__status[data-status='completed_with_errors'] {
  color: var(--green, #15803d);
}

.cruncard__status[data-status='failed'] {
  color: #b91c1c;
}

.cruncard__status[data-status='cancelled'] {
  color: var(--text-muted);
}

.cruncard__elapsed {
  font-size: 12px;
  color: var(--text-muted);
}

.cruncard__stats {
  display: flex;
  align-items: center;
  gap: 16px;
}

.cruncard__stat {
  display: flex;
  align-items: baseline;
  gap: 5px;
}

.cruncard__stat-value {
  font-size: 18px;
  font-weight: 700;
}

.cruncard__stat-label {
  font-size: 12px;
  color: var(--text-muted);
}

.cruncard__live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-muted);
}

.cruncard__spinner {
  animation: cruncard-spin 1s linear infinite;
}

@keyframes cruncard-spin {
  to {
    transform: rotate(360deg);
  }
}

.cruncard__platforms {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.cruncard__platform {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.cruncard__platform-name {
  min-width: 52px;
  font-weight: 500;
}

.cruncard__platform-status {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--border);
  color: var(--text-muted);
}

.cruncard__platform-status[data-status='running'] {
  background: var(--cyan);
  color: #fff;
}

.cruncard__platform-status[data-status='completed'] {
  background: var(--green, #dcfce7);
  color: #15803d;
}

.cruncard__platform-status[data-status='failed'] {
  background: #fee2e2;
  color: #b91c1c;
}

.cruncard__platform-posts,
.cruncard__platform-attempts {
  font-size: 12px;
  color: var(--text-muted);
}

.cruncard__platform-error {
  font-size: 12px;
  color: #b91c1c;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cruncard__error {
  margin: 0;
  font-size: 12px;
  color: #b91c1c;
}

.cruncard__actions {
  display: flex;
  gap: 8px;
  margin-top: 2px;
}

.cruncard__btn {
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

.cruncard__btn--danger {
  color: #b91c1c;
  border-color: #fecaca;
}

.cruncard__btn:hover {
  border-color: var(--accent);
}
</style>
