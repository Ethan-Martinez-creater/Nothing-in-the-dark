<script setup lang="ts">
import {
  Bot,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Layers,
  Loader2,
  Navigation,
  PackageOpen,
  RotateCcw,
  TriangleAlert,
  Wrench,
  XCircle,
} from 'lucide-vue-next'
import { computed, nextTick, ref, watch } from 'vue'

import type { AgentRun, Artifact, ModelCallTrace, RunEvent, RunTrace, ToolCallTrace } from '@/types/api'

import MarkdownBody from './MarkdownBody.vue'

const props = defineProps<{
  run: AgentRun
  artifacts: Artifact[]
  trace: RunTrace | null
  traceLoading: boolean
  artifactsError: boolean
  caseId?: string
  // 内联 Harness 过程：SSE 实时增量（终态后由 trace 全量覆盖）。
  liveEvents?: RunEvent[]
  liveToolCalls?: ToolCallTrace[]
  liveModelCalls?: ModelCallTrace[]
  // 模型最终回答（run 完成后紧随的 assistant turn），Markdown 渲染为主体。
  finalContent?: string
}>()

const emit = defineEmits<{
  cancel: [runId: string]
  resume: [runId: string]
  loadTrace: [runId: string]
  retryArtifacts: [runId: string]
  askArtifact: [artifactId: string]
  enterSteer: [runId: string]
}>()

const agentLabels: Record<string, string> = {
  coordinator: '协调器',
  opinion: '观点专家',
  propagation: '传播专家',
  verification: '核查专家',
  evidence_critic: '证据批判',
  report: '报告专家',
  citation_validator: '引用校验',
}

const artifactKindLabels: Record<string, string> = {
  opinion_analysis: '舆情分析',
  propagation_reconstruction: '传播链路',
  fact_check: '事实核查',
  evidence_review: '证据评审',
  report: '完整报告',
  citation_validation: '引用校验',
}

// 工具调用面向用户的动作名（未命中显示原始名，MCP 工具截短前缀）。
const toolLabels: Record<string, string> = {
  search_social_evidence: '检索社交证据',
  collect_social_posts: '采集平台帖子',
  dispatch_expert: '委派专家',
  load_skill: '加载技能',
  write_case_memory: '写入案例记忆',
  get_artifact: '读取成果',
  classify_sentiment: '情感分类',
  query_claims: '查询主张',
  query_evidence: '查询证据',
  query_propagation: '查询传播',
  analyze_opinion: '观点分析',
  reconstruct_propagation: '传播重建',
  verify_claims: '主张核查',
  build_report: '生成报告',
  compare_platforms: '平台对比',
}

// 时间线里值得单独成行的运行事件（模型/工具调用已由专用行展示）。
const timelineEventTypes = new Set([
  'context_built',
  'expert_dispatched',
  'expert_completed',
  'expert_failed',
  'expert_artifact_created',
  'agent_error',
  'summary_failed',
  'tool_progress',
])

// 成果折叠：默认收起为摘要行，展开才渲染 ArtifactCard（长对话更易定位，
// 也避免 ECharts 图在未查看时挂载）。
const artifactsOpen = ref(false)

// 最终回答折叠：默认展开，点击标题栏收起（超长回答不再占据整个视口）。
const answerOpen = ref(true)

const artifactSummary = computed(() => {
  if (!props.artifacts.length) return ''
  const labels = props.artifacts
    .map((artifact) => artifactKindLabels[artifact.kind] || artifact.kind)
  const unique = [...new Set(labels)]
  return `成果 ${props.artifacts.length} 个：${unique.join('、')}`
})

const isActive = computed(() =>
  ['pending', 'running', 'waiting_approval'].includes(props.run.status),
)

// 过程展示源：终态用全量 trace（精确数据），运行中/未加载用实时增量。
const toolCalls = computed(() => props.trace?.tool_calls ?? props.liveToolCalls ?? [])
const modelCalls = computed(() => props.trace?.model_calls ?? props.liveModelCalls ?? [])
const runEvents = computed(() => props.trace?.events ?? props.liveEvents ?? [])

type TimelineEntry =
  | { key: string; kind: 'model'; at: string; call: ModelCallTrace }
  | { key: string; kind: 'tool'; at: string; call: ToolCallTrace }
  | { key: string; kind: 'event'; at: string; event: RunEvent }

// 思考时间线：模型调用（=思考）与工具调用按发生时间合并成单条流，
// ChatGPT 式「先看过程、再看回答」。
const timeline = computed<TimelineEntry[]>(() => {
  const entries: TimelineEntry[] = []
  for (const call of modelCalls.value) {
    entries.push({ key: `m:${call.id}`, kind: 'model', at: call.created_at, call })
  }
  for (const call of toolCalls.value) {
    entries.push({ key: `t:${call.id}`, kind: 'tool', at: call.started_at, call })
  }
  for (const event of runEvents.value) {
    if (!timelineEventTypes.has(event.event_type)) continue
    entries.push({ key: `e:${event.id}`, kind: 'event', at: event.created_at, event })
  }
  return entries.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0))
})

