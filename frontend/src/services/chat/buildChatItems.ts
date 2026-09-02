// C10: 完整对话流重建（唯一生产实现）。
// 自旧 CaseWorkspaceView.buildChatItems 迁入：expert assistant turn 归属、
// coordinator 最终回答向后匹配、orphan artifacts、无 turn 的 run 兜底。
// CaseWorkspaceView 与 CopilotDrawer 共用，不再维护简化版本。
import type {
  AgentRun,
  Artifact,
  ChatItem,
  TurnRecord,
} from '@/types/api'

export function buildChatItems(
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
      .filter(
        (candidate) =>
          candidate.turn_id && turnById.get(candidate.turn_id)?.role === 'assistant',
      )
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
          if (
            candidate.role === 'assistant' &&
            !runLinkedAssistantIds.has(candidate.id)
          ) {
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
  // 兜底：没有关联 turn 的 run（失败/取消的专家子 run 没有回答 turn，
  // turn_id 为空）。按 created_at 插回正确位置，而不是追加到末尾——
  // 否则会脱离所属对话轮次，跑到最新命令之后。
  for (const run of runs) {
    if (run.turn_id && turns.some((turn) => turn.id === run.turn_id)) continue
    const item: Extract<ChatItem, { type: 'run' }> = {
      type: 'run',
      run,
      artifacts: artifactsByRun.get(run.id) ?? [],
      approvals: [],
      trace: null,
      traceLoading: false,
      liveEvents: [],
      liveToolCalls: [],
      liveModelCalls: [],
    }
    const insertAt = items.findIndex((candidate) => {
      // orphan-artifacts 无 created_at，不参与时间排序定位
      if (candidate.type === 'run') return candidate.run.created_at > run.created_at
      if (candidate.type === 'turn') return candidate.turn.created_at > run.created_at
      return false
    })
    if (insertAt === -1) items.push(item)
    else items.splice(insertAt, 0, item)
  }
  if (orphanArtifacts.length) {
    items.push({ type: 'orphan-artifacts', artifacts: orphanArtifacts })
  }
  return items
}

export function makeRunItem(run: AgentRun): Extract<ChatItem, { type: 'run' }> {
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

/**
 * 重建后保留旧 run 项的本地实时状态（C10）：
 * trace 懒加载状态、live SSE 缓冲与审批卡（SSE 不会重发已消费事件，
 * 清空会让审批队列在每次对话流重建时闪烁消失）。
 */
export function preserveRunLiveState(
  previous: ChatItem[],
  rebuilt: ChatItem[],
): ChatItem[] {
  const oldByRun = new Map(
    previous
      .filter((item): item is Extract<ChatItem, { type: 'run' }> => item.type === 'run')
      .map((item) => [item.run.id, item]),
  )
  for (const item of rebuilt) {
    if (item.type !== 'run') continue
    const old = oldByRun.get(item.run.id)
    if (!old) continue
    item.trace = old.trace
    item.traceLoading = old.traceLoading
    if (old.trace || old.traceLoading) {
      item.artifactsError = old.artifactsError
    }
    item.liveEvents = old.liveEvents
    item.liveToolCalls = old.liveToolCalls
    item.liveModelCalls = old.liveModelCalls
    item.approvals = old.approvals
  }
  return rebuilt
}
