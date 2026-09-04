import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { caseId: 'case-1' } }),
}))

const apiMock = vi.hoisted(() => ({
  getCase: vi.fn(),
  listCaseRuns: vi.fn(),
  listArtifacts: vi.fn(),
  updateCase: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: apiMock }))

const collectionRunApiMock = vi.hoisted(() => ({ list: vi.fn(), cancel: vi.fn() }))
vi.mock('@/services/api/collectionRuns', () => ({
  collectionRunApi: collectionRunApiMock,
  isActiveCollectionRun: () => true,
}))

const qualityApiMock = vi.hoisted(() => ({ get: vi.fn(), refresh: vi.fn() }))
const crossApiMock = vi.hoisted(() => ({ related: vi.fn() }))
vi.mock('@/services/api/intelligence', () => ({
  qualityApi: qualityApiMock,
  crossApi: crossApiMock,
}))

vi.mock('@/components/collection/CollectionDefinitionCard.vue', () => ({
  default: { name: 'CollectionDefinitionCard', template: '<div data-stub="definition" />' },
}))
vi.mock('@/components/collection/CollectionRunCard.vue', () => ({
  default: { name: 'CollectionRunCard', template: '<div data-stub="run" />' },
}))
vi.mock('@/components/goals/GoalPlanPanel.vue', () => ({
  default: { name: 'GoalPlanPanel', template: '<div data-stub="plan" />' },
}))
vi.mock('@/components/intelligence/RelatedInvestigationsCard.vue', () => ({
  default: {
    name: 'RelatedInvestigationsCard',
    props: ['caseId'],
    template: '<div data-stub="related" />',
  },
}))

import InvestigationOverviewView from './InvestigationOverviewView.vue'

function makeQuality() {
  return {
    case_id: 'case-1',
    overall_score: 71.2,
    grade: 'acceptable',
    dimensions: [
      { key: 'collection', label: '数据采集', weight: 25, score: 0.8, available: true, metrics: {} },
    ],
    gaps: [
      { code: 'g1', severity: 'warning', object_type: 'finding', object_id: null, message: '结论缺少支撑链接', action: {} },
    ],
    warnings: [],
    disclaimer: 'Quality Score 表示调查完整度与准备度，不代表事实真实性。',
    computed_at: '2026-09-01T10:00:00+00:00',
    algorithm_version: 'quality-1.0.0',
    input_fingerprint: 'fp123',
  }
}

describe('InvestigationOverviewView', () => {
  beforeEach(() => {
    apiMock.getCase.mockReset()
    apiMock.listCaseRuns.mockReset()
    apiMock.listArtifacts.mockReset()
    collectionRunApiMock.list.mockReset()
    collectionRunApiMock.cancel.mockReset()
    qualityApiMock.get.mockReset()
    qualityApiMock.refresh.mockReset()
    crossApiMock.related.mockReset()

    apiMock.getCase.mockResolvedValue({
      id: 'case-1',
      title: '调研事件',
      topic: '事件',
      description: '',
      platforms: ['weibo'],
      status: 'active',
      time_range: null,
      created_at: '',
      updated_at: '',
    })
    apiMock.listCaseRuns.mockResolvedValue([])
    apiMock.listArtifacts.mockResolvedValue([])
    collectionRunApiMock.list.mockResolvedValue([])
    qualityApiMock.get.mockResolvedValue(makeQuality())
    crossApiMock.related.mockResolvedValue([])
  })

  it('loads and renders the quality card with its six-dimension data', async () => {
    const wrapper = mount(InvestigationOverviewView)
    await flushPromises()
    await vi.waitFor(() => expect(qualityApiMock.get).toHaveBeenCalledWith('case-1'))

    expect(wrapper.text()).toContain('调查质量')
    expect(wrapper.text()).toContain('71.2')
    expect(wrapper.text()).toContain('可接受')
    expect(wrapper.text()).toContain('数据采集')
    expect(wrapper.text()).toContain('结论缺少支撑链接')
    expect(wrapper.text()).toContain('不代表事实真实性')
    wrapper.unmount()
  })

  it('refreshes quality on the card refresh action', async () => {
    qualityApiMock.refresh.mockResolvedValue(makeQuality())
    const wrapper = mount(InvestigationOverviewView)
    await flushPromises()
    await vi.waitFor(() => expect(qualityApiMock.get).toHaveBeenCalled())

    await wrapper.find('.iqcard__refresh').trigger('click')
    await flushPromises()
    expect(qualityApiMock.refresh).toHaveBeenCalledWith('case-1')
    wrapper.unmount()
  })

  it('passes the case id to the related investigations card', async () => {
    const wrapper = mount(InvestigationOverviewView)
    await flushPromises()

    const related = wrapper.findComponent({ name: 'RelatedInvestigationsCard' })
    expect(related.exists()).toBe(true)
    expect(related.props('caseId')).toBe('case-1')
    wrapper.unmount()
  })
})