import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AgentBubble from './AgentBubble.vue'

const LONG_CONTENT = '这是一条很长的 Agent 输出。'.repeat(20)

function makeTurn(content: string) {
  return {
    id: 'turn-1',
    case_id: 'case-1',
    role: 'assistant',
    content,
    created_at: '2026-09-02T00:00:00Z',
  }
}

describe('AgentBubble collapse', () => {
  it('renders content expanded by default', () => {
    const wrapper = mount(AgentBubble, { props: { turn: makeTurn('回答内容') } })
    expect(wrapper.find('.agent-bubble__body').isVisible()).toBe(true)
    expect(wrapper.text()).toContain('回答内容')
  })

  it('collapses content when the toggle is clicked and shows a summary', async () => {
    const wrapper = mount(AgentBubble, { props: { turn: makeTurn(LONG_CONTENT) } })
    await wrapper.find('.agent-bubble__toggle').trigger('click')
    expect(wrapper.find('.agent-bubble__body').isVisible()).toBe(false)
    expect(wrapper.find('.agent-bubble__summary').exists()).toBe(true)
    expect(wrapper.find('.agent-bubble__summary').text()).toContain('…')
  })

  it('expands again on second click', async () => {
    const wrapper = mount(AgentBubble, { props: { turn: makeTurn('再次展开') } })
    const toggle = wrapper.find('.agent-bubble__toggle')
    await toggle.trigger('click')
    await toggle.trigger('click')
    expect(wrapper.find('.agent-bubble__body').isVisible()).toBe(true)
    expect(wrapper.text()).toContain('再次展开')
  })
})
