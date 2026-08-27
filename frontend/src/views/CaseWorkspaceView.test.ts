import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatInputBar from '@/components/chat/ChatInputBar.vue'
import ChatThread from '@/components/chat/ChatThread.vue'
import type { AgentRun, ApprovalInfo, Artifact, ChatItem, RunEvent, RunTrace, TurnRecord } from '@/types/api'

import CaseWorkspaceView from './CaseWorkspaceView.vue'

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const apiMock = vi.hoisted(() => ({
  getCase: vi.fn(),
  listTurns: vi.fn(),
  listCaseRuns: vi.fn(),
  listArtifacts: vi.fn(),
  getEvidenceSummary: vi.fn(),
  getCapabilities: vi.fn(),
  sendMessage: vi.fn(),
  steerRun: vi.fn(),
  getRun: vi.fn(),
  listRunEvents: vi.fn(),
  getRunTrace: vi.fn(),
  cancelRun: vi.fn(),
  resumeRun: vi.fn(),
  approveRun: vi.fn(),
  runEventStreamUrl: vi.fn((runId: string, cursor: number) =>
    `http://test.local/api/v1/runs/${runId}/events/stream?cursor=${cursor}`,
  ),
  getPlatformComparison: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

// jsdom has no EventSource; stub a controllable one so the SSE paths
// (attach, resume-cursor reconnect, error fallback) can be driven manually.
class MockEventSource {
  static instances: MockEventSource[] = []
  static last(): MockEventSource | undefined {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }

  url: string
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  close(): void {
    this.onmessage = null
    this.onerror = null
  }

  emit(event: RunEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent)
  }

  fail(): void {
    this.onerror?.()
  }
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CASE_RECORD = {
  id: 'case-1',
  title: '财报舆情',
  topic: '财报',
  description: '',
  status: 'ready',
  platforms: ['weibo'],
  time_range: { start: null, end: null },
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
}

const CAPABILITIES = {
  version: 'test',
  environment: 'test',
  demo_mode: true,
  framework: 'langgraph',
  platforms: ['weibo'],
  llm_configured: true,
  llm: { provider: 'deepseek', configured: true, routes: {} },
}

function makeRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: 'run-1',
    case_id: 'case-1',
    turn_id: 'turn-1',
    parent_run_id: null,
    agent: 'coordinator',
    status: 'running',
    objective: '分析',
    model_route: 'flash',
    input_tokens: 0,
    output_tokens: 0,
    tool_call_count: 0,
    estimated_cost: 0,
    error_code: null,
    error: null,
    metadata_json: {},
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

function makeTurn(overrides: Partial<TurnRecord> = {}): TurnRecord {
  return {
    id: 'turn-1',
    case_id: 'case-1',
    role: 'user',
    content: '请分析',
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

function makeArtifact(overrides: Partial<Artifact> = {}): Artifact {
  return {
    id: 'art-1',
    case_id: 'case-1',
    task_id: null,
    run_id: 'run-1',
    kind: 'report',
    title: '报告',
    version: 1,
    data: {},
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

function makeEvent(overrides: Partial<RunEvent> & { payload?: Record<string, unknown> }): RunEvent {
  return {
    id: 1,
    run_id: 'run-1',
    event_type: 'agent_queued',
    agent: 'coordinator',
    skill: null,
    tool_call_id: null,
    tool: null,
    status: 'running',
    payload: {},
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

const TRACE: RunTrace = {
  run: makeRun({ status: 'completed' }),
  model_calls: [],
  tool_calls: [{ id: 'tc-1', run_id: 'run-1', tool_name: 'crawl', skill_name: null, status: 'success', arguments: {}, result: {}, error_code: null, input_summary: 'in', output_summary: 'out', retry_count: 0, duration_ms: 10, estimated_cost: 0.01, idempotency_key: null, approval_id: null, rag: null, started_at: '', finished_at: '' }],
  approvals: [],
  events: [],
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

let wrapper: VueWrapper | null = null
let router: ReturnType<typeof createRouter> | null = null
function freshApiMocks(): void {
  apiMock.getCase.mockResolvedValue(CASE_RECORD)
  apiMock.listTurns.mockResolvedValue([])
  apiMock.listCaseRuns.mockResolvedValue([])
  apiMock.listArtifacts.mockResolvedValue([])
  apiMock.getEvidenceSummary.mockResolvedValue({
    case_id: 'case-1',
    claims: [],
    unassigned: [],
  })
  apiMock.getCapabilities.mockResolvedValue(CAPABILITIES)
  // 默认已有采集数据（滑块辩论侧可用）；禁用场景在用例内单独覆盖。
  apiMock.getPlatformComparison.mockResolvedValue({
    platforms: ['weibo', 'bilibili'],
    participation: [
      { platform: 'weibo', posts: 3, total_engagement: 100, avg_engagement: 33 },
      { platform: 'bilibili', posts: 3, total_engagement: 90, avg_engagement: 30 },
    ],
    sentiment: [],
    timeline: [],
    topic_terms: [],
    common_terms: [],
    insights: [],
  })
  apiMock.getRun.mockResolvedValue(makeRun({ status: 'completed' }))
  apiMock.listRunEvents.mockResolvedValue([])
  apiMock.getRunTrace.mockResolvedValue(TRACE)
  apiMock.approveRun.mockResolvedValue(makeRun({ status: 'waiting_approval' }))
  apiMock.cancelRun.mockResolvedValue(makeRun({ status: 'cancelled' }))
  apiMock.sendMessage.mockResolvedValue(makeRun({ status: 'pending' }))
  apiMock.steerRun.mockResolvedValue({ id: 's-1' })
}

async function mountWorkspace(provides: Record<string, unknown> = {}): Promise<void> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/cases/:caseId', component: CaseWorkspaceView },
    ],
  })
  router.push('/cases/case-1')
  await router.isReady()
  wrapper = mount(CaseWorkspaceView, {
    global: {
      plugins: [router],
      // Stub the leaf children: their rendering is covered elsewhere; here
      // we drive their event outlets (decide / cancel / resume / send).
      stubs: {
        ChatThread: true,
        ChatInputBar: true,
        EvidenceSidebar: true,
        VisualSidebar: true,
        DebatePanel: true,
      },
      provide: provides,
    },
  })
  await flushPromises()
}

function vm(): {
  chatItems: ChatItem[]
  activeRun: AgentRun | null
  activePanel: 'evidence' | 'viz' | null
  chatMode: 'chat' | 'debate'
  evidenceOpen: boolean
  steerTarget: AgentRun | null
  askTarget: { artifactId: string } | null
  approvalTarget: {
    runId: string
    run: AgentRun
    approval: ApprovalInfo
    queueCount: number
  } | null
  [key: string]: unknown
} {
  if (!wrapper) throw new Error('wrapper not mounted')
  return wrapper.vm as never
}

beforeEach(() => {
  vi.clearAllMocks()
  freshApiMocks()
  MockEventSource.instances = []
  vi.stubGlobal('EventSource', MockEventSource)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  router = null
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

// ---------------------------------------------------------------------------
// 多轮对话流组装
// ---------------------------------------------------------------------------

describe('multi-turn chat flow assembly', () => {
  it('builds turn / run / orphan-artifact items in chronological order', async () => {
    apiMock.listTurns.mockResolvedValue([
      makeTurn({ id: 'turn-1', content: '第一问' }),
      makeTurn({ id: 'turn-2', content: '追问' }),
    ])
    apiMock.listCaseRuns.mockResolvedValue([
      makeRun({ id: 'run-1', turn_id: 'turn-2', status: 'completed' }),
    ])
    apiMock.listArtifacts.mockResolvedValue([
      makeArtifact({ id: 'art-1', run_id: 'run-1' }),
      makeArtifact({ id: 'art-2', run_id: null }), // legacy orphan
    ])

    await mountWorkspace()

    const items = vm().chatItems
    // turn-1 has no run (plain turn); turn-2 is replaced by its run bubble;
    // the run's artifact rides inside the run item; the unbound artifact
    // becomes a trailing orphan block.
    expect(items.map((item) => item.type)).toEqual(['turn', 'run', 'orphan-artifacts'])
    expect(items[0]!.type === 'turn' && items[0]!.turn.content).toBe('第一问')
    const runItem = items[1]!
    expect(runItem.type).toBe('run')
    if (runItem.type === 'run') {
      expect(runItem.run.id).toBe('run-1')
      expect(runItem.artifacts.map((a) => a.id)).toEqual(['art-1'])
    }
    const orphanItem = items[2]!
    if (orphanItem.type === 'orphan-artifacts') {
      expect(orphanItem.artifacts.map((a) => a.id)).toEqual(['art-2'])
    }
  })

  it('merges the trailing assistant turn into the run item as finalContent', async () => {
    // 创建会话不再生成主题 turn：user turn 来自用户第一条指令；run 完成后
    // 紧邻的 assistant turn 是模型最终回答，合并进 run 卡片顶部展示。
    apiMock.listTurns.mockResolvedValue([
      makeTurn({ id: 'turn-1', content: '用户指令' }),
      makeTurn({ id: 'turn-2', role: 'assistant', content: '模型最终回答' }),
    ])
    apiMock.listCaseRuns.mockResolvedValue([
      makeRun({ id: 'run-1', turn_id: 'turn-1', status: 'completed' }),
    ])
    apiMock.listArtifacts.mockResolvedValue([])

    await mountWorkspace()

    const items = vm().chatItems
    // 只应有 1 个 run 项：assistant turn 不再单独渲染为一个气泡。
    expect(items.map((item) => item.type)).toEqual(['run'])
    if (items[0]?.type === 'run') {
      expect(items[0].run.id).toBe('run-1')
      expect(items[0].finalContent).toBe('模型最终回答')
    }
  })

  it('merges expert answer turns and skips them for the top-level run', async () => {
    // 专家 run 的 turn_id 直接指向自己的 assistant 回答 turn；协调器
    // 的回答 turn 在专家 turn 之后，合并时跳过被专家关联的 turn，
    // 但中间的专家 turn 不能被吞掉（每个专家都要生成卡片）。
    apiMock.listTurns.mockResolvedValue([
      makeTurn({ id: 'turn-user', content: '用户指令' }),
      makeTurn({ id: 'turn-expert', role: 'assistant', content: '专家最终回答' }),
      makeTurn({ id: 'turn-coord', role: 'assistant', content: '协调器最终回答' }),
    ])
    apiMock.listCaseRuns.mockResolvedValue([
      makeRun({ id: 'run-coord', turn_id: 'turn-user', status: 'completed' }),
      makeRun({ id: 'run-expert', turn_id: 'turn-expert', parent_run_id: 'run-coord', agent: 'opinion', status: 'completed' }),
    ])
    apiMock.listArtifacts.mockResolvedValue([])

    await mountWorkspace()

    const runs = vm().chatItems.filter((item) => item.type === 'run')
    // 协调器与专家 run 都必须出现且都带上各自的最终回答。
    expect(runs).toHaveLength(2)
    const coord = runs.find((item) => item.type === 'run' && item.run.id === 'run-coord')
    const expert = runs.find((item) => item.type === 'run' && item.run.id === 'run-expert')
    if (coord?.type === 'run') expect(coord.finalContent).toBe('协调器最终回答')
    if (expert?.type === 'run') expect(expert.finalContent).toBe('专家最终回答')
  })

  it('does not attach SSE when no run is active', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'completed' })])
    await mountWorkspace()
    expect(MockEventSource.instances).toHaveLength(0)
    expect(vm().activeRun).toBeNull()
  })

  it('attaches SSE to the active run with cursor 0', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'running' })])
    await mountWorkspace()
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0]!.url).toContain('cursor=0')
    expect(vm().activeRun?.id).toBe('run-1')
  })
})

