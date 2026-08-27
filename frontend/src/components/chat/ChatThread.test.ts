import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatThread from './ChatThread.vue'

// 叶子组件在各自测试中覆盖，这里只测空状态引导卡的渲染与事件出口。
const stubs = {
  RunBubble: { template: '<div class="run-bubble-stub" />' },
  UserBubble: { template: '<div class="user-bubble-stub" />' },
  ArtifactCard: { template: '<div class="artifact-card-stub" />' },
}

describe('ChatThread welcome guide', () => {
  it('renders guide cards and example prompts on empty chat', () => {
    const wrapper = mount(ChatThread, { props: { items: [], guide: true }, global: { stubs } })
    expect(wrapper.text()).toContain('欢迎使用案例分析工作台')
    expect(wrapper.findAll('.guide-card')).toHaveLength(4)
    expect(wrapper.text()).toContain('快速完整分析')
    expect(wrapper.text()).toContain('查看案例证据')
    expect(wrapper.text()).toContain('指挥运行中任务')
    expect(wrapper.text()).toContain('追问分析成果')
    expect(wrapper.findAll('.prompt-chip')).toHaveLength(3)
    expect(wrapper.text()).toContain('核查辟谣时间线')
  })

  it('does not render the guide once the chat has items', () => {
    const wrapper = mount(ChatThread, {
      props: {
        guide: false,
        items: [
          {
            type: 'run',
            run: {
              id: 'run-1',
              case_id: 'case-1',
              turn_id: 'turn-1',
              parent_run_id: null,
              agent: 'coordinator',
              status: 'completed',
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
            },
            artifacts: [],
            approvals: [],
            trace: null,
            traceLoading: false,
            artifactsError: false,
            liveEvents: [],
            liveToolCalls: [],
            liveModelCalls: [],
          },
        ],
      },
      global: { stubs },
    })
    expect(wrapper.text()).not.toContain('欢迎使用案例分析工作台')
    expect(wrapper.findAll('.guide-card')).toHaveLength(0)
  })

  it('shows the guide when the case has a topic turn but no run yet', () => {
    const wrapper = mount(ChatThread, {
      props: {
        guide: true,
        items: [
          {
            type: 'turn',
            turn: {
              id: 'turn-1',
              case_id: 'case-1',
              role: 'user',
              content: '某地暴雨后出现谣言称水库泄洪，引发恐慌',
              created_at: '2026-08-01T00:00:00Z',
            },
          },
        ],
      },
      global: { stubs },
    })
    expect(wrapper.findAll('.guide-card')).toHaveLength(4)
    expect(wrapper.text()).toContain('欢迎使用案例分析工作台')
  })

  it('renders guide cards as display-only (not clickable)', async () => {
    const wrapper = mount(ChatThread, { props: { items: [], guide: true }, global: { stubs } })
    const cards = wrapper.findAll('.guide-card')
    expect(cards).toHaveLength(4)
    // 展示元素，不是 button，点击不产生任何事件
    for (const card of cards) {
      expect(card.element.tagName).not.toBe('BUTTON')
    }
    await cards[0]!.trigger('click')
    expect(wrapper.emitted('quick')).toBeUndefined()
    expect(wrapper.emitted('openEvidence')).toBeUndefined()
    expect(wrapper.emitted('guideAction')).toBeUndefined()
  })

  it('emits fillInput when an example prompt chip is clicked', async () => {
    const wrapper = mount(ChatThread, { props: { items: [], guide: true }, global: { stubs } })
    await wrapper.find('.prompt-chip').trigger('click')
    expect(wrapper.emitted('fillInput')?.[0]).toEqual(['聚焦传播源头与传播阶段'])
  })

  it('renders user turns right-side and assistant turns left-side', async () => {
    const makeTurn = (role: 'user' | 'assistant', content: string) => ({
      id: `t-${role}`,
      case_id: 'case-1',
      role,
      content,
      created_at: '2026-08-01T00:00:00Z',
    })
    const wrapper = mount(ChatThread, {
      props: {
        guide: false,
        items: [
          { type: 'turn', turn: makeTurn('user', '用户问题') },
          { type: 'turn', turn: makeTurn('assistant', 'Agent 回复内容') },
        ],
      },
      global: {
        stubs: {
          ...stubs,
          UserBubble: { template: '<div class="user-bubble-stub"><p class="content" /></div>' },
          AgentBubble: { template: '<div class="agent-bubble-stub"><p class="content" /></div>' },
        },
      },
    })
    // UserBubble stub 接收 turn；user turn 渲染右侧用户气泡组件
    expect(wrapper.find('.user-bubble-stub').exists()).toBe(true)
    expect(wrapper.find('.agent-bubble-stub').exists()).toBe(true)
    // 与顺序一致：第一个是 user turn
    expect(wrapper.findAll('.user-bubble-stub, .agent-bubble-stub')[0]!.classes()).toContain('user-bubble-stub')
  })

  it('does not render expert run objectives as user messages', async () => {
    // 专家子 run 的 objective 是系统委派提示词，不应出现在用户侧（气泡）。
    const makeRun = (overrides: { id: string; parent: string | null; agent: string }) => ({
      type: 'run' as const,
      run: {
        id: overrides.id,
        case_id: 'case-1',
        turn_id: `turn-${overrides.id}`,
        parent_run_id: overrides.parent,
        agent: overrides.agent,
        status: 'completed' as const,
        objective: `委派提示词：${overrides.agent}`,
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
      },
      artifacts: [],
      approvals: [],
      trace: null,
      traceLoading: false,
      artifactsError: false,
      liveEvents: [],
      liveToolCalls: [],
      liveModelCalls: [],
      finalContent: undefined,
    })
    const wrapper = mount(ChatThread, {
      props: {
        guide: false,
        items: [
          makeRun({ id: 'expert-1', parent: 'root-1', agent: 'opinion' }),
          makeRun({ id: 'root-1', parent: null, agent: 'coordinator' }),
        ],
      },
      global: {
        stubs: {
          ...stubs,
          UserBubble: {
            props: ['content'],
            template: '<div class="user-bubble-stub">{{ content }}</div>',
          },
        },
      },
    })
    // 顶层 run 的 objective 渲染为用户气泡
    expect(wrapper.text()).toContain('委派提示词：coordinator')
    // 专家子 run 的 objective 不作为用户消息展示
    expect(wrapper.text()).not.toContain('委派提示词：opinion')
    expect(wrapper.findAll('.user-bubble-stub')).toHaveLength(1)
  })
})
