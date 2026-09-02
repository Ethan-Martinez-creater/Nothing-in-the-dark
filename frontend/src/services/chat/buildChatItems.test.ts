import { describe, expect, it } from 'vitest'

import { buildChatItems, preserveRunLiveState } from './buildChatItems'
import type { AgentRun, Artifact, ChatItem, RunTrace, TurnRecord } from '@/types/api'

function makeTurn(overrides: Partial<TurnRecord> = {}): TurnRecord {
  return {
    id: 't1',
    case_id: 'case-1',
    role: 'user',
    content: '用户指令',
    created_at: '2026-08-01T00:00:00+00:00',
    ...overrides,
  } as TurnRecord
}

function makeRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: 'run-1',
    case_id: 'case-1',
    status: 'completed',
    created_at: '2026-08-01T00:00:01+00:00',
    ...overrides,
  } as AgentRun
}

describe('buildChatItems（完整对话流重建）', () => {
  it('merges coordinator final answer into the run card', () => {
    const turns = [
      makeTurn({ id: 't-user', role: 'user', content: '启动调查' }),
      makeTurn({
        id: 't-final',
        role: 'assistant',
        content: '协调器最终回答',
        created_at: '2026-08-01T00:00:05+00:00',
      }),
    ]
    const runs = [makeRun({ id: 'run-top', turn_id: 't-user' })]
    const items = buildChatItems(turns, runs, [])
    expect(items).toHaveLength(1)
    const runItem = items[0]
    expect(runItem?.type).toBe('run')
    expect(runItem?.type === 'run' && runItem.finalContent).toBe('协调器最终回答')
  })

  it('keeps expert assistant turns as their own run cards', () => {
    const turns = [
      makeTurn({ id: 't-user', role: 'user', content: '启动调查' }),
      makeTurn({
        id: 't-coordinator',
        role: 'assistant',
        content: '协调器回答',
        created_at: '2026-08-01T00:00:05+00:00',
      }),
      makeTurn({
        id: 't-expert',
        role: 'assistant',
        content: '专家回答',
        created_at: '2026-08-01T00:00:03+00:00',
      }),
    ]
    const runs = [
      makeRun({ id: 'run-top', turn_id: 't-user' }),
      makeRun({ id: 'run-expert', turn_id: 't-expert' }),
    ]
    const items = buildChatItems(turns, runs, [])
    const expertItem = items.find(
      (item) => item.type === 'run' && item.run.id === 'run-expert',
    )
    expect(expertItem?.type === 'run' && expertItem.finalContent).toBe('专家回答')
    // coordinator 的 finalContent 不吃掉专家 turn
    const topItem = items.find((item) => item.type === 'run' && item.run.id === 'run-top')
    expect(topItem?.type === 'run' && topItem.finalContent).toBe('协调器回答')
  })

  it('renders plain turns without runs and preserves ordering', () => {
    const turns = [
      makeTurn({ id: 't-a', role: 'user', content: '第一问' }),
      makeTurn({
        id: 't-b',
        role: 'assistant',
        content: '直接回答（无 run）',
        created_at: '2026-08-01T00:00:02+00:00',
      }),
      makeTurn({ id: 't-c', role: 'user', content: '第二问', created_at: '2026-08-01T00:00:03+00:00' }),
      makeTurn({
        id: 't-d',
        role: 'assistant',
        content: '第二答',
        created_at: '2026-08-01T00:00:04+00:00',
      }),
    ]
    const items = buildChatItems(turns, [], [])
    expect(items).toHaveLength(4)
    expect(items[1]?.type).toBe('turn')
    expect(items[1]?.type === 'turn' && items[1].turn.content).toBe('直接回答（无 run）')
  })

  it('groups artifacts by run and collects orphan artifacts', () => {
    const turns = [makeTurn({ id: 't-user' })]
    const runs = [makeRun({ id: 'run-1', turn_id: 't-user' })]
    const runArtifact = { id: 'a1', run_id: 'run-1' } as Artifact
    const orphan = { id: 'a2', run_id: 'run-missing' } as Artifact
    const items = buildChatItems(turns, runs, [runArtifact, orphan])
    const runItem = items.find((item) => item.type === 'run')
    expect(runItem?.type === 'run' && runItem.artifacts).toEqual([runArtifact])
    const orphanItem = items.find((item) => item.type === 'orphan-artifacts')
    expect(orphanItem?.type === 'orphan-artifacts' && orphanItem.artifacts).toEqual([orphan])
  })

  it('falls back to standalone run cards for runs without turns', () => {
    const items = buildChatItems([], [makeRun({ id: 'run-x', turn_id: undefined })], [])
    expect(items).toHaveLength(1)
    expect(items[0]?.type).toBe('run')
  })

  it('places failed expert runs (no turn) into their own conversation turn, not at the end', () => {
    // 回归：失败/取消的专家子 run 没有回答 turn（turn_id 为空）。
    // 此前兜底逻辑把它们追加到列表末尾，导致上一轮的失败提示跑到
    // 最新命令之后（新命令显示在失败提示上面）。
    const turns = [
      makeTurn({ id: 't-user', role: 'user', content: '委派专家', created_at: '2026-08-01T00:00:00+00:00' }),
      makeTurn({
        id: 't-final',
        role: 'assistant',
        content: '上一轮协调器回答',
        created_at: '2026-08-01T00:00:05+00:00',
      }),
      makeTurn({ id: 't-new', role: 'user', content: '新的深度采集', created_at: '2026-08-01T00:00:10+00:00' }),
    ]
    const runs = [
      makeRun({
        id: 'run-top',
        turn_id: 't-user',
        created_at: '2026-08-01T00:00:00+00:00',
      }),
      makeRun({
        id: 'run-expert-fail',
        turn_id: undefined,
        status: 'failed',
        created_at: '2026-08-01T00:00:02+00:00',
      }),
      makeRun({
        id: 'run-new',
        turn_id: 't-new',
        created_at: '2026-08-01T00:00:10+00:00',
      }),
    ]
    const items = buildChatItems(turns, runs, [])
    const runOrder = items
      .filter((item): item is Extract<ChatItem, { type: 'run' }> => item.type === 'run')
      .map((item) => item.run.id)
    // 失败专家应插在所属轮次（run-top 之后、新命令 run-new 之前）。
    expect(runOrder).toEqual(['run-top', 'run-expert-fail', 'run-new'])
  })

  it('preserves approvals and trace across a refresh rebuild', () => {
    const turns = [makeTurn({ id: 't-user' })]
    const runs = [makeRun({ id: 'run-1', turn_id: 't-user' })]
    const previous: ChatItem[] = buildChatItems(turns, runs, [])
    const runPrevious = previous[0]
    if (runPrevious?.type !== 'run') throw new Error('expected run item')
    runPrevious.approvals = [
      {
        id: 'appr-1',
        action: 'collect_social_posts',
        reason: '需要采集',
        status: 'pending',
        request_payload: {},
      },
    ]
    runPrevious.trace = { events: [], total_cost: 0.5 } as unknown as RunTrace
    runPrevious.liveEvents = [{ event_type: 'agent_end' } as never]

    const rebuilt = buildChatItems(turns, runs, [])
    const merged = preserveRunLiveState(previous, rebuilt)
    const mergedRun = merged[0]
    if (mergedRun?.type !== 'run') throw new Error('expected run item')
    expect(mergedRun.approvals).toHaveLength(1)
    expect(mergedRun.approvals[0]?.id).toBe('appr-1')
    expect(mergedRun.trace).not.toBeNull()
    expect(mergedRun.liveEvents).toHaveLength(1)
  })
})