// ---------------------------------------------------------------------------
// SSE 事件驱动（多轮运行状态机）
// ---------------------------------------------------------------------------

describe('SSE event handling', () => {
  async function mountWithActiveRun(status: AgentRun['status'] = 'pending') {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status })])
    await mountWorkspace()
    return MockEventSource.last()!
  }

  it('agent_queued promotes a pending run to running', async () => {
    const source = await mountWithActiveRun('pending')
    source.emit(makeEvent({ id: 1, event_type: 'agent_queued' }))
    await flushPromises()

    const runItem = vm().chatItems[0]!
    expect(runItem.type).toBe('run')
    if (runItem.type === 'run') expect(runItem.run.status).toBe('running')
    expect(vm().activeRun?.status).toBe('running')
  })

  it('deduplicates events by id (resume cursor)', async () => {
    const source = await mountWithActiveRun('running')
    source.emit(makeEvent({ id: 5, event_type: 'agent_end', status: 'completed' }))
    source.emit(makeEvent({ id: 5, event_type: 'agent_end', status: 'completed' }))
    await flushPromises()
    const runItem = vm().chatItems[0]!
    expect(runItem.type).toBe('run')
    if (runItem.type === 'run') expect(runItem.liveEvents).toHaveLength(1)
  })

  it('approval_pending creates an approval card and flips to waiting_approval', async () => {
    const source = await mountWithActiveRun('running')
    source.emit(
      makeEvent({
        id: 2,
        event_type: 'approval_pending',
        payload: {
          approval_id: 'appr-1',
          action: 'crawl_extended',
          reason: '采集范围扩大',
          request_payload: { platforms: ['zhihu'] },
        },
      }),
    )
    await flushPromises()

    const runItem = vm().chatItems[0]!
    expect(runItem.type).toBe('run')
    if (runItem.type === 'run') {
      expect(runItem.approvals).toHaveLength(1)
      expect(runItem.approvals[0]).toMatchObject({
        id: 'appr-1',
        action: 'crawl_extended',
        status: 'pending',
      })
    }
    expect(vm().activeRun?.status).toBe('waiting_approval')
  })

  it('streams tool / model calls into the run bubble incrementally', async () => {
    const source = await mountWithActiveRun('running')
    source.emit(
      makeEvent({
        id: 1,
        event_type: 'tool_execution_start',
        tool_call_id: 'tc-1',
        tool: 'crawl',
        status: 'started',
      }),
    )
    source.emit(
      makeEvent({
        id: 2,
        event_type: 'tool_execution_end',
        tool_call_id: 'tc-1',
        status: 'success',
        payload: { duration_ms: 120, estimated_cost: 0.01, output_summary: '3 帖', rag: { available: true, hit_count: 2, retrieval_modes: ['pgvector'] } },
      }),
    )
    source.emit(
      makeEvent({
        id: 3,
        event_type: 'model_call_start',
        tool_call_id: 'mc-1',
        skill: 'flash',
        status: 'started',
        payload: { model: 'deepseek-v4-flash', route: 'flash' },
      }),
    )
    source.emit(
      makeEvent({
        id: 4,
        event_type: 'model_call_end',
        tool_call_id: 'mc-1',
        status: 'success',
        payload: { input_tokens: 100, output_tokens: 50, estimated_cost: 0.001, latency_ms: 200 },
      }),
    )
    await flushPromises()

    const runItem = vm().chatItems[0]!
    expect(runItem.type).toBe('run')
    if (runItem.type === 'run') {
      expect(runItem.liveToolCalls).toHaveLength(1)
      const toolCall = runItem.liveToolCalls[0]!
      expect(toolCall).toMatchObject({ status: 'success', duration_ms: 120, estimated_cost: 0.01, output_summary: '3 帖' })
      expect(toolCall.rag?.hit_count).toBe(2)

      expect(runItem.liveModelCalls).toHaveLength(1)
      const modelCall = runItem.liveModelCalls[0]!
      expect(modelCall).toMatchObject({ model: 'deepseek-v4-flash', input_tokens: 100, output_tokens: 50 })
    }
  })

  it('agent_end drives the run to the terminal status', async () => {
    const source = await mountWithActiveRun('running')
    source.emit(makeEvent({ id: 1, event_type: 'agent_end', status: 'completed' }))
    await flushPromises()
    const runItem = vm().chatItems[0]!
    expect(runItem.type).toBe('run')
    if (runItem.type === 'run') expect(runItem.run.status).toBe('completed')
  })
})

