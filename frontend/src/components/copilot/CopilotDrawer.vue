<script setup lang="ts">
// Optimization V2 (M2.4)：Contextual Copilot Drawer。
// 复用 ChatThread / ChatInputBar 渲染与输入；发送时 snapshot 当前 UI 上下文，
// 经结构化 ui_context 字段进入 Run metadata（不拼进 content）。
// 历史属于同一 Case Turn/Run 流，不在各页面重复创建独立聊天记录。
import { computed, inject, onMounted, ref, watch } from 'vue'

import { X } from 'lucide-vue-next'

import ChatInputBar from '@/components/chat/ChatInputBar.vue'
import ChatThread from '@/components/chat/ChatThread.vue'
import {
  useInvestigationContext,
} from '@/composables/useInvestigationContext'
import { useRunSubscriptions, isActiveRunStatus } from '@/composables/useRunSubscriptions'
import { api } from '@/services/api'
import type {
  AgentRun,
  Artifact,
  ChatItem,
  RunEvent,
  TurnRecord,
} from '@/types/api'

const props = defineProps<{ caseId: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const { uiContext, clearSelection } = useInvestigationContext()

const items = ref<ChatItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const sending = ref(false)
const realCrawl = ref(false)
const llmConfigured = ref(true)

const contextLabel = computed(() => {
  const ctx = uiContext.value
  const parts: string[] = [ctx.workspace]
  if (ctx.selected_type) parts.push(ctx.selected_type)
  return parts.join(' · ')
})

function runItemFor(run: AgentRun): ChatItem {
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

async function loadHistory() {
  error.value = null
  try {
    const [turns, runs, artifacts] = await Promise.all([
      api.listTurns(props.caseId),
      api.listCaseRuns(props.caseId),
      api.listArtifacts(props.caseId),
    ])
    items.value = buildItems(turns, runs, artifacts)
  } catch {
    error.value = 'Copilot 历史加载失败，请重试。'
  } finally {
    loading.value = false
  }
}

// 简化版对话流重建：按时间合并 turns / runs，run 挂其后的首个未消费
// assistant turn 作为 finalContent；artifacts 按 run_id 分组。
function buildItems(turns: TurnRecord[], runs: AgentRun[], artifacts: Artifact[]): ChatItem[] {
  const artifactsByRun = new Map<string, Artifact[]>()
  for (const artifact of artifacts) {
    if (!artifact.run_id) continue
    const bucket = artifactsByRun.get(artifact.run_id) ?? []
    bucket.push(artifact)
    artifactsByRun.set(artifact.run_id, bucket)
  }

  const items: ChatItem[] = []
  const consumed = new Set<string>()
  const timeline = [
    ...turns.map((turn) => ({ at: turn.created_at, kind: 'turn' as const, turn })),
    ...runs.map((run) => ({ at: run.created_at, kind: 'run' as const, run })),
  ].sort((a, b) => a.at.localeCompare(b.at))

  for (const entry of timeline) {
    if (entry.kind === 'turn') {
      if (consumed.has(entry.turn.id)) continue
      items.push({ type: 'turn', turn: entry.turn })
      continue
    }
    const run = entry.run
    const finalContent = findAssistantAfter(turns, run.created_at, consumed)
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
  }
  return items
}

function findAssistantAfter(
  turns: TurnRecord[],
  afterIso: string,
  consumed: Set<string>,
): string | undefined {
  const sorted = [...turns].sort((a, b) => a.created_at.localeCompare(b.created_at))
  for (const turn of sorted) {
    if (consumed.has(turn.id)) continue
    if (turn.role !== 'assistant') continue
    if (turn.created_at >= afterIso) {
      consumed.add(turn.id)
      return turn.content
    }
  }
  return undefined
}

const subscriptions = useRunSubscriptions({
  resolveItem: (runId) => {
    const item = items.value.find(
      (candidate): candidate is Extract<ChatItem, { type: 'run' }> =>
        candidate.type === 'run' && candidate.run.id === runId,
    )
    return item
  },
  onEvent: (item, event) => {
    const payload = event.payload as Record<string, unknown>
    if (event.event_type === 'agent_queued' && item.run.status === 'pending') {
      item.run.status = 'running'
    }
    if (['agent_end', 'agent_error'].includes(event.event_type)) {
      item.run.status = (event.status as AgentRun['status']) ?? 'completed'
    }
    if (['approval_pending', 'approval_required'].includes(event.event_type)) {
      const approvalId = String(payload.approval_id || '')
      if (approvalId && !item.approvals.some((approval) => approval.id === approvalId)) {
        item.approvals.push({
          id: approvalId,
          action: String(payload.action || ''),
          reason: String(payload.reason || ''),
          status: 'pending',
          request_payload: (payload.request_payload as Record<string, unknown>) || {},
        })
      }
      item.run.status = 'waiting_approval'
    }
  },
  onFinalized: () => {
    // 终态后重拉历史：拿到 assistant 最终回复与全量 trace 数据。
    void loadHistory()
  },
})

async function handleSend(content: string, approveCrawl: boolean, artifactId?: string) {
  sending.value = true
  error.value = null
  const uiContextSnapshot = JSON.parse(JSON.stringify(uiContext.value)) as Record<
    string,
    unknown
  >
  try {
    const run = await api.sendMessage(
      props.caseId,
      content,
      approveCrawl,
      artifactId,
      uiContextSnapshot,
    )
    // 发送瞬间的 context 已冻结进 Run；本地 snapshot 清除选中对象。
    clearSelection()
    items.value.push(runItemFor(run))
    subscriptions.openEventStream(run.id)
  } catch {
    error.value = '消息发送失败，请重试。'
  } finally {
    sending.value = false
  }
}

async function handleDecide(
  runId: string,
  approvalId: string,
  decision: boolean,
  note: string,
) {
  try {
    await api.approveRun(runId, approvalId, decision, note || undefined)
    await loadHistory()
  } catch {
    error.value = '审批操作失败，请重试。'
  }
}

watch(
  () => props.caseId,
  () => {
    loading.value = true
    void loadHistory()
  },
  { immediate: false },
)

onMounted(async () => {
  await loadHistory()
  try {
    const capabilities = await api.getCapabilities()
    realCrawl.value = capabilities.demo_mode === false
    llmConfigured.value = capabilities.llm_configured ?? capabilities.llm?.configured ?? true
  } catch {
    // 默认演示模式展示
  }
})
</script>

<template>
  <aside class="copilot" aria-label="Copilot">
    <header class="copilot__header">
      <div class="copilot__title">
        <strong>Copilot</strong>
        <button
          type="button"
          class="copilot__context"
          title="清除选中对象（保留工作区）"
          @click="clearSelection"
        >
          {{ contextLabel }}
        </button>
      </div>
      <button type="button" class="copilot__close" aria-label="关闭" @click="emit('close')">
        <X :size="16" />
      </button>
    </header>

    <p v-if="error" class="copilot__error">{{ error }}</p>

    <div class="copilot__thread">
      <p v-if="loading" class="copilot__hint">正在加载…</p>
      <ChatThread
        v-else
        :items="items"
        :case-id="caseId"
        @decide="handleDecide"
      />
    </div>

    <div class="copilot__input">
      <ChatInputBar
        :sending="sending"
        :real-crawl="realCrawl"
        :llm-configured="llmConfigured"
        @send="handleSend"
      />
    </div>
  </aside>
</template>

<style scoped>
.copilot {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  border-left: 1px solid var(--border);
  background: var(--surface);
}

.copilot__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}

.copilot__title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 14px;
}

.copilot__context {
  overflow: hidden;
  max-width: 240px;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--accent-strong);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.copilot__close {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
}

.copilot__error {
  margin: 8px 12px 0;
  font-size: 12px;
  color: var(--red);
}

.copilot__thread {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px;
}

.copilot__hint {
  font-size: 13px;
  color: var(--text-muted);
}

.copilot__input {
  border-top: 1px solid var(--border);
  padding: 10px 12px;
}
</style>
