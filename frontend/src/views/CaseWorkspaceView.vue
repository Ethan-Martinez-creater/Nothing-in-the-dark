<script setup lang="ts">
import { ArrowLeft, BarChart3, Bell, FileText, Gavel, GitCompare, Image as ImageIcon, Lock, MessagesSquare, ShieldAlert } from 'lucide-vue-next'
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AlignmentPanel from '@/components/alignment/AlignmentPanel.vue'
import ChatInputBar from '@/components/chat/ChatInputBar.vue'
import ChatThread from '@/components/chat/ChatThread.vue'
import DebatePanel from '@/components/debate/DebatePanel.vue'
import EvidenceSidebar from '@/components/evidence/EvidenceSidebar.vue'
import IntegrityPanel from '@/components/integrity/IntegrityPanel.vue'
import MediaPanel from '@/components/media/MediaPanel.vue'
import MonitoringPanel from '@/components/monitoring/MonitoringPanel.vue'
import VisualSidebar from '@/components/visual/VisualSidebar.vue'
import { api } from '@/services/api'
import type {
  AgentRun,
  ApprovalInfo,
  Artifact,
  CaseRecord,
  ChatItem,
  EvidenceSummary,
  RunEvent,
  RunStatus,
  SystemCapabilities,
  ToolCallTrace,
  TurnRecord,
} from '@/types/api'

const route = useRoute()
const router = useRouter()
// 侧栏案例列表由 App 持有：工作台加载/终态后通知外壳刷新，避免列表时间戳过期。
const refreshCases = inject<() => Promise<void>>('refreshCases', async () => {})

// caseId 跟随路由变化：切换会话时组件实例会被复用，必须用 computed
// 而不是 setup 一次性常量，否则对话框区域不会刷新为新的会话内容。
const caseId = computed(() => String(route.params.caseId ?? ''))

const currentCase = ref<CaseRecord | null>(null)
const capabilities = ref<SystemCapabilities | null>(null)
const chatItems = ref<ChatItem[]>([])
const loading = ref(true)
const sending = ref(false)
const error = ref('')
const retryAction = ref<(() => void) | null>(null)

// 当前关注的 run：用于运行指令目标与「等待审批」头标；不再持有实时数据。
const activeRun = ref<AgentRun | null>(null)

// 主区视图模式：对话 / 辩论，由顶部滑块切换，视图与滑块位置绑定。
const chatMode = ref<'chat' | 'debate'>('chat')

// 对话区最右侧面板：互斥，一次只开一个；重复点击关闭。
const activePanel = ref<'evidence' | 'viz' | 'monitoring' | 'media' | 'alignment' | 'integrity' | null>(null)
const evidenceOpen = computed(() => activePanel.value === 'evidence')
const vizOpen = computed(() => activePanel.value === 'viz')
const monitoringOpen = computed(() => activePanel.value === 'monitoring')
const mediaOpen = computed(() => activePanel.value === 'media')
const alignmentOpen = computed(() => activePanel.value === 'alignment')
const integrityOpen = computed(() => activePanel.value === 'integrity')

const evidenceSummary = ref<EvidenceSummary | null>(null)
let evidenceLoaded = false

const vizHintVisible = ref(false)

// 辩论前置条件：案例已采集到社交平台数据（有入库帖子）才允许滑块切到辩论。
const debateReady = ref(false)

async function refreshDebateReady() {
  try {
    const comparison = await api.getPlatformComparison(caseId.value)
    debateReady.value = comparison.participation.some((item) => item.posts > 0)
  } catch {
    // 查询失败保持现状（默认禁用），不阻断工作台主流程。
  }
}

// 输入区模式：steerTarget（运行指令）优先于 askTarget（Artifact 追问）。
const steerTarget = ref<AgentRun | null>(null)
const askTarget = ref<{ artifactId: string } | null>(null)

// 审批队列：全部 run 的待审批项按进入顺序排列，输入框上方一次展示队首。
const approvalQueue = computed(() => {
  const queue: { runId: string; run: AgentRun; approval: ApprovalInfo }[] = []
  for (const item of chatItems.value) {
    if (item.type !== 'run') continue
    for (const approval of item.approvals) {
      if (approval.status === 'pending') {
        queue.push({ runId: item.run.id, run: item.run, approval })
      }
    }
  }
  return queue
})

const approvalTarget = computed(() => {
  const first = approvalQueue.value[0]
  if (!first) return null
  return { ...first, queueCount: approvalQueue.value.length }
})
const steerNotice = ref('')
let steerNoticeTimer: number | null = null

const platformLabels: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

const realCrawl = computed(() => capabilities.value?.demo_mode === false)
const llmConfigured = computed(
  () => capabilities.value?.llm_configured ?? capabilities.value?.llm?.configured ?? true,
)