// ---------------------------------------------------------------------------
// SSE 断线重连（轮询兜底 → 重建流）
// ---------------------------------------------------------------------------

describe('SSE reconnect', () => {
  it('falls back to polling on error and rebuilds the stream when quiet', async () => {
    vi.useFakeTimers()
    try {
      apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'running' })])
      // The run stays active while polling: no events -> rebuild the SSE
      // stream instead of finalizing.
      apiMock.getRun.mockResolvedValue(makeRun({ status: 'running' }))
      await mountWorkspace()
      const first = MockEventSource.last()!
      expect(MockEventSource.instances).toHaveLength(1)

      first.fail() // SSE error -> close + startPolling
      await vi.advanceTimersByTimeAsync(2000)

      // quiet poll: no new events, run still active -> rebuild SSE with cursor
      expect(MockEventSource.instances).toHaveLength(2)
      expect(MockEventSource.last()!.url).toContain('cursor=0')
    } finally {
      vi.useRealTimers()
    }
  })

  it('polls events after a disconnect and finalizes with the full trace', async () => {
    vi.useFakeTimers()
    try {
      const terminal = makeRun({ status: 'completed' })
      apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'running' })])
      apiMock.listRunEvents.mockResolvedValue([makeEvent({ id: 3, event_type: 'agent_end', status: 'completed' })])
      apiMock.getRun.mockResolvedValue(terminal)
      await mountWorkspace()

      MockEventSource.last()!.fail()
      await vi.advanceTimersByTimeAsync(2000)
      await flushPromises()

      // Terminal state reached through polling: trace fetched, no rebuild.
      expect(apiMock.getRunTrace).toHaveBeenCalledWith('run-1')
      expect(MockEventSource.instances).toHaveLength(1)
      const runItem = vm().chatItems[0]!
      expect(runItem.type).toBe('run')
      if (runItem.type === 'run') {
        expect(runItem.run.status).toBe('completed')
        expect(runItem.trace?.tool_calls).toHaveLength(1)
        // 终态后全量 trace 覆盖实时增量。
        expect(runItem.liveToolCalls).toHaveLength(1)
      }
      // activeRun 保留最后关注的 run（终态 completed）。
      expect(vm().activeRun?.status).toBe('completed')
    } finally {
      vi.useRealTimers()
    }
  })
})

