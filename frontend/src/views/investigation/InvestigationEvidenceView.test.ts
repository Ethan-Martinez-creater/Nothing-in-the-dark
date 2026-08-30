import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  getEvidenceSummary: vi.fn(),
  reviewClaim: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

const setUiContext = vi.fn()
vi.mock('@/composables/useInvestigationContext', () => ({
  useInvestigationContext: () => ({ setUiContext }),
}))

vi.mock('@/components/semantics/SemanticAnnotationsPanel.vue', () => ({
  default: {
    name: 'SemanticAnnotationsPanel',
    template: '<div data-stub="semantics" />',
    props: ['caseId'],
  },
}))

vi.mock('@/components/evidence/UnassignedEvidenceList.vue', () => ({
  default: {
    name: 'UnassignedEvidenceList',
    template: `<ul data-stub="unassigned"><li class="uev__item" v-for="item in items" :key="item.id" @click="$emit('select', item)">{{ item.excerpt }}</li></ul>`,
    props: ['items'],
    emits: ['select'],
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { caseId: 'case-1' } }),
}))

import InvestigationEvidenceView from './InvestigationEvidenceView.vue'
import type { EvidenceSummary } from '@/types/api'

function makeSummary(): EvidenceSummary {
  return {
    case_id: 'case-1',
    claims: [
      {
        id: 'claim-1',
        text: '官方已确认延期开学',
        status: 'pending',
        verdict: '',
        confidence: 0.7,
        created_at: '2026-08-01T00:00:00+00:00',
        evidence: [
          {
            id: 'ev-1',
            case_id: 'case-1',
            claim_id: 'claim-1',
            source_type: 'social_post',
            source_id: 'p-1',
            stance: 'support',
            excerpt: '公告原文摘录',
            relevance: 0.92,
            metadata_json: { platform: 'weibo' },
            created_at: '2026-08-01T00:00:00+00:00',
          },
        ],
      },
      {
        id: 'claim-2',
        text: '网传补偿方案已落地',
        status: 'verified',
        verdict: 'supported',
        confidence: 0.8,
        created_at: '2026-08-01T00:00:00+00:00',
        evidence: [],
      },
    ],
    unassigned: [
      {
        id: 'ev-u1',
        case_id: 'case-1',
        claim_id: null,
        source_type: 'social_post',
        source_id: 'p-9',
        stance: 'context',
        excerpt: '未分组证据',
        relevance: 0.4,
        metadata_json: {},
        created_at: '2026-08-01T00:00:00+00:00',
      },
    ],
  }
}

