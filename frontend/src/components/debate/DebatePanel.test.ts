import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listDebates: vi.fn(),
  getDebate: vi.fn(),
  createDebate: vi.fn(),
  advanceDebate: vi.fn(),
  addDebateMessage: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import DebatePanel from './DebatePanel.vue'

function makeDebate(overrides: Record<string, unknown> = {}) {
  return {
    id: 'debate-1',
    case_id: 'case-1',
    title: '多平台观点辩论',
    status: 'in_progress',
    round: 1,
    platform_roles: { platforms: ['weibo', 'bilibili'] },
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    messages: [
      {
        id: 'm1',
        debate_id: 'debate-1',
        role: 'platform_role',
        platform: 'weibo',
        round: 1,
        content: '微博视角：首发信息可信度中等',
        created_at: '2026-08-01T00:00:00Z',
      },
    ],
    votes: [],
    ...overrides,
  }
}

describe('DebatePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.listDebates.mockResolvedValue([])
    apiMock.getDebate.mockResolvedValue(makeDebate())
    apiMock.createDebate.mockResolvedValue(makeDebate())
    apiMock.advanceDebate.mockResolvedValue(makeDebate({ round: 2 }))
    apiMock.addDebateMessage.mockResolvedValue({ id: 'm2' })
  })

  it('shows empty state with a start button when no debate exists', async () => {
    const wrapper = mount(DebatePanel, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('发起辩论')
    expect(wrapper.text()).toContain('观点陈述')
  })

  it('creates a debate and loads its detail', async () => {
    const wrapper = mount(DebatePanel, { props: { caseId: 'case-1' } })
    await flushPromises()
    await wrapper.find('.primary-button').trigger('click')
    await flushPromises()
    expect(apiMock.createDebate).toHaveBeenCalledWith('case-1')
    expect(apiMock.getDebate).toHaveBeenCalled()
  })

  it('renders platform role messages with labels', async () => {
    apiMock.listDebates.mockResolvedValue([makeDebate()])
    apiMock.getDebate.mockResolvedValue(makeDebate())
    const wrapper = mount(DebatePanel, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('微博')
    expect(wrapper.text()).toContain('微博视角：首发信息可信度中等')
    expect(wrapper.text()).toContain('第 1 轮')
  })

  it('advances rounds through the advance button', async () => {
    apiMock.listDebates.mockResolvedValue([makeDebate()])
    apiMock.getDebate.mockResolvedValue(makeDebate())
    const wrapper = mount(DebatePanel, { props: { caseId: 'case-1' } })
    await flushPromises()
    await wrapper.find('.debate-advance').trigger('click')
    await flushPromises()
    expect(apiMock.advanceDebate).toHaveBeenCalledWith('debate-1')
  })

  it('sends user interjections', async () => {
    apiMock.listDebates.mockResolvedValue([makeDebate()])
    apiMock.getDebate.mockResolvedValue(makeDebate())
    const wrapper = mount(DebatePanel, { props: { caseId: 'case-1' } })
    await flushPromises()
    await wrapper.find('.chat-textarea').setValue('我认为官方通报更可信')
    await wrapper.find('.send-button').trigger('click')
    await flushPromises()
    expect(apiMock.addDebateMessage).toHaveBeenCalledWith(
      'debate-1',
      '我认为官方通报更可信',
    )
  })

  it('retries the debate list after a load failure', async () => {
    apiMock.listDebates.mockRejectedValueOnce(new Error('down'))
    const wrapper = mount(DebatePanel, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('辩论列表加载失败')
    apiMock.listDebates.mockResolvedValue([])
    await wrapper.find('.modal-error button').trigger('click')
    await flushPromises()
    expect(apiMock.listDebates).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('发起辩论')
  })
})

it('offers a restart button after the debate completes', async () => {
  apiMock.listDebates.mockResolvedValue([makeDebate({ status: 'completed', round: 4 })])
  apiMock.getDebate.mockResolvedValue(makeDebate({ status: 'completed', round: 4 }))
  const wrapper = mount(DebatePanel, { props: { caseId: 'case-1' } })
  await flushPromises()
  const restart = wrapper.find('.debate-restart')
  expect(restart.exists()).toBe(true)
  await restart.trigger('click')
  await flushPromises()
  expect(apiMock.createDebate).toHaveBeenCalledWith('case-1')
})