const ACTIVE_STATUSES = ['pending', 'running', 'waiting_approval'] as const

// ---------------- 通用错误提示（带重试） ----------------

function fail(message: string, retry?: () => void) {
  error.value = message
  retryAction.value = retry ?? null
}

// ---------------- 对话流重建 ----------------

function buildChatItems(
  turns: TurnRecord[],
  runs: AgentRun[],
  artifacts: Artifact[],
): ChatItem[] {
  const artifactsByRun = new Map<string, Artifact[]>()
  const orphanArtifacts: Artifact[] = []
  for (const artifact of artifacts) {
    if (artifact.run_id && runs.some((run) => run.id === artifact.run_id)) {
      const list = artifactsByRun.get(artifact.run_id) ?? []
      list.push(artifact)
      artifactsByRun.set(artifact.run_id, list)
    } else {
      orphanArtifacts.push(artifact)
    }
  }

  const items: ChatItem[] = []
  // 被专家子 run 直接关联的回答 turn（turn.role === 'assistant' 且该 run
  // 的 turn_id 指向它）：顶层 run 的最终回答必须跳过这些 turn，向后找
  // 第一个未被任何 run 关联的 assistant turn。
  const turnById = new Map(turns.map((turn) => [turn.id, turn]))
  const runLinkedAssistantIds = new Set(
    runs
      .filter((candidate) => candidate.turn_id && turnById.get(candidate.turn_id)?.role === 'assistant')
      .map((candidate) => candidate.turn_id),
  )
  const consumedIndexes = new Set<number>()
  for (let index = 0; index < turns.length; index += 1) {
    if (consumedIndexes.has(index)) continue
    const turn = turns[index]
    if (!turn) continue
    const run = runs.find((candidate) => candidate.turn_id === turn.id)
    if (run) {
      // 最终回答合并进 run 卡片顶部（Markdown）：
      // - 专家子 run：turn_id 指向它自己的回答 turn（graph worker 完成时
      //   回写），该 turn 本身就是 finalContent；
      // - 顶层 run（协调器）：turn_id 指向用户指令 turn，向后找第一个未被
      //   专家 run 关联的 assistant turn 作为回答。只标记该回答 turn 已
      //   消费，中间的专家回答 turn 仍留给主循环正常生成卡片。
      let finalContent: string | undefined
      if (turn.role === 'assistant') {
        finalContent = turn.content
      } else {
        let nextIndex = index + 1
        while (nextIndex < turns.length) {
          const candidate = turns[nextIndex]
          if (!candidate) break
          if (candidate.role === 'assistant' && !runLinkedAssistantIds.has(candidate.id)) {
            finalContent = candidate.content
            consumedIndexes.add(nextIndex)
            break
          }
          nextIndex += 1
        }
      }
      items.push({
        type: 'run',
        run,
        artifacts: artifactsByRun.get(run.id) ?? [],
        approvals: [],
        trace: null,
        traceLoading: false,
        liveEvents: [],
        liveToolCalls: [],
        liveModelCalls: [],
        finalContent,
      })
    } else {
      items.push({ type: 'turn', turn })
    }
  }
  // 兜底：没有关联 turn 的 run（当前后端 turn/run 一对一，正常不会出现）
  for (const run of runs) {
    if (!run.turn_id || !turns.some((turn) => turn.id === run.turn_id)) {
      items.push({
        type: 'run',
        run,
        artifacts: artifactsByRun.get(run.id) ?? [],
        approvals: [],
        trace: null,
        traceLoading: false,
        liveEvents: [],
        liveToolCalls: [],
        liveModelCalls: [],
      })
    }
  }
  if (orphanArtifacts.length) {
    items.push({ type: 'orphan-artifacts', artifacts: orphanArtifacts })
  }
  return items
}

function runItem(runId: string): Extract<ChatItem, { type: 'run' }> | undefined {
  return chatItems.value.find(
    (candidate): candidate is Extract<ChatItem, { type: 'run' }> =>
      candidate.type === 'run' && candidate.run.id === runId,
  )
}

function makeRunItem(run: AgentRun): Extract<ChatItem, { type: 'run' }> {
  return {
    type: 'run',
    run,
    artifacts: [],
    approvals: [],
    trace: null,
    traceLoading: false,
    liveEvents: [],
    liveToolCalls: [],
    liveModelCalls: [],
  }
}

async function refreshArtifactsForRun(runId: string) {
  try {
    const caseArtifacts = await api.listArtifacts(caseId.value)
    const item = runItem(runId)
    if (item) {
      item.artifacts = caseArtifacts.filter((artifact) => artifact.run_id === runId)
      item.artifactsError = false
    }
  } catch {
    const item = runItem(runId)
    if (item) {
      item.artifactsError = true
    }
  }
}