describe('InvestigationEvidenceView', () => {
  beforeEach(() => {
    apiMock.getEvidenceSummary.mockReset()
    apiMock.getEvidenceSummary.mockResolvedValue(makeSummary())
    apiMock.reviewClaim.mockReset()
    setUiContext.mockReset()
  })

  it('loads summary and shows claim workspace with filter', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    expect(apiMock.getEvidenceSummary).toHaveBeenCalledWith('case-1')
    const text = wrapper.text()
    expect(text).toContain('全部')
    expect(text).toContain('待核查')
    expect(text).toContain('已核实')
    expect(text).toContain('已剔除')
    expect(text).toContain('官方已确认延期开学')
    // 未分组证据计数可见
    expect(text).toContain('未分组证据 1')
  })

  it('filters claims by status', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    const verifiedButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '已核实')
    await verifiedButton!.trigger('click')
    expect(wrapper.text()).toContain('网传补偿方案已落地')
    expect(wrapper.text()).not.toContain('官方已确认延期开学')
  })

  it('sends claim selection into copilot context', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    await wrapper.find('.ecl__claim').trigger('click')
    expect(setUiContext).toHaveBeenCalledWith({
      workspace: 'evidence',
      selected_type: 'claim',
      selected_id: 'claim-1',
    })
  })

  it('sends evidence selection into copilot context', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    await wrapper.find('.ecl__item').trigger('click')
    expect(setUiContext).toHaveBeenCalledWith({
      workspace: 'evidence',
      selected_type: 'evidence',
      selected_id: 'ev-1',
    })
  })

  it('shows error state when summary fetch fails', async () => {
    apiMock.getEvidenceSummary.mockRejectedValue(new Error('boom'))
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    expect(wrapper.text()).toContain('证据加载失败')
  })

  it('shows semantics panel on semantics tab (C9.1)', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    const tabs = wrapper.findAll('.iev__tab')
    await tabs[1]!.trigger('click')
    const stub = wrapper.find('[data-stub="semantics"]')
    expect(stub.exists()).toBe(true)
    expect(stub.attributes('caseid')).toBeUndefined() // caseId 经 prop 传递
    expect(
      wrapper.findComponent({ name: 'SemanticAnnotationsPanel' }).props('caseId'),
    ).toBe('case-1')
  })

  it('shows empty guide when no evidence exists', async () => {
    apiMock.getEvidenceSummary.mockResolvedValue({
      case_id: 'case-1',
      claims: [],
      unassigned: [],
    })
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    expect(wrapper.text()).toContain('尚无证据')
  })

  it('shows the unassigned scope switch with counts (FC3)', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('Claims (2)')
    expect(text).toContain('Unassigned (1)')
    // 默认 scope 是 Claims，未分组列表不渲染
    expect(wrapper.find('[data-stub="unassigned"]').exists()).toBe(false)
  })

  it('switches to unassigned scope and shows unassigned excerpts (FC3)', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    const unassignedButton = wrapper
      .findAll('button')
      .find((button) => button.text().startsWith('Unassigned'))
    await unassignedButton!.trigger('click')
    const stub = wrapper.find('[data-stub="unassigned"]')
    expect(stub.exists()).toBe(true)
    expect(stub.text()).toContain('未分组证据')
  })

  it('sends unassigned evidence selection into copilot context (FC3)', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    const unassignedButton = wrapper
      .findAll('button')
      .find((button) => button.text().startsWith('Unassigned'))
    await unassignedButton!.trigger('click')
    await wrapper.find('.uev__item').trigger('click')
    expect(setUiContext).toHaveBeenCalledWith({
      workspace: 'evidence',
      selected_type: 'evidence',
      selected_id: 'ev-u1',
    })
    // DetailPanel 收到 item（claim 为 null），沿用现有 item 模式
    const detail = wrapper.findComponent({ name: 'EvidenceDetailPanel' })
    expect(detail.props('item')).toMatchObject({ id: 'ev-u1' })
    expect(detail.props('claim')).toBeNull()
  })

  it('guides to unassigned when claims are empty but unassigned exist (FC3)', async () => {
    apiMock.getEvidenceSummary.mockResolvedValue({
      case_id: 'case-1',
      claims: [],
      unassigned: [
        {
          id: 'ev-u1',
          case_id: 'case-1',
          claim_id: null,
          source_type: 'social_post',
          source_id: 'p-9',
          stance: 'context',
          excerpt: '未分组证据',
          relevance: 0.4,
          metadata_json: {},
          created_at: '2026-08-01T00:00:00+00:00',
        },
      ],
    })
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    const text = wrapper.text()
    // 不再误导为"尚无证据"：提示可切到 Unassigned，且数据可见
    expect(text).toContain('暂无已归组主张')
    expect(text).not.toContain('尚无证据')
    const unassignedButton = wrapper
      .findAll('button')
      .find((button) => button.text().startsWith('Unassigned'))
    await unassignedButton!.trigger('click')
    expect(wrapper.find('[data-stub="unassigned"]').text()).toContain('未分组证据')
  })

  it('keeps semantics tab unaffected by the scope switch (FC3)', async () => {
    const wrapper = mount(InvestigationEvidenceView)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().startsWith('Unassigned'))!
      .trigger('click')
    const tabs = wrapper.findAll('.iev__tab')
    await tabs[1]!.trigger('click')
    expect(wrapper.find('[data-stub="semantics"]').exists()).toBe(true)
  })
})