// ---------------------------------------------------------------------------
// 审批与 Run 操作（经 ChatThread / ChatInputBar 事件出口）
// ---------------------------------------------------------------------------

describe('approval and run actions', () => {
  it('approve decision updates the approval card and run', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'running' })])
    await mountWorkspace()

    // 审批入口在输入框上方（ChatInputBar 审批队列首卡）。
    const inputBar = wrapper!.findComponent(ChatInputBar)
    expect(inputBar.exists()).toBe(true)
    inputBar.vm.$emit('decide', 'run-1', 'appr-1', true, '同意')
    await flushPromises()

    expect(apiMock.approveRun).toHaveBeenCalledWith('run-1', 'appr-1', true, '同意')
    expect(vm().activeRun?.status).toBe('waiting_approval')
  })

  it('pending approvals queue above the input bar and advance one by one', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'running' })])
    await mountWorkspace()
    const source = MockEventSource.last()!
    source.emit(
      makeEvent({
        id: 2,
        event_type: 'approval_pending',
        payload: { approval_id: 'appr-1', action: 'crawl_real_platform', reason: '第一项' },
      }),
    )
    source.emit(
      makeEvent({
        id: 3,
        event_type: 'approval_pending',
        payload: { approval_id: 'appr-2', action: 'search_web', reason: '第二项' },
      }),
    )
    await flushPromises()

    // 队列一次只暴露队首：第一个审批 + 队列计数 2。
    const head = vm().approvalTarget
    expect(head).toMatchObject({ runId: 'run-1', queueCount: 2 })
    expect(head?.approval.id).toBe('appr-1')

    // 决定队首后：已决定的不再排队，下一个自动浮现。
    apiMock.approveRun.mockResolvedValue(makeRun({ status: 'pending' }))
    wrapper!.findComponent(ChatInputBar).vm.$emit('decide', 'run-1', 'appr-1', false, '')
    await flushPromises()

    expect(apiMock.approveRun).toHaveBeenCalledWith('run-1', 'appr-1', false, '')
    expect(vm().approvalTarget?.approval.id).toBe('appr-2')
    expect(vm().approvalTarget?.queueCount).toBe(1)
  })

  it('cancel run finalizes the active run', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'running' })])
    await mountWorkspace()

    const chatThread = wrapper!.findComponent(ChatThread)
    chatThread.vm.$emit('cancel', 'run-1')
    await flushPromises()

    expect(apiMock.cancelRun).toHaveBeenCalledWith('run-1')
    expect(vm().activeRun?.status).toBe('cancelled')
  })

  it('sendMessage pushes a pending run bubble and attaches SSE', async () => {
    await mountWorkspace()
    const chatInput = wrapper!.findComponent(ChatInputBar)
    chatInput.vm.$emit('send', '新一轮分析', true)
    await flushPromises()

    expect(apiMock.sendMessage).toHaveBeenCalledWith('case-1', '新一轮分析', true, undefined)
    expect(MockEventSource.instances).toHaveLength(1)
    const lastItem = vm().chatItems[vm().chatItems.length - 1]
    expect(lastItem?.type).toBe('run')
    if (lastItem?.type === 'run') expect(lastItem.run.status).toBe('pending')
  })

  it('resume attaches the resumed run', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'waiting_approval' })])
    await mountWorkspace()
    MockEventSource.instances = []

    apiMock.resumeRun.mockResolvedValue(makeRun({ status: 'running' }))
    const chatThread = wrapper!.findComponent(ChatThread)
    chatThread.vm.$emit('resume', 'run-1')
    await flushPromises()

    expect(apiMock.resumeRun).toHaveBeenCalledWith('run-1')
    // resumeRun 后 loadWorkspace 重建对话流，watch 为该 run 建立一条订阅
    // （per-run 订阅：同一 run 不会重复订阅）。
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.last()!.url).toContain('cursor=0')
    expect(vm().activeRun?.status).toBe('running')
  })
})