function retryArtifacts(runId: string) {
  const item = runItem(runId)
  if (item) {
    item.artifactsError = false
  }
  void refreshArtifactsForRun(runId)
}

// 专家子 run 产出成果后，重建对话流让「专家卡片 + 成果」动态出现。
let chatRefreshTimer: number | undefined

async function refreshChatItems() {
  try {
    const [turns, runs, caseArtifacts] = await Promise.all([
      api.listTurns(caseId.value),
      api.listCaseRuns(caseId.value),
      api.listArtifacts(caseId.value),
    ])
    const rebuilt = buildChatItems(turns, runs, caseArtifacts)
    // 保留旧项中已展开 trace 的懒加载状态与审批卡本地态
    const previous = new Map(
      chatItems.value
        .filter((item) => item.type === 'run')
        .map((item) => [item.run.id, item]),
    )
    for (const item of rebuilt) {
      if (item.type === 'run') {
        const old = previous.get(item.run.id)
        if (old && old.type === 'run') {
          item.trace = old.trace
          item.traceLoading = old.traceLoading
          if (old.trace || old.traceLoading) {
            item.artifactsError = old.artifactsError
          }
          item.liveEvents = old.liveEvents
          item.liveToolCalls = old.liveToolCalls
          item.liveModelCalls = old.liveModelCalls
          // 审批状态跨重建保留：SSE 不会重发已消费事件，清空会让
          // 审批队列在每次对话流重建时闪烁消失。
          item.approvals = old.approvals
        }
      }
    }
    chatItems.value = rebuilt
  } catch {
    // 重建失败不阻断：后续事件还会再次触发
  }
}

function scheduleChatRefresh() {
  if (chatRefreshTimer) window.clearTimeout(chatRefreshTimer)
  chatRefreshTimer = window.setTimeout(() => void refreshChatItems(), 1200)
}

async function loadWorkspace() {
  loading.value = true
  try {
    const [caseRecord, turns, runs, caseArtifacts, systemCapabilities] = await Promise.all([
      api.getCase(caseId.value),
      api.listTurns(caseId.value),
      api.listCaseRuns(caseId.value),
      api.listArtifacts(caseId.value),
      api.getCapabilities(),
    ])
    currentCase.value = caseRecord
    capabilities.value = systemCapabilities
    chatItems.value = buildChatItems(turns, runs, caseArtifacts)
    error.value = ''
    retryAction.value = null
    for (const item of chatItems.value) {
      if (
        item.type === 'run'
        && item.run.status === 'waiting_approval'
        && item.approvals.length === 0
      ) {
        void loadTrace(item.run.id)
      }
    }
    void refreshDebateReady()
    void refreshCases()
  } catch {
    fail('无法加载案例，请检查后端服务。', loadWorkspace)
  } finally {
    loading.value = false
  }
}

// 切换会话时清空当前会话的全部本地状态，避免旧会话数据残留。
function resetWorkspaceState() {
  disconnectAll()
  if (chatRefreshTimer) {
    window.clearTimeout(chatRefreshTimer)
    chatRefreshTimer = undefined
  }
  if (steerNoticeTimer) {
    window.clearTimeout(steerNoticeTimer)
    steerNoticeTimer = null
  }
  chatItems.value = []
  currentCase.value = null
  capabilities.value = null
  activeRun.value = null
  activePanel.value = null
  steerTarget.value = null
  askTarget.value = null
  steerNotice.value = ''
  evidenceSummary.value = null
  evidenceLoaded = false
  vizHintVisible.value = false
  debateReady.value = false
  chatMode.value = 'chat'
  error.value = ''
  retryAction.value = null
}

// 路由切换（同一组件实例复用）：重置状态并按新 caseId 重载。
watch(
  () => route.params.caseId,
  () => {
    resetWorkspaceState()
    void loadWorkspace()
  },
)

// ---------------- 发送 ----------------

function quickInstruction(): string {
  const caseRecord = currentCase.value
  if (!caseRecord) return ''
  const platforms = caseRecord.platforms.map((platform) => platformLabels[platform] || platform).join('、')
  const timeRange = caseRecord.time_range?.start
    ? `，时间范围：${caseRecord.time_range.start} 至 ${caseRecord.time_range.end}`
    : ''
  return (
    `请对案例「${caseRecord.title}」（主题：${caseRecord.topic}，平台：${platforms}${timeRange}）`
    + '执行完整舆情分析。包含事实核查。最高预算 ¥5。'
  )
}

async function sendMessage(content: string, approveCrawl: boolean, artifactId?: string) {
  if (!llmConfigured.value) {
    fail('未配置 LLM。请填写 backend/.env 的 LLM_API_KEY 与 LLM_FAST_MODEL 后重启后端。')
    return
  }
  sending.value = true
  error.value = ''
  try {
    const run = await api.sendMessage(caseId.value, content, approveCrawl, artifactId)
    chatItems.value.push(makeRunItem(run))
    askTarget.value = null
  } catch {
    fail(
      '消息发送失败，请检查后端服务后重试。',
      () => void sendMessage(content, approveCrawl, artifactId),
    )
  } finally {
    sending.value = false
  }
}

