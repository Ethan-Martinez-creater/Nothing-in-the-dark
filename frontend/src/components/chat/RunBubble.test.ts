import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { AgentRun, ModelCallTrace, RunEvent, ToolCallTrace } from '@/types/api'

import RunBubble from './RunBubble.vue'

function makeRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: 'run-1',
    case_id: 'case-1',
    turn_id: null,
    parent_run_id: null,
    agent: 'coordinator',
    status: 'running',
    objective: '分析一下',
    model_route: 'fast',
    input_tokens: 100,
    output_tokens: 50,
    tool_call_count: 1,
    estimated_cost: 0.01,
    error_code: null,
    error: null,
    metadata_json: {},
    created_at: '2026-08-16T10:00:00Z',
    updated_at: '2026-08-16T10:00:20Z',
    ...overrides,
  }
}

function makeModelCall(overrides: Partial<ModelCallTrace> = {}): ModelCallTrace {
  return {
    id: 'mc-1',
    run_id: 'run-1',
    model: 'deepseek-v4-flash',
    route: 'fast',
    status: 'completed',
    input_tokens: 400,
    cached_input_tokens: 0,
    output_tokens: 120,
    estimated_cost: 0.001,
    currency: 'CNY',
    pricing_model: 'deepseek-v4-flash',
    latency_ms: 1800,
    error_code: null,
    created_at: '2026-08-16T10:00:02Z',
    ...overrides,
  }
}

function makeToolCall(overrides: Partial<ToolCallTrace> = {}): ToolCallTrace {
  return {
    id: 'tc-1',
    run_id: 'run-1',
    tool_name: 'search_social_evidence',
    skill_name: null,
    status: 'completed',
    arguments: {},
    result: {},
    error_code: null,
    input_summary: '关键词：某事件',
    output_summary: '命中 12 条',
    retry_count: 0,
    duration_ms: 2400,
    estimated_cost: 0,
    idempotency_key: null,
    approval_id: null,
    rag: { available: true, hit_count: 12, retrieval_modes: ['vector', 'keyword'] },
    started_at: '2026-08-16T10:00:05Z',
    finished_at: '2026-08-16T10:00:07Z',
    ...overrides,
  }
}

function makeEvent(id: number, overrides: Partial<RunEvent> = {}): RunEvent {
  return {
    id,
    run_id: 'run-1',
    event_type: 'context_built',
    agent: 'coordinator',
    skill: null,
    tool_call_id: null,
    tool: null,
    status: 'ok',
    payload: {},
    created_at: '2026-08-16T10:00:01Z',
    ...overrides,
  }
}

type RunBubbleProps = InstanceType<typeof RunBubble>['$props']

function mountBubble(extra: Partial<RunBubbleProps> = {}) {
  return mount(RunBubble, {
    props: {
      run: makeRun(),
      artifacts: [],
      trace: null,
      traceLoading: false,
      artifactsError: false,
      ...extra,
    } as RunBubbleProps,
  })
}

