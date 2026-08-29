// Optimization V2 (M2.1)：per-run SSE 订阅机制从 CaseWorkspaceView 提取。
// composable 拥有：订阅表、游标去重、SSE 错误轮询兜底、终态 trace 覆盖、404 停止。
// 业务语义（事件→状态机、终态附加动作）通过回调注入，保持行为与原实现一致。
import { api } from '@/services/api'
import type { AgentRun, ChatItem, RunEvent } from '@/types/api'

export const ACTIVE_RUN_STATUSES = ['pending', 'running', 'waiting_approval'] as const

export type RunChatItem = Extract<ChatItem, { type: 'run' }>

export function isActiveRunStatus(status: string): boolean {
  return (ACTIVE_RUN_STATUSES as readonly string[]).includes(status)
}

interface RunSubscription {
  source: EventSource | null
  cursor: number
  pollTimer: number | null
}

export interface UseRunSubscriptionsOptions {
  /** 按 runId 定位对话流中的 run 条目；不存在时订阅事件被忽略。 */
  resolveItem: (runId: string) => RunChatItem | undefined
  /** 单条事件驱动的业务状态机（审批卡 / 工具模型调用增量 / 成果刷新）。 */
  onEvent?: (item: RunChatItem, event: RunEvent) => void
  /** 终态附加业务动作（在 item.run 已更新、trace 已覆盖后调用）。 */
  onFinalized?: (run: AgentRun) => void | Promise<void>
  /** run 查询 404（已被删除）时的提示回调。 */
  onNotFound?: () => void
}

export function useRunSubscriptions(options: UseRunSubscriptionsOptions) {
  // 每个活跃 run 一条独立订阅（含游标与轮询兜底）；终态后由 trace 覆盖。
  const subscriptions = new Map<string, RunSubscription>()

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
    const item = options.resolveItem(runId)
    if (!item) return
    item.liveEvents.push(event)
    options.onEvent?.(item, event)
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
        if (!isActiveRunStatus(run.status)) {
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
          options.onNotFound?.()
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
    if (isActiveRunStatus(run.status)) return
    disconnectRun(run.id)
    const item = options.resolveItem(run.id)
    if (!item) return
    item.run = run
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
    await options.onFinalized?.(run)
  }

  function has(runId: string) {
    return subscriptions.has(runId)
  }

  function activeIds(): string[] {
    return [...subscriptions.keys()]
  }

  return {
    openEventStream,
    ingestRunEvent,
    startPolling,
    disconnectRun,
    disconnectAll,
    finalizeRun,
    has,
    activeIds,
  }
}