// ---------------- Steering 与 Artifact 追问 ----------------

async function steerRun(runId: string, content: string) {
  sending.value = true
  error.value = ''
  try {
    await api.steerRun(runId, content)
    steerTarget.value = null
    steerNotice.value = '运行指令已发送，将在下一轮模型上下文生效'
    if (steerNoticeTimer) window.clearTimeout(steerNoticeTimer)
    steerNoticeTimer = window.setTimeout(() => {
      steerNotice.value = ''
    }, 4000)
  } catch {
    fail('指令发送失败，请重试。', () => void steerRun(runId, content))
  } finally {
    sending.value = false
  }
}

function askArtifact(artifactId: string) {
  askTarget.value = { artifactId }
  steerNotice.value = ''
}

function cancelSteer() {
  steerTarget.value = null
  steerNotice.value = ''
}

// 从 run 卡片的「运行指令」按钮显式进入指令模式。
function enterSteerFromBubble(runId: string) {
  const item = runItem(runId)
  if (!item) return
  if (ACTIVE_STATUSES.includes(item.run.status as (typeof ACTIVE_STATUSES)[number])) {
    steerTarget.value = item.run
    askTarget.value = null
    steerNotice.value = ''
  }
}

function quickAnalyze() {
  if (!llmConfigured.value) {
    fail('未配置 LLM。请填写 backend/.env 的 LLM_API_KEY 与 LLM_FAST_MODEL 后重启后端。')
    return
  }
  void sendMessage(quickInstruction(), true)
}

// ---------------- 空状态引导接线 ----------------

const inputBarRef = ref<InstanceType<typeof ChatInputBar> | null>(null)

// 首次使用引导：案例从未产生任何 run 时显示欢迎卡片。
const guideVisible = computed(() => !chatItems.value.some((item) => item.type === 'run'))

function fillInput(content: string) {
  inputBarRef.value?.fill?.(content)
}

function openEvidenceFromGuide() {
  activePanel.value = 'evidence'
  void loadEvidenceSummary()
}

// ---------------- 右侧面板（证据 / 可视化）互斥开关 ----------------

function togglePanel(panel: 'evidence' | 'viz' | 'monitoring' | 'media' | 'alignment' | 'integrity') {
  activePanel.value = activePanel.value === panel ? null : panel
  if (activePanel.value === 'evidence') void loadEvidenceSummary()
}

// ---------------- per-run SSE 订阅与实时内联 ----------------

interface RunSubscription {
  source: EventSource | null
  cursor: number
  pollTimer: number | null
}

// 每个活跃 run 一条独立订阅（含游标与轮询兜底）；终态后由 trace 覆盖。
const subscriptions = new Map<string, RunSubscription>()
let syncingSubscriptions = false

function openEventStream(runId: string) {
  const sub = subscriptions.get(runId) ?? { source: null, cursor: 0, pollTimer: null }
  subscriptions.set(runId, sub)
  sub.source = new EventSource(api.runEventStreamUrl(runId, sub.cursor))
  sub.source.onmessage = (message: MessageEvent) => {
    const event = JSON.parse(message.data) as RunEvent
    ingestRunEvent(runId, event)
  }
  sub.source.onerror = () => {
    sub.source?.close()
    sub.source = null
    startPolling(runId)
  }
}

function ingestRunEvent(runId: string, event: RunEvent) {
  const sub = subscriptions.get(runId)
  if (!sub) return
  if (event.id <= sub.cursor) return
  sub.cursor = event.id
  const item = runItem(runId)
  if (!item) return
  item.liveEvents.push(event)
  applyEventState(item, event)
}