describe('RunBubble（ChatGPT 式过程 + 回答）', () => {
  it('运行中：折叠头显示正在思考、时间线直播过程、占位等待回答', () => {
    const wrapper = mountBubble({
      liveEvents: [makeEvent(1)],
      liveModelCalls: [
        makeModelCall({ id: 'mc-1', status: 'started', latency_ms: 0, created_at: '2026-08-16T10:00:02Z' }),
        makeModelCall({ id: 'mc-2', created_at: '2026-08-16T10:00:08Z' }),
      ],
      liveToolCalls: [makeToolCall()],
    })

    // 运行中思考面板默认展开
    expect(wrapper.find('.think-panel.open').exists()).toBe(true)
    expect(wrapper.text()).toContain('正在思考…')
    // 工具行使用面向用户的动作名并展示 RAG 命中
    expect(wrapper.text()).toContain('检索社交证据')
    expect(wrapper.text()).toContain('RAG 命中 12 条')
    // 无最终回答时展示生成占位，而不是空白
    expect(wrapper.text()).toContain('正在生成回答')
  })

  it('完成后：折叠为单行摘要，最终回答以 Markdown 加粗大字号渲染', async () => {
    const wrapper = mountBubble({
      run: makeRun({ status: 'completed' }),
      finalContent: '**结论**：证据支持该主张',
      liveModelCalls: [
        makeModelCall({ id: 'mc-1' }),
        makeModelCall({ id: 'mc-2', created_at: '2026-08-16T10:00:08Z' }),
      ],
      liveToolCalls: [makeToolCall()],
    })

    // 终态默认折叠为一行摘要，不再平铺过程
    expect(wrapper.text()).toContain('已深度思考 · 3 个步骤')
    expect(wrapper.find('.think-timeline').exists()).toBe(false)
    // 回答在过程之后渲染为 Markdown
    expect(wrapper.find('.run-answer').html()).toContain('<strong>结论</strong>')
    expect(wrapper.text()).not.toContain('正在生成回答')

    // 点击折叠头可回看过程时间线
    await wrapper.find('.think-toggle').trigger('click')
    expect(wrapper.find('.think-timeline').exists()).toBe(true)
  })

  it('时间线按发生时间合并模型思考、工具与事件', () => {
    const wrapper = mountBubble({
      liveEvents: [makeEvent(1, { event_type: 'context_built', created_at: '2026-08-16T10:00:03Z' })],
      liveModelCalls: [makeModelCall({ created_at: '2026-08-16T10:00:02Z' })],
      liveToolCalls: [makeToolCall({ started_at: '2026-08-16T10:00:01Z', finished_at: '2026-08-16T10:00:01Z' })],
    })

    const titles = wrapper.findAll('.think-row .row-title').map((row) => row.text())
    expect(titles).toHaveLength(3)
    expect(titles[0]).toContain('检索社交证据')
    expect(titles[1]).toContain('思考')
    expect(titles[2]).toContain('组装上下文')
  })

  it('失败的工具调用在时间线中标红并保留原始错误信息', () => {
    const wrapper = mountBubble({
      liveToolCalls: [
        makeToolCall({ status: 'failed', output_summary: null, error_code: 'crawl_timeout' }),
      ],
    })

    const failedRow = wrapper.findAll('.think-row').find((row) => row.classes().includes('is-failed'))
    expect(failedRow).toBeDefined()
    expect(failedRow!.text()).toContain('检索社交证据')
  })

  it('等待审批时展示审批状态与恢复入口', () => {
    const wrapper = mountBubble({ run: makeRun({ status: 'waiting_approval' }) })

    expect(wrapper.text()).toContain('等待你的审批')
    const buttons = wrapper.findAll('button').map((button) => button.text())
    expect(buttons).toContain('恢复')
  })

  it('运行中可取消并转发 runId', async () => {
    const wrapper = mountBubble()

    await wrapper.findAll('button').find((button) => button.text() === '取消')!.trigger('click')
    expect(wrapper.emitted('cancel')).toEqual([['run-1']])
  })

  it('失败 run 展示可读错误，而不是空白回答', () => {
    const wrapper = mountBubble({
      run: makeRun({
        status: 'failed',
        error: '模型网关不可用',
        error_code: 'llm_unavailable',
      }),
    })

    expect(wrapper.text()).toContain('执行失败')
    expect(wrapper.text()).toContain('模型网关不可用')
    expect(wrapper.text()).not.toContain('正在生成回答')
  })

  it('终态 run 展开时自动加载执行记录（无需手动点加载按钮）', async () => {
    const wrapper = mountBubble({
      run: makeRun({ status: 'completed' }),
      finalContent: '结论',
      trace: null,
      traceLoading: false,
    })

    // 终态折叠；trace 尚未加载，也未请求过
    expect(wrapper.find('.think-timeline').exists()).toBe(false)
    expect(wrapper.emitted('loadTrace')).toBeUndefined()

    // 展开折叠头：应自动请求 trace，而不是停留在「暂无执行记录」
    await wrapper.find('.think-toggle').trigger('click')
    expect(wrapper.emitted('loadTrace')).toEqual([['run-1']])
  })
})