const stepCount = computed(() => modelCalls.value.length + toolCalls.value.length)

// 过程耗时（wall-clock）：首个步骤到最后一个步骤；不足两步时回退 run 生命周期。
const thinkSeconds = computed(() => {
  const stamps = timeline.value.map((entry) => new Date(entry.at).getTime()).filter((t) => !Number.isNaN(t))
  if (stamps.length >= 2) {
    const seconds = (Math.max(...stamps) - Math.min(...stamps)) / 1000
    if (seconds > 0) return seconds
  }
  const started = new Date(props.run.created_at).getTime()
  const ended = new Date(props.run.updated_at).getTime()
  const seconds = (ended - started) / 1000
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null
})

// 思考折叠：运行中默认展开，进入终态自动收起为一行摘要；用户手动切换后不再干预。
const processOpen = ref(isActive.value)
const userToggled = ref(false)

function toggleProcess() {
  userToggled.value = true
  processOpen.value = !processOpen.value
}

watch(isActive, (active) => {
  if (userToggled.value) return
  processOpen.value = active
})

// 终态 run 展开时若 trace 尚未加载则自动拉取，而不是先显示
// 「暂无执行记录」让用户再手动点一次加载按钮。
watch(processOpen, (open) => {
  if (open && needsTraceLoad.value) loadTrace()
})

// 时间线新增条目时贴底滚动（过程直播观感）；仅当视口本就贴近底部时跟随，
// 避免用户回看历史时被强制拉底（这也是过程区「跳动」感的来源之一）。
const timelineEl = ref<HTMLOListElement | null>(null)
watch(() => timeline.value.length, async () => {
  await nextTick()
  const el = timelineEl.value
  if (!el) return
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  if (nearBottom) el.scrollTop = el.scrollHeight
})

const stateLabel = computed(() => {
  const steps = stepCount.value
    ? ` · ${stepCount.value} 个步骤${thinkSeconds.value != null ? ` · ${fmtSeconds(thinkSeconds.value)}` : ''}`
    : ''
  switch (props.run.status) {
    case 'pending':
      return '排队等待执行…'
    case 'running':
      return '正在思考…'
    case 'waiting_approval':
      return '等待你的审批'
    case 'completed':
      return `已深度思考${steps}`
    case 'failed':
      return '执行失败'
    default:
      return '已取消'
  }
})

// 终态但 trace 尚未加载（历史 run / trace 拉取失败）时提供懒加载入口。
const needsTraceLoad = computed(
  () => !isActive.value && !props.trace && !props.traceLoading,
)

const showThinkPanel = computed(
  () => timeline.value.length > 0 || isActive.value || props.traceLoading || needsTraceLoad.value,
)

function loadTrace() {
  if (props.trace || props.traceLoading) return
  emit('loadTrace', props.run.id)
}

// ---- 行渲染辅助 ----

function isModelRunning(call: ModelCallTrace): boolean {
  return !call.latency_ms && ['started', 'running', 'pending'].includes(call.status)
}

type ToolState = 'running' | 'waiting' | 'ok' | 'failed' | 'cancelled'

function toolState(call: ToolCallTrace): ToolState {
  if (call.status === 'failed') return 'failed'
  if (call.status === 'cancelled' || call.status === 'rejected') return 'cancelled'
  if (call.status === 'waiting_approval') return 'waiting'
  if (call.status === 'completed' || call.status === 'success') return 'ok'
  return 'running'
}