// 单条事件驱动 run 状态机 + 审批卡 + 成果刷新 + 工具/模型调用增量。
function applyEventState(item: Extract<ChatItem, { type: 'run' }>, event: RunEvent) {
  const eventPayload = event.payload as Record<string, unknown>

  if (event.event_type === 'agent_queued' && item.run.status === 'pending') {
    item.run.status = 'running'
    if (activeRun.value?.id === item.run.id) activeRun.value.status = 'running'
  }
  if (['agent_end', 'agent_error'].includes(event.event_type)) {
    item.run.status = (event.status as RunStatus) ?? 'completed'
  }
  if (['approval_pending', 'approval_required'].includes(event.event_type)) {
    const approvalId = String(eventPayload.approval_id || '')
    if (approvalId && !item.approvals.some((approval) => approval.id === approvalId)) {
      item.approvals.push({
        id: approvalId,
        action: String(eventPayload.action || ''),
        reason: String(eventPayload.reason || ''),
        status: 'pending',
        request_payload: (eventPayload.request_payload as Record<string, unknown>) || {},
      })
    }
    if (activeRun.value?.id === item.run.id) {
      activeRun.value.status = 'waiting_approval'
    }
  }
  if (
    event.event_type === 'expert_artifact_created'
    || event.event_type === 'expert_dispatched'
    || event.event_type === 'expert_completed'
    || event.event_type === 'expert_failed'
  ) {
    void refreshArtifactsForRun(item.run.id)
    // 委派开始/结束：重建对话流，让子 run 卡片与成果动态出现。
    scheduleChatRefresh()
  }

  if (event.event_type === 'tool_execution_start') {
    const callId = event.tool_call_id
    if (callId && !item.liveToolCalls.some((call) => call.id === callId)) {
      item.liveToolCalls.push({
        id: callId,
        run_id: item.run.id,
        tool_name: event.tool || 'unknown',
        skill_name: event.skill,
        status: event.status,
        arguments: {},
        result: {},
        error_code: null,
        input_summary: null,
        output_summary: null,
        retry_count: 0,
        duration_ms: 0,
        estimated_cost: 0,
        idempotency_key: null,
        approval_id: null,
        rag: null,
        started_at: event.created_at,
        finished_at: null,
      })
    }
  } else if (event.event_type === 'tool_execution_end') {
    const call = item.liveToolCalls.find((candidate) => candidate.id === event.tool_call_id)
    if (call) {
      call.status = event.status
      if (typeof eventPayload.duration_ms === 'number') call.duration_ms = eventPayload.duration_ms
      if (typeof eventPayload.estimated_cost === 'number') call.estimated_cost = eventPayload.estimated_cost
      if (typeof eventPayload.output_summary === 'string') call.output_summary = eventPayload.output_summary
      if (typeof eventPayload.input_summary === 'string') call.input_summary = eventPayload.input_summary
      if (eventPayload.rag && typeof eventPayload.rag === 'object') call.rag = eventPayload.rag as ToolCallTrace['rag']
      call.finished_at = event.created_at
    }
  } else if (event.event_type === 'model_call_start') {
    const callId = event.tool_call_id
    if (callId && !item.liveModelCalls.some((call) => call.id === callId)) {
      item.liveModelCalls.push({
        id: callId,
        run_id: item.run.id,
        model:
          typeof eventPayload.model === 'string'
            ? eventPayload.model
            : event.skill || 'unknown',
        route: String(eventPayload.route || ''),
        status: event.status,
        input_tokens: 0,
        cached_input_tokens: 0,
        output_tokens: 0,
        estimated_cost: 0,
        currency: 'CNY',
        pricing_model: null,
        latency_ms: 0,
        error_code: null,
        created_at: event.created_at,
      })
    }
  } else if (event.event_type === 'model_call_end') {
    const call = item.liveModelCalls.find((candidate) => candidate.id === event.tool_call_id)
    if (call) {
      call.status = event.status
      if (typeof eventPayload.input_tokens === 'number') call.input_tokens = eventPayload.input_tokens
      if (typeof eventPayload.output_tokens === 'number') call.output_tokens = eventPayload.output_tokens
      if (typeof eventPayload.cached_input_tokens === 'number') call.cached_input_tokens = eventPayload.cached_input_tokens
      if (typeof eventPayload.estimated_cost === 'number') call.estimated_cost = eventPayload.estimated_cost
      if (typeof eventPayload.latency_ms === 'number') call.latency_ms = eventPayload.latency_ms
    }
  }
}

function startPolling(runId: string) {
  const sub = subscriptions.get(runId)
  if (!sub) return
  if (sub.pollTimer) window.clearInterval(sub.pollTimer)
  sub.pollTimer = window.setInterval(async () => {
    try {
      const fresh = await api.listRunEvents(runId, sub.cursor)
      for (const event of fresh) ingestRunEvent(runId, event)
      const run = await api.getRun(runId)
      if (!ACTIVE_STATUSES.includes(run.status as (typeof ACTIVE_STATUSES)[number])) {
        if (sub.pollTimer) {
          window.clearInterval(sub.pollTimer)
          sub.pollTimer = null
        }
        await finalizeRun(run)
        return
      }
      if (fresh.length === 0) {
        // SSE 断线后没有新事件：重建 SSE 流（事件 id 幂等，不会重复消费）
        if (sub.pollTimer) {
          window.clearInterval(sub.pollTimer)
          sub.pollTimer = null
        }
        openEventStream(runId)
      }
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status === 404) {
        if (sub.pollTimer) {
          window.clearInterval(sub.pollTimer)
          sub.pollTimer = null
        }
        subscriptions.delete(runId)
        fail('Run 不存在或已删除，已停止订阅。')
        return
      }
      // 其他错误：保持轮询兜底至终态
    }
  }, 2000)
}

