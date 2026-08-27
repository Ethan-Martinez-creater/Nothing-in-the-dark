import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { AgentRun } from '@/types/api'

import ChatInputBar from './ChatInputBar.vue'

function makeRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: 'run-1',
    case_id: 'case-1',
    turn_id: 'turn-1',
    parent_run_id: null,
    agent: 'coordinator',
    status: 'running',
    objective: '请分析该案例',
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

async function typeAndSubmit(wrapper: ReturnType<typeof mount>, text: string) {
  await wrapper.find('.chat-textarea').setValue(text)
  await wrapper.find('.send-button').trigger('click')
}

describe('ChatInputBar', () => {
  it('blocks send and quick analyze when LLM is not configured', async () => {
    const wrapper = mount(ChatInputBar, {
      props: { sending: false, realCrawl: false, llmConfigured: false },
    })
    expect(wrapper.text()).toContain('未配置大模型')
    expect(wrapper.find('.send-button').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.quick-button').attributes('disabled')).toBeDefined()
    await typeAndSubmit(wrapper, '帮我分析')
    expect(wrapper.emitted('send')).toBeUndefined()
    await wrapper.find('.quick-button').trigger('click')
    expect(wrapper.emitted('quick')).toBeUndefined()
  })

  it('sends a normal message with approveCrawl flag', async () => {
    const wrapper = mount(ChatInputBar, {
      props: { sending: false, realCrawl: false, steerTarget: null, askTarget: null },
    })
    await typeAndSubmit(wrapper, '帮我分析')
    expect(wrapper.emitted('send')?.[0]).toEqual(['帮我分析', false, undefined])
  })

  it('emits steer with the target run id in steering mode', async () => {
    const run = makeRun()
    const wrapper = mount(ChatInputBar, {
      props: { sending: false, realCrawl: false, steerTarget: run, askTarget: null },
    })
    expect(wrapper.text()).toContain('运行指令模式')
    expect(wrapper.find('.send-button').text()).toContain('发指令')
    await typeAndSubmit(wrapper, '请补充核查')
    expect(wrapper.emitted('steer')?.[0]).toEqual(['run-1', '请补充核查'])
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('emits send with artifact_id in ask mode', async () => {
    const wrapper = mount(ChatInputBar, {
      props: {
        sending: false,
        realCrawl: false,
        steerTarget: null,
        askTarget: { artifactId: 'art-9' },
      },
    })
    expect(wrapper.text()).toContain('追问模式')
    await typeAndSubmit(wrapper, '解释这个结论')
    expect(wrapper.emitted('send')?.[0]).toEqual(['解释这个结论', false, 'art-9'])
  })

  it('steering mode takes precedence over ask mode', async () => {
    const wrapper = mount(ChatInputBar, {
      props: {
        sending: false,
        realCrawl: false,
        steerTarget: makeRun(),
        askTarget: { artifactId: 'art-9' },
      },
    })
    await typeAndSubmit(wrapper, '优先核查')
    expect(wrapper.emitted('steer')).toHaveLength(1)
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('hides the ask banner while steering is active (mode exclusivity)', async () => {
    const wrapper = mount(ChatInputBar, {
      props: {
        sending: false,
        realCrawl: false,
        steerTarget: makeRun(),
        askTarget: { artifactId: 'art-9' },
      },
    })
    expect(wrapper.text()).toContain('运行指令模式')
    expect(wrapper.text()).not.toContain('追问模式')
    expect(wrapper.findAll('.steer-banner')).toHaveLength(1)
  })

  it('emits cancelSteer in steering mode / cancelAsk in ask mode', async () => {
    const steerWrapper = mount(ChatInputBar, {
      props: { sending: false, realCrawl: false, steerTarget: makeRun(), askTarget: null },
    })
    await steerWrapper.find('.steer-banner button').trigger('click')
    expect(steerWrapper.emitted('cancelSteer')).toHaveLength(1)

    const askWrapper = mount(ChatInputBar, {
      props: { sending: false, realCrawl: false, steerTarget: null, askTarget: { artifactId: 'art-9' } },
    })
    await askWrapper.find('.steer-banner button').trigger('click')
    expect(askWrapper.emitted('cancelAsk')).toHaveLength(1)
  })

  it('disables quick analyze in steering mode', async () => {
    const wrapper = mount(ChatInputBar, {
      props: { sending: false, realCrawl: false, steerTarget: makeRun(), askTarget: null },
    })
    expect(wrapper.find('.quick-button').attributes('disabled')).toBeDefined()
  })

  it('exposes fill() to prefill the input from the welcome guide', async () => {
    const wrapper = mount(ChatInputBar, {
      props: { sending: false, realCrawl: false, steerTarget: null, askTarget: null },
    })
    wrapper.vm.fill('聚焦传播源头与传播阶段')
    await wrapper.vm.$nextTick()
    expect((wrapper.find('.chat-textarea').element as HTMLTextAreaElement).value).toBe(
      '聚焦传播源头与传播阶段',
    )
    // 填入后发送按钮启用
    expect(wrapper.find('.send-button').attributes('disabled')).toBeUndefined()
  })
})
