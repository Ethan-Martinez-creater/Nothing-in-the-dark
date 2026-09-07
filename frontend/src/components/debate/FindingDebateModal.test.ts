import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listFindingDebates: vi.fn(),
  createFindingDebate: vi.fn(),
  getDebate: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import FindingDebateModal from './FindingDebateModal.vue'

function makeChallenge(overrides: Record<string, unknown> = {}) {
  return {
    id: 'challenge-1',
    case_id: 'case-1',
    title: '对抗性审查：泄洪致灾结论',
    status: 'completed',
    round: 4,
    mode: 'finding_challenge',
    finding_id: 'finding-1',
    platform_roles: { platforms: ['weibo', 'bilibili'] },
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-01T10:00:00Z',
    ...overrides,
  }
}

describe('FindingDebateModal (M5.2/M5.6)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.listFindingDebates.mockResolvedValue([makeChallenge()])
    apiMock.createFindingDebate.mockResolvedValue(
      makeChallenge({ id: 'challenge-2', status: 'in_progress', round: 1 }),
    )
    apiMock.getDebate.mockResolvedValue({
      ...makeChallenge(),
      context_snapshot: null,
      messages: [],
      votes: [],
    })
  })

  function mountModal(props: Record<string, unknown> = {}) {
    return mount(FindingDebateModal, {
      props: {
        caseId: 'case-1',
        findingId: 'finding-1',
        findingTitle: '泄洪致灾结论',
        findingStatus: '候选',
        debateId: 'challenge-1',
        ...props,
      },
    })
  }

  it('renders header with finding title and status', async () => {
    const wrapper = mountModal()
    await flushPromises()
    expect(wrapper.text()).toContain('对抗性审查')
    expect(wrapper.text()).toContain('泄洪致灾结论')
    expect(wrapper.text()).toContain('候选')
  })

  it('shows challenge history with time, status and round', async () => {
    const wrapper = mountModal()
    await flushPromises()
    expect(apiMock.listFindingDebates).toHaveBeenCalledWith(
      'case-1',
      'finding-1',
    )
    expect(wrapper.text()).toContain('历史挑战 1 次')
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('第 4 轮 / 4')
  })

  it('starts a new challenge via create-or-resume API', async () => {
    const wrapper = mountModal()
    await flushPromises()
    await wrapper.find('.fdm__primary').trigger('click')
    await flushPromises()
    expect(apiMock.createFindingDebate).toHaveBeenCalledWith(
      'case-1',
      'finding-1',
    )
    expect(apiMock.listFindingDebates).toHaveBeenCalledTimes(2)
  })

  it('views a completed history item read-only by switching active debate', async () => {
    apiMock.listFindingDebates.mockResolvedValue([
      makeChallenge({
        id: 'challenge-0',
        status: 'in_progress',
        round: 2,
        created_at: '2026-08-02T10:00:00Z',
      }),
      makeChallenge(),
    ])
    const wrapper = mountModal({ debateId: 'challenge-0' })
    await flushPromises()
    const buttons = wrapper.findAll('.fdm__history-btn')
    expect(buttons.length).toBe(2)
    const completedButton = buttons[1]
    expect(completedButton).toBeDefined()
    await completedButton!.trigger('click')
    await flushPromises()
    expect(apiMock.getDebate).toHaveBeenCalledWith('challenge-1')
  })

  it('shows empty history hint when no challenges exist', async () => {
    apiMock.listFindingDebates.mockResolvedValue([])
    const wrapper = mountModal({ debateId: null })
    await flushPromises()
    expect(wrapper.text()).toContain('尚未进行对抗性审查')
    expect(wrapper.text()).toContain('开始挑战')
  })
})