function disconnectRun(runId: string) {
  const sub = subscriptions.get(runId)
  if (!sub) return
  sub.source?.close()
  sub.source = null
  if (sub.pollTimer) {
    window.clearInterval(sub.pollTimer)
    sub.pollTimer = null
  }
  subscriptions.delete(runId)
}

function disconnectAll() {
  for (const runId of [...subscriptions.keys()]) disconnectRun(runId)
}

// 终态：断订阅，拉全量 trace 覆盖气泡内联数据。
async function finalizeRun(run: AgentRun) {
  if (ACTIVE_STATUSES.includes(run.status as (typeof ACTIVE_STATUSES)[number])) return
  disconnectRun(run.id)
  if (activeRun.value?.id === run.id) {
    activeRun.value = run
  }
  const item = runItem(run.id)
  if (!item) return
  item.run = run
  if (run.status === 'completed') {
    vizHintVisible.value = true
    // 采集/分析完成后重查辩论前置条件（首次采集入库即解锁滑块）。
    void refreshDebateReady()
    // 证据面板数据可能已更新（新帖子/新主张）：作废缓存，面板开着就刷新。
    evidenceLoaded = false
    if (activePanel.value === 'evidence') void loadEvidenceSummary()
    // 案例 updated_at / 状态变化后同步左侧会话列表。
    void refreshCases()
  }
  // 终态后重建对话流：专家成果（artifacts）随子 run 卡片一起出现。
  scheduleChatRefresh()
  try {
    const trace = await api.getRunTrace(run.id)
    item.trace = trace
    // 全量 trace 覆盖实时增量（同一次运行的最终精确数据）。
    item.liveToolCalls = trace.tool_calls
    item.liveModelCalls = trace.model_calls
    item.approvals = trace.approvals.map((approval) => ({
      id: approval.id,
      action: approval.action,
      reason: approval.reason,
      status: approval.status,
      request_payload: approval.request_payload,
    }))
  } catch {
    // trace 拉取失败不影响主流程
  }
}

// 订阅同步：活跃 run 建订阅、终态 run 收尾、失效订阅清理。
// deep watch 在 ingestRunEvent 修改 live 数据时也会触发；sync 幂等 +
// 防重入标志避免递归抖动。
watch(
  chatItems,
  (items) => {
    if (syncingSubscriptions) return
    syncingSubscriptions = true
    try {
      const remainingIds = new Set<string>()
      for (const item of items) {
        if (item.type !== 'run') continue
        remainingIds.add(item.run.id)
        if (ACTIVE_STATUSES.includes(item.run.status as (typeof ACTIVE_STATUSES)[number])) {
          if (!subscriptions.has(item.run.id)) openEventStream(item.run.id)
        } else if (subscriptions.has(item.run.id)) {
          // 订阅中的 run 转为终态：拉全量 trace 覆盖内联数据。
          void finalizeRun(item.run)
        }
      }
      for (const runId of [...subscriptions.keys()]) {
        if (!remainingIds.has(runId)) disconnectRun(runId)
      }
      // 当前关注 run：优先取第一个活跃 run；无活跃 run 时保留最后关注的
      // run（终态仍可见，如头部「等待审批」标与追踪），仅当它已不在对话
      // 流中才清空。
      const active = items.find(
        (item): item is Extract<ChatItem, { type: 'run' }> =>
          item.type === 'run'
          && ACTIVE_STATUSES.includes(item.run.status as (typeof ACTIVE_STATUSES)[number]),
      )
      if (active) {
        activeRun.value = active.run
      } else if (activeRun.value && !remainingIds.has(activeRun.value.id)) {
        activeRun.value = null
      }
      // 运行指令目标自动跟随第一个活跃 run（无显式目标或目标已终态时）。
      if (activeRun.value && ACTIVE_STATUSES.includes(
        activeRun.value.status as (typeof ACTIVE_STATUSES)[number],
      )) {
        if (
          !steerTarget.value
          || !ACTIVE_STATUSES.includes(
            steerTarget.value.status as (typeof ACTIVE_STATUSES)[number],
          )
        ) {
          steerTarget.value = activeRun.value
        }
      } else {
        steerTarget.value = null
      }
    } finally {
      syncingSubscriptions = false
    }
  },
  { deep: true },
)

// ---------------- Run 操作 ----------------

async function cancelRun(runId: string) {
  try {
    const run = await api.cancelRun(runId)
    await finalizeRun(run)
  } catch {
    fail('取消失败，请重试。', () => void cancelRun(runId))
  }
}

