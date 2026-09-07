import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listFindingDebates: vi.fn(),
  createFindingDebate: vi.fn(),
  getDebate: vi.fn(),
}))

const findingApiMock = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  updateStatus: vi.fn(),
  sync: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))
vi.mock('@/services/api/findings', () => ({
  findingApi: findingApiMock,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { caseId: 'case-1' } }),
}))

import InvestigationFindingsView from './InvestigationFindingsView.vue'

function makeFinding(overrides: Record<string, unknown> = {}) {
  return {
    id: 'finding-1',
    case_id: 'case-1',
    kind: 'manual',
    title: '泄洪致灾结论',
    statement: '本次灾害主要由泄洪导致。',
    status: 'candidate',
    confidence: 0.83,
    attributes: {},
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

function makeDetail(finding = makeFinding()) {
  return {
    finding,
    evidence_links: [
      { evidence_ref: 'ev-1', relation: 'supports' },
    ],
    sources: [],
    review: null,
  }
}

function makeChallenge(overrides: Record<string, unknown> = {}) {
  return {
    id: 'challenge-1',
    case_id: 'case-1',
    title: '对抗性审查：泄洪致灾结论',
    status: 'completed',
    round: 4,
    mode: 'finding_challenge',
    finding_id: 'finding-1',
    platform_roles: { platforms: ['weibo'] },
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-01T10:00:00Z',
    ...overrides,
  }
}

describe('InvestigationFindingsView · adversarial challenge (M5.4/M5.5)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    findingApiMock.list.mockResolvedValue([makeFinding()])
    findingApiMock.get.mockResolvedValue(makeDetail())
    apiMock.listFindingDebates.mockResolvedValue([])
    apiMock.createFindingDebate.mockResolvedValue(
      makeChallenge({ status: 'in_progress', round: 1 }),
    )
    apiMock.getDebate.mockResolvedValue({
      ...makeChallenge(),
      context_snapshot: null,
      messages: [
        {
          id: 'm1',
          debate_id: 'challenge-1',
          role: 'moderator',
          platform: null,
          round: 4,
          content:
            '### 共识\n双方认可时间线。证据仍不足以确认因果，建议人工复核。',
          created_at: '2026-08-01T10:00:00Z',
        },
      ],
      votes: [],
    })
  })

  it('shows the start state when no challenge exists (M7-1)', async () => {
    const wrapper = mount(InvestigationFindingsView)
    await flushPromises()
    await wrapper.find('.ifind__card').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('对抗性审查')
    expect(wrapper.text()).toContain('尚未进行对抗性审查')
    expect(wrapper.text()).toContain('开始挑战')
  })

  it('creates a finding challenge and opens the modal (M7-2)', async () => {
    const wrapper = mount(InvestigationFindingsView)
    await flushPromises()
    await wrapper.find('.ifind__card').trigger('click')
    await flushPromises()
    await wrapper.find('.ifind__challenge-row .ifind__btn').trigger('click')
    await flushPromises()
    expect(apiMock.createFindingDebate).toHaveBeenCalledWith(
      'case-1',
      'finding-1',
    )
    expect(wrapper.findComponent({ name: 'FindingDebateModal' }).exists()).toBe(
      true,
    )
    // 死链提示不得出现（M5.5）
    expect(wrapper.text()).not.toContain('可在辩论记录中查看')
  })

  it('shows in-progress state with continue button (M7-3)', async () => {
    apiMock.listFindingDebates.mockResolvedValue([
      makeChallenge({ status: 'in_progress', round: 2 }),
    ])
    const wrapper = mount(InvestigationFindingsView)
    await flushPromises()
    await wrapper.find('.ifind__card').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('对抗性审查：进行中')
    expect(wrapper.text()).toContain('第 2 轮 / 4')
    expect(wrapper.text()).toContain('继续挑战')
  })

  it('shows completed summary with history count (M7-8)', async () => {
    apiMock.listFindingDebates.mockResolvedValue([makeChallenge()])
    const wrapper = mount(InvestigationFindingsView)
    await flushPromises()
    await wrapper.find('.ifind__card').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('最近一次挑战：已完成')
    expect(wrapper.text()).toContain('历史挑战 1 次')
    expect(wrapper.text()).toContain('查看结果')
    expect(wrapper.text()).toContain('发起新一轮挑战')
    expect(wrapper.text()).toContain('证据仍不足以确认因果')
  })
})