// ---------------------------------------------------------------------------
// Evidence 侧栏
// ---------------------------------------------------------------------------

describe('evidence sidebar', () => {
  it('toggles open and lazily loads the evidence summary once', async () => {
    await mountWorkspace()
    expect(apiMock.getEvidenceSummary).not.toHaveBeenCalled()

    await wrapper!.find('.evidence-toggle').trigger('click')
    await flushPromises()
    expect(vm().evidenceOpen).toBe(true)
    expect(apiMock.getEvidenceSummary).toHaveBeenCalledTimes(1)
    expect(apiMock.getEvidenceSummary).toHaveBeenCalledWith('case-1')

    // 关闭再打开：懒加载只触发一次。
    await wrapper!.find('.evidence-toggle').trigger('click')
    expect(vm().evidenceOpen).toBe(false)
    await wrapper!.find('.evidence-toggle').trigger('click')
    await flushPromises()
    expect(vm().evidenceOpen).toBe(true)
    expect(apiMock.getEvidenceSummary).toHaveBeenCalledTimes(1)
  })

  it('shows an error banner when the summary fails to load', async () => {
    apiMock.getEvidenceSummary.mockRejectedValue(new Error('boom'))
    await mountWorkspace()

    await wrapper!.find('.evidence-toggle').trigger('click')
    await flushPromises()
    expect(wrapper!.text()).toContain('证据汇总加载失败')
  })

  it('retries evidence summary after a failed load', async () => {
    apiMock.getEvidenceSummary
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({
        case_id: 'case-1',
        claims: [],
        unassigned: [],
      })
    await mountWorkspace()

    await wrapper!.find('.evidence-toggle').trigger('click')
    await flushPromises()
    expect(wrapper!.text()).toContain('证据汇总加载失败')

    await wrapper!.find('.error-banner button').trigger('click')
    await flushPromises()
    expect(apiMock.getEvidenceSummary).toHaveBeenCalledTimes(2)
    expect(wrapper!.text()).not.toContain('证据汇总加载失败')
  })
})