async function resumeRun(runId: string) {
  try {
    const run = await api.resumeRun(runId)
    // 恢复后重建订阅：先断开旧流，再重建对话流让 watch 重新挂接实时流。
    disconnectRun(runId)
    await loadWorkspace()
    const item = runItem(runId)
    if (item) item.run = run
  } catch {
    fail('恢复失败，请重试。', () => void resumeRun(runId))
  }
}

async function decideApproval(
  runId: string,
  approvalId: string,
  decision: boolean,
  note: string,
) {
  try {
    const run = await api.approveRun(runId, approvalId, decision, note)
    const item = runItem(runId)
    if (item) {
      const approval = item.approvals.find((candidate) => candidate.id === approvalId)
      if (approval) approval.status = decision ? 'approved' : 'rejected'
      item.run = run
    }
    if (activeRun.value?.id === runId) {
      activeRun.value = run
    }
  } catch {
    fail('审批提交失败，请重试。', () => void decideApproval(runId, approvalId, decision, note))
  }
}

async function loadTrace(runId: string) {
  const item = runItem(runId)
  if (!item || item.trace || item.traceLoading) return
  item.traceLoading = true
  try {
    const trace = await api.getRunTrace(runId)
    item.trace = trace
    item.liveToolCalls = trace.tool_calls
    item.liveModelCalls = trace.model_calls
    item.approvals = trace.approvals.map((approval) => ({
      id: approval.id,
      action: approval.action,
      reason: approval.reason,
      status: approval.status,
      request_payload: approval.request_payload,
    }))
  } catch {
    fail('Run Trace 加载失败。', () => void loadTrace(runId))
  } finally {
    item.traceLoading = false
  }
}

// ---------------- Evidence 面板 ----------------

async function loadEvidenceSummary() {
  if (evidenceLoaded) return
  evidenceLoaded = true
  try {
    evidenceSummary.value = await api.getEvidenceSummary(caseId.value)
    if (error.value.startsWith('证据汇总')) {
      error.value = ''
      retryAction.value = null
    }
  } catch {
    // 失败必须清缓存，否则横幅「重试」会因 evidenceLoaded 直接 return。
    evidenceLoaded = false
    fail('证据汇总加载失败。', () => void loadEvidenceSummary())
  }
}

onMounted(loadWorkspace)
onBeforeUnmount(disconnectAll)
</script>