function toolLabel(name: string): string {
  if (toolLabels[name]) return toolLabels[name]
  if (name.startsWith('mcp:')) {
    const short = name.split(':').slice(1).join(':')
    return `MCP · ${short}`
  }
  return name
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function toolSummary(call: ToolCallTrace): string {
  const parts: string[] = []
  if (call.input_summary) parts.push(truncate(call.input_summary, 76))
  if (call.output_summary) parts.push(`→ ${truncate(call.output_summary, 76)}`)
  return parts.join(' ')
}

function fmtMs(ms: number): string {
  if (!ms) return ''
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
  const minutes = Math.floor(ms / 60_000)
  return `${minutes}m${Math.round((ms % 60_000) / 1000)}s`
}

function fmtSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m${Math.round(seconds % 60)}s`
}

function modelTokens(call: ModelCallTrace): number {
  return call.input_tokens + call.output_tokens
}

function eventLabel(event: RunEvent): string {
  const payload = event.payload as Record<string, unknown>
  switch (event.event_type) {
    case 'context_built':
      return '组装上下文（案例 · 记忆 · 历史）'
    case 'expert_dispatched':
      return `委派专家 · ${String(payload.agent || '')}`
    case 'expert_completed':
      return `专家完成 · ${String(payload.agent || '')}`
    case 'expert_failed':
      return `专家失败 · ${String(payload.agent || '')}`
    case 'expert_artifact_created':
      return `产出成果 · ${artifactKindLabels[String(payload.artifact_kind)] || String(payload.artifact_kind || '')}`
    case 'agent_error': {
      const message = (payload.error as { message?: string } | undefined)?.message
      return `执行失败${message ? `：${message}` : ''}`
    }
    case 'summary_failed':
      return '对话摘要生成失败（不影响回答）'
    case 'tool_progress':
      return progressLabel(payload)
    default:
      return event.event_type
  }
}

const platformLabels: Record<string, string> = {
  weibo: '微博',
  douyin: '抖音',
  bilibili: 'B站',
  zhihu: '知乎',
  tieba: '贴吧',
}

function platformLabel(platform: unknown): string {
  return platformLabels[String(platform || '')] || String(platform || '')
}

// 串行采集的逐平台直播进度（tool_progress 事件）。
function progressLabel(payload: Record<string, unknown>): string {
  const platform = platformLabel(payload.platform)
  const attempt = Number(payload.attempt || 0)
  const suffix = attempt > 1 ? `（第 ${attempt} 次尝试）` : ''
  switch (String(payload.stage || '')) {
    case 'platform_start':
      return payload.phase === 'retry'
        ? `补采 ${platform}${suffix}…`
        : `正在采集 ${platform}${suffix}…`
    case 'platform_attempt_failed':
      return `${platform} 本次尝试失败`
    case 'platform_done': {
      const count = Number(payload.count || 0)
      return payload.count === 0
        ? `${platform} 未采到内容`
        : `${platform} 采集完成 · ${count} 条`
    }
    default:
      return '采集进度更新'
  }
}

const eventIcons: Record<string, typeof Layers> = {
  context_built: Layers,
  expert_dispatched: Navigation,
  expert_completed: Check,
  expert_failed: XCircle,
  expert_artifact_created: PackageOpen,
  agent_error: XCircle,
  summary_failed: TriangleAlert,
  tool_progress: Wrench,
}

function eventIcon(event: RunEvent) {
  return eventIcons[event.event_type] || Layers
}
</script>

<template>
  <div class="chat-bubble run-bubble" :class="`run-${run.status}`">
    <!-- ① 思考过程（ChatGPT 式折叠面板：运行中展开直播，完成后收起为一行摘要） -->
    <div v-if="showThinkPanel" class="think-panel" :class="{ open: processOpen }">
      <button type="button" class="think-toggle" @click="toggleProcess">
        <component :is="processOpen ? ChevronDown : ChevronRight" :size="14" class="think-caret" />
        <span class="think-agent"><Bot :size="13" />{{ agentLabels[run.agent] || run.agent }}</span>
        <span class="think-state" :class="`state-${run.status}`">
          <Loader2 v-if="run.status === 'running'" class="spin" :size="12" />
          <CircleDashed v-else-if="run.status === 'pending'" :size="12" />
          {{ stateLabel }}
        </span>
      </button>
      <div v-if="processOpen" class="think-body">
        <div v-if="traceLoading" class="think-hint">正在加载执行记录…</div>
        <ol v-else-if="timeline.length" ref="timelineEl" class="think-timeline">
          <li
            v-for="entry in timeline"
            :key="entry.key"
            class="think-row"
            :class="{
              'is-running': (entry.kind === 'model' && isModelRunning(entry.call)) || (entry.kind === 'tool' && toolState(entry.call) === 'running'),
              'is-failed': (entry.kind === 'tool' && toolState(entry.call) === 'failed') || (entry.kind === 'event' && entry.event.event_type === 'agent_error'),
            }"
          >
            <template v-if="entry.kind === 'model'">
              <Brain :size="13" class="row-icon" />
              <div class="row-main">
                <span class="row-title">{{ isModelRunning(entry.call) ? '正在思考…' : '思考' }}</span>
              </div>
              <span class="row-meta">
                <Loader2 v-if="isModelRunning(entry.call)" class="spin" :size="11" />
                <template v-else>{{ fmtMs(entry.call.latency_ms) }}<template v-if="modelTokens(entry.call)"> · {{ modelTokens(entry.call) }} tok</template></template>
              </span>
            </template>

            <template v-else-if="entry.kind === 'tool'">
              <Wrench :size="13" class="row-icon" />
              <div class="row-main">
                <span class="row-title">{{ toolLabel(entry.call.tool_name) }}</span>
                <span v-if="toolSummary(entry.call)" class="row-sub">{{ toolSummary(entry.call) }}</span>
                <span v-if="entry.call.rag && entry.call.rag.hit_count > 0" class="row-sub rag">
                  RAG 命中 {{ entry.call.rag.hit_count }} 条
                  <code v-for="mode in entry.call.rag.retrieval_modes" :key="mode" class="rag-mode">{{ mode }}</code>
                </span>
              </div>
              <span class="row-meta">
                <Loader2 v-if="toolState(entry.call) === 'running'" class="spin" :size="11" />
                <Check v-else-if="toolState(entry.call) === 'ok'" :size="12" class="meta-ok" />
                <XCircle v-else-if="toolState(entry.call) === 'failed'" :size="12" class="meta-bad" />
                <CircleDashed v-else :size="12" />
                <template v-if="entry.call.duration_ms">{{ fmtMs(entry.call.duration_ms) }}</template>
              </span>
            </template>

            <template v-else>
              <component :is="eventIcon(entry.event)" :size="13" class="row-icon" />
              <div class="row-main">
                <span class="row-title">{{ eventLabel(entry.event) }}</span>
              </div>
            </template>
          </li>
        </ol>
        <div v-else class="think-hint">{{ isActive ? '等待第一个执行步骤…' : '暂无执行记录' }}</div>
        <button v-if="needsTraceLoad" type="button" class="think-load" @click="loadTrace">
          <RotateCcw :size="12" />
          加载完整执行记录
        </button>
      </div>
    </div>

    <!-- ② 审批提示已上移到输入框上方（队列式，见 ChatInputBar） -->

    <!-- ③ 最终输出：加粗大一号正文，与上方过程区形成明显视觉层级；
         带折叠标题栏（默认展开，点击收起，避免超长回答占满整个视口） -->
    <div class="run-output">
      <button
        v-if="finalContent"
        type="button"
        class="answer-toggle"
        :aria-expanded="answerOpen"
        @click="answerOpen = !answerOpen"
      >
        <component :is="answerOpen ? ChevronDown : ChevronRight" :size="14" class="answer-caret" />
        <Bot :size="13" />
        <span>{{ agentLabels[run.agent] || run.agent }} · 回答</span>
      </button>
      <div v-if="finalContent" v-show="answerOpen" class="answer-body">
        <MarkdownBody :text="finalContent" class="run-answer" />
      </div>
      <div v-else-if="isActive" class="answer-pending">
        <span class="typing-dots"><i /><i /><i /></span>
        正在生成回答
      </div>
      <p v-if="run.error" class="run-error">{{ run.error }}</p>
    </div>

    <!-- ④ 底部弱化条：用量与运行操作 -->
    <div class="run-footer">
      <span v-if="run.input_tokens + run.output_tokens">{{ run.input_tokens + run.output_tokens }} tok</span>
      <span>¥ {{ run.estimated_cost.toFixed(2) }}</span>
      <span v-if="run.tool_call_count">{{ run.tool_call_count }} 次工具调用</span>
      <div class="run-actions">
        <button
          v-if="isActive && run.status !== 'waiting_approval'"
          type="button"
          class="ghost-button"
          title="向运行中的任务发送指令，将在下一轮模型上下文生效"
          @click="emit('enterSteer', run.id)"
        >
          <Navigation :size="13" />
          运行指令
        </button>
        <button
          v-if="isActive && run.status !== 'waiting_approval'"
          type="button"
          class="ghost-button danger"
          @click="emit('cancel', run.id)"
        >
          取消
        </button>
        <button
          v-if="run.status === 'waiting_approval'"
          type="button"
          class="ghost-button"
          @click="emit('resume', run.id)"
        >
          恢复
        </button>
      </div>
    </div>

    <div v-if="artifactsError" class="artifact-error">
      <span>Artifact 加载失败</span>
      <button type="button" class="ghost-button" @click="emit('retryArtifacts', run.id)">
        重试
      </button>
    </div>

    <template v-if="artifacts.length">
      <button
        type="button"
        class="artifact-summary"
        :title="artifactsOpen ? '收起成果' : '展开查看成果详情'"
        @click="artifactsOpen = !artifactsOpen"
      >
        <span>{{ artifactSummary }}</span>
        <component :is="artifactsOpen ? ChevronDown : ChevronRight" :size="14" />
      </button>
      <div v-if="artifactsOpen" class="artifact-list">
        <ArtifactCard
          v-for="artifact in artifacts"
          :key="artifact.id"
          :artifact="artifact"
          :case-id="caseId"
          @ask-artifact="emit('askArtifact', $event)"
        />
      </div>
    </template>
  </div>
</template>

<script lang="ts">
import ArtifactCard from '@/components/artifacts/ArtifactCard.vue'
</script>