// ---------------------------------------------------------------------------
// 路由切换（同一组件实例复用）
// ---------------------------------------------------------------------------

describe('route switching', () => {
  it('reloads content when the route switches to another case', async () => {
    apiMock.listTurns.mockResolvedValue([makeTurn({ id: 'turn-1', content: '第一问' })])
    apiMock.listCaseRuns.mockResolvedValue([])
    apiMock.listArtifacts.mockResolvedValue([])
    await mountWorkspace()
    expect(apiMock.getCase).toHaveBeenCalledTimes(1)
    expect(vm().chatItems).toHaveLength(1)

    // 切换到另一个会话：caseId 从路由重取，内容应整体重载而不是残留旧会话。
    apiMock.getCase.mockResolvedValue({ ...CASE_RECORD, id: 'case-2', title: '第二个会话' })
    apiMock.listTurns.mockResolvedValue([
      makeTurn({ id: 'turn-2', case_id: 'case-2', content: '第二问' }),
    ])
    await router!.push('/cases/case-2')
    await flushPromises()

    expect(apiMock.getCase).toHaveBeenCalledTimes(2)
    expect(apiMock.listTurns).toHaveBeenLastCalledWith('case-2')
    const items = vm().chatItems
    expect(items).toHaveLength(1)
    if (items[0]?.type === 'turn') {
      expect(items[0].turn.case_id).toBe('case-2')
    }
  })

  it('resets local state when switching cases', async () => {
    await mountWorkspace()
    // 打开证据面板、拉一份汇总（只属于旧会话）。
    await wrapper!.find('.evidence-toggle').trigger('click')
    await flushPromises()
    expect(vm().evidenceOpen).toBe(true)

    apiMock.getCase.mockResolvedValue({ ...CASE_RECORD, id: 'case-2' })
    apiMock.listTurns.mockResolvedValue([])
    apiMock.listCaseRuns.mockResolvedValue([])
    apiMock.listArtifacts.mockResolvedValue([])
    apiMock.getEvidenceSummary.mockClear()
    await router!.push('/cases/case-2')
    await flushPromises()

    // 面板与旧证据汇总被清空；新会话的证据面板是全新状态。
    expect(vm().evidenceOpen).toBe(false)
    await wrapper!.find('.evidence-toggle').trigger('click')
    await flushPromises()
    expect(apiMock.getEvidenceSummary).toHaveBeenCalledTimes(1)
    expect(apiMock.getEvidenceSummary).toHaveBeenCalledWith('case-2')
  })
})