<template>
  <div class="page workspace-page">
    <div v-if="loading" class="workspace-loading">
      <span class="spinner" />
      正在载入案例…
    </div>

    <template v-else>
      <!-- 加载失败时 idle 态也要看得到错误与重试，不能只在案例加载成功后出现。 -->
      <div v-if="error" class="error-banner">
        <span>{{ error }}</span>
        <button v-if="retryAction" type="button" class="ghost-button" @click="retryAction()">
          重试
        </button>
      </div>

      <template v-if="!currentCase">
        <!-- 会话加载失败 / 已被删除时的兜底：不再是纯空白，展示系统介绍 -->
        <div class="workspace-idle">
          <div class="workspace-idle-card">
            <span class="eyebrow">COIFESP · SOCIAL INTELLIGENCE HARNESS</span>
            <h1>让每条结论，都能回到证据。</h1>
            <p>
              从多平台采集、证据化对话分析到跨平台对齐与辩论验证，一站式舆情研究工作台。
              当前调查不可用或已被删除，返回工作台选择一个调查继续分析。
            </p>
            <button type="button" class="primary-button" @click="router.push('/')">
              返回工作台
            </button>
          </div>
        </div>
      </template>

      <template v-else-if="currentCase">
      <section class="workspace-header">
        <button class="back-button" type="button" @click="router.push('/')">
          <ArrowLeft :size="18" />
        </button>
        <div class="workspace-title">
          <div class="workspace-meta">
            <span class="case-id">CASE · {{ currentCase.id.slice(0, 8).toUpperCase() }}</span>
            <span class="case-title" :title="currentCase.description || currentCase.topic">
              {{ currentCase.title }}
            </span>
            <span v-for="platform in currentCase.platforms" :key="platform" class="platform-chip">
              {{ platformLabels[platform] || platform }}
            </span>
          </div>
        </div>
        <span
          v-if="activeRun && activeRun.status === 'waiting_approval'"
          class="status-label waiting"
        >
          等待审批
        </span>
        <!-- 对话 / 辩论 滑块切换：视图与滑块位置绑定，滑块带位移动画 -->
        <div class="mode-slider" role="tablist" aria-label="对话区视图切换">
          <span class="slider-thumb" :class="{ debate: chatMode === 'debate' }" aria-hidden="true" />
          <button
            type="button"
            role="tab"
            :aria-selected="chatMode === 'chat'"
            :class="{ active: chatMode === 'chat' }"
            @click="chatMode = 'chat'"
          >
            <MessagesSquare :size="14" />
            对话
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="chatMode === 'debate'"
            :class="{ active: chatMode === 'debate' }"
            :disabled="!debateReady"
            :title="debateReady ? '切换到多角色辩论' : '需先完成社交平台数据采集后才能辩论'"
            @click="chatMode = 'debate'"
          >
            <Gavel :size="14" />
            辩论
            <Lock v-if="!debateReady" :size="11" class="lock-hint" />
          </button>
        </div>
        <button
          type="button"
          class="ghost-button evidence-toggle"
          :class="{ active: evidenceOpen }"
          @click="togglePanel('evidence')"
        >
          <FileText :size="15" />
          证据
        </button>
        <button
          type="button"
          class="ghost-button"
          :class="{ active: vizOpen }"
          title="查看跨平台数据可视化"
          @click="togglePanel('viz')"
        >
          <BarChart3 :size="15" />
          可视化
        </button>
        <button
          type="button"
          class="ghost-button"
          :class="{ active: monitoringOpen }"
          title="查看持续监测与告警"
          @click="togglePanel('monitoring')"
        >
          <Bell :size="15" />
          监测
        </button>
        <button
          type="button"
          class="ghost-button"
          :class="{ active: mediaOpen }"
          title="查看媒体资产与 OCR/字幕"
          @click="togglePanel('media')"
        >
          <ImageIcon :size="15" />
          媒体
        </button>
        <button
          type="button"
          class="ghost-button"
          :class="{ active: alignmentOpen }"
          title="查看跨平台实体/内容/叙事对齐候选"
          @click="togglePanel('alignment')"
        >
          <GitCompare :size="15" />
          对齐
        </button>
        <button
          type="button"
          class="ghost-button"
          :class="{ active: integrityOpen }"
          title="查看垃圾营销/机器人/协同行为风险信号"
          @click="togglePanel('integrity')"
        >
          <ShieldAlert :size="15" />
          完整性
        </button>
      </section>

      <div v-if="steerNotice" class="notice-banner">{{ steerNotice }}</div>

      <div class="chat-layout">
        <main class="chat-main">
          <template v-if="chatMode === 'chat'">
            <ChatThread
              :items="chatItems"
              :guide="guideVisible"
              :case-id="caseId"
              @cancel="cancelRun"
              @resume="resumeRun"
              @load-trace="loadTrace"
              @retry-artifacts="retryArtifacts"
              @ask-artifact="askArtifact"
              @quick="quickAnalyze"
              @open-evidence="openEvidenceFromGuide"
              @fill-input="fillInput"
              @enter-steer="enterSteerFromBubble"
            />
            <ChatInputBar
              ref="inputBarRef"
              :sending="sending"
              :real-crawl="realCrawl"
              :llm-configured="llmConfigured"
              :steer-target="steerTarget"
              :ask-target="askTarget"
              :approval-target="approvalTarget"
              @send="sendMessage"
              @quick="quickAnalyze"
              @steer="steerRun"
              @cancel-steer="cancelSteer"
              @cancel-ask="askTarget = null"
              @decide="decideApproval"
            />
          </template>
          <!-- 辩论内嵌对话主区：与对话流同宽，不再挤在右侧窄边栏 -->
          <DebatePanel v-else :case-id="caseId" embedded />
        </main>

        <!-- 对话区最右侧滑出面板：证据 / 可视化（互斥）。
             未打开完全不渲染（v-if），避免 flex gap 在右侧留下空白带。 -->
        <EvidenceSidebar
          v-if="evidenceOpen"
          :open="evidenceOpen"
          :summary="evidenceSummary"
          @close="activePanel = null"
          @run-analysis="quickAnalyze"
        />
        <VisualSidebar
          v-if="vizOpen"
          :open="vizOpen"
          :case-id="caseId"
          @close="activePanel = null"
        />
        <MonitoringPanel
          v-if="monitoringOpen"
          :open="monitoringOpen"
          :case-id="caseId"
          @close="activePanel = null"
        />
        <MediaPanel
          v-if="mediaOpen"
          :open="mediaOpen"
          :case-id="caseId"
          @close="activePanel = null"
        />
        <AlignmentPanel
          v-if="alignmentOpen"
          :open="alignmentOpen"
          :case-id="caseId"
          @close="activePanel = null"
        />
        <IntegrityPanel
          v-if="integrityOpen"
          :open="integrityOpen"
          :case-id="caseId"
          @close="activePanel = null"
        />
      </div>

      <div v-if="vizHintVisible" class="viz-hint">
        <BarChart3 :size="14" />
        <span>分析完成，已生成可视化数据</span>
        <button type="button" class="viz-hint-link" @click="activePanel = 'viz'; vizHintVisible = false">
          查看
        </button>
      </div>
      </template>
    </template>
  </div>
</template>