// ---------------------------------------------------------------------------
// Steering 与 Artifact 追问
// ---------------------------------------------------------------------------

describe('steering and artifact follow-up', () => {
  it('steers the active run through the input bar and clears the mode', async () => {
    await mountWorkspace()
    // 先发一条消息产生 pending run（attachRun 会进入运行指令模式）。
    const chatInput = wrapper!.findComponent(ChatInputBar)
    chatInput.vm.$emit('send', '先分析', false)
    await flushPromises()
    expect(vm().steerTarget?.id).toBe('run-1')

    chatInput.vm.$emit('steer', 'run-1', '请补充核查')
    await flushPromises()
    expect(apiMock.steerRun).toHaveBeenCalledWith('run-1', '请补充核查')
    expect(vm().steerTarget).toBeNull()
    expect(wrapper!.text()).toContain('运行指令已发送')
  })

  it('does not steer when the run reached a terminal state', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'completed' })])
    await mountWorkspace()
    expect(vm().steerTarget).toBeNull()
  })

  it('enters ask mode from an artifact and sends with artifact_id', async () => {
    await mountWorkspace()
    const chatThread = wrapper!.findComponent(ChatThread)
    chatThread.vm.$emit('ask-artifact', 'art-1')
    expect(vm().askTarget).toEqual({ artifactId: 'art-1' })

    const chatInput = wrapper!.findComponent(ChatInputBar)
    chatInput.vm.$emit('send', '解释这个结论', false, 'art-1')
    await flushPromises()
    expect(apiMock.sendMessage).toHaveBeenCalledWith('case-1', '解释这个结论', false, 'art-1')
    // 发送成功后退出追问模式。
    expect(vm().askTarget).toBeNull()
  })

  it('wires welcome-guide quick / evidence / fill-input outlets', async () => {
    await mountWorkspace()
    const chatThread = wrapper!.findComponent(ChatThread)

    chatThread.vm.$emit('quick')
    await flushPromises()
    // quickAnalyze → sendMessage(quickInstruction(), true)
    expect(apiMock.sendMessage).toHaveBeenCalledWith(
      'case-1',
      expect.stringContaining('执行完整舆情分析'),
      true,
      undefined,
    )

    chatThread.vm.$emit('open-evidence')
    await flushPromises()
    expect(vm().evidenceOpen).toBe(true)

    // fill-input 交给 ChatInputBar（stub 无 fill 方法，不应抛错）。
    chatThread.vm.$emit('fill-input', '聚焦传播源头')
  })

  it('enters steering mode from the run bubble 运行指令 button', async () => {
    apiMock.listCaseRuns.mockResolvedValue([
      makeRun({ id: 'run-1', turn_id: 'turn-1', status: 'running' }),
    ])
    await mountWorkspace()
    expect(vm().steerTarget?.id).toBe('run-1')

    // 模拟从 run 卡片进入指令模式（终止态 run 不可进入）。
    const chatThread = wrapper!.findComponent(ChatThread)
    chatThread.vm.$emit('enter-steer', 'run-1')
    expect(vm().steerTarget?.id).toBe('run-1')

    chatThread.vm.$emit('enter-steer', 'missing-run')
    expect(vm().steerTarget?.id).toBe('run-1')
  })
})

describe('empty / error / refresh recovery', () => {
  it('shows idle state with retry when the case fails to load', async () => {
    apiMock.getCase.mockRejectedValue(new Error('down'))
    await mountWorkspace()

    expect(wrapper!.find('.workspace-idle').exists()).toBe(true)
    expect(wrapper!.text()).toContain('无法加载案例')
    expect(wrapper!.find('.error-banner button').exists()).toBe(true)

    apiMock.getCase.mockResolvedValue(CASE_RECORD)
    await wrapper!.find('.error-banner button').trigger('click')
    await flushPromises()
    expect(wrapper!.text()).toContain('财报舆情')
    expect(wrapper!.find('.workspace-idle').exists()).toBe(false)
  })

  it('restores pending approvals from trace after a waiting_approval reload', async () => {
    apiMock.listCaseRuns.mockResolvedValue([makeRun({ status: 'waiting_approval' })])
    apiMock.getRunTrace.mockResolvedValue({
      ...TRACE,
      run: makeRun({ status: 'waiting_approval' }),
      approvals: [
        {
          id: 'appr-9',
          run_id: 'run-1',
          action: 'collect_social_posts',
          reason: '采集需批准',
          status: 'pending',
          request_payload: { platforms: ['weibo'] },
          decision_payload: {},
          decided_at: null,
          created_at: '2026-08-01T00:00:00Z',
        },
      ],
    })
    await mountWorkspace()
    await flushPromises()

    expect(apiMock.getRunTrace).toHaveBeenCalledWith('run-1')
    expect(vm().approvalTarget?.approval.id).toBe('appr-9')
    expect(vm().approvalTarget?.queueCount).toBe(1)
  })

  it('blocks send when LLM is not configured', async () => {
    apiMock.getCapabilities.mockResolvedValue({
      ...CAPABILITIES,
      llm_configured: false,
      llm: { provider: 'deepseek', configured: false, routes: {} },
    })
    await mountWorkspace()

    wrapper!.findComponent(ChatInputBar).vm.$emit('send', '请分析', false)
    await flushPromises()
    expect(apiMock.sendMessage).not.toHaveBeenCalled()
    expect(wrapper!.text()).toContain('未配置 LLM')
  })

  it('notifies the shell to refresh the case list after a successful load', async () => {
    const refreshCases = vi.fn().mockResolvedValue(undefined)
    await mountWorkspace({ refreshCases })
    expect(refreshCases).toHaveBeenCalled()
  })
})

describe('chat / debate mode slider', () => {
  it('defaults to chat mode and renders the chat thread, not the debate panel', async () => {
    await mountWorkspace()
    expect(vm().chatMode).toBe('chat')
    expect(wrapper!.findComponent(ChatThread).exists()).toBe(true)
    expect(wrapper!.findComponent({ name: 'DebatePanel' }).exists()).toBe(false)
  })

  it('switches the embedded debate view with the slider and binds the thumb state', async () => {
    await mountWorkspace()

    // 滑块第二个按钮 = 辩论
    const tabs = wrapper!.findAll('.mode-slider button')
    expect(tabs).toHaveLength(2)
    await tabs[1]!.trigger('click')

    expect(vm().chatMode).toBe('debate')
    // 视图与滑块绑定：对话流隐藏，辩论内嵌渲染，thumb 移到右侧
    expect(wrapper!.findComponent(ChatThread).exists()).toBe(false)
    expect(wrapper!.findComponent({ name: 'DebatePanel' }).exists()).toBe(true)
    expect(wrapper!.find('.slider-thumb.debate').exists()).toBe(true)

    // 切回对话：视图与 thumb 同步恢复
    await tabs[0]!.trigger('click')
    expect(vm().chatMode).toBe('chat')
    expect(wrapper!.findComponent(ChatThread).exists()).toBe(true)
    expect(wrapper!.findComponent({ name: 'DebatePanel' }).exists()).toBe(false)
  })

  it('locks the debate side until social platform data has been collected', async () => {
    apiMock.getPlatformComparison.mockResolvedValue({
      platforms: [],
      participation: [],
      sentiment: [],
      timeline: [],
      topic_terms: [],
      common_terms: [],
      insights: [],
    })
    await mountWorkspace()
    await flushPromises()

    // 无采集数据：辩论按钮禁用且带锁提示，点击不切换视图
    const tabs = wrapper!.findAll('.mode-slider button')
    expect((tabs[1]!.element as HTMLButtonElement).disabled).toBe(true)
    await tabs[1]!.trigger('click')
    expect(vm().chatMode).toBe('chat')
    expect(wrapper!.find('.lock-hint').exists()).toBe(true)
  })
})
