import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const crossApiMock = vi.hoisted(() => ({ related: vi.fn() }))
vi.mock('@/services/api/intelligence', () => ({ crossApi: crossApiMock }))

import RelatedInvestigationsCard from './RelatedInvestigationsCard.vue'

function makeRelated(overrides: Record<string, unknown> = {}) {
  return {
    case_id: 'case-b',
    title: '关联调查B',
    relation_types: ['shared_actor'],
    relation_count: 2,
    max_score: 0.77,
    shared_actor_count: 1,
    shared_post_count: 0,
    shared_media_count: 0,
    shared_content_count: 0,
    has_candidate_relation: false,
    ...overrides,
  }
}

describe('RelatedInvestigationsCard', () => {
  beforeEach(() => {
    push.mockReset()
    crossApiMock.related.mockReset()
    crossApiMock.related.mockResolvedValue([makeRelated()])
  })

  it('loads up to five related investigations on mount', async () => {
    mount(RelatedInvestigationsCard, { props: { caseId: 'case-a' } })
    await flushPromises()
    expect(crossApiMock.related).toHaveBeenCalledWith('case-a', 5)
  })

  it('renders relation types with Chinese labels', async () => {
    crossApiMock.related.mockResolvedValue([makeRelated(), makeRelated({ case_id: 'case-c', title: '关联调查C', relation_types: ['shared_post'] })])
    const wrapper = mount(RelatedInvestigationsCard, { props: { caseId: 'case-a' } })
    await flushPromises()
    expect(wrapper.text()).toContain('关联调查B')
    expect(wrapper.text()).toContain('共享账号')
    expect(wrapper.text()).toContain('共享帖子')
    expect(wrapper.text()).toContain('2 条关联')
  })

  it('marks entries containing candidate relations', async () => {
    crossApiMock.related.mockResolvedValue([makeRelated({ has_candidate_relation: true })])
    const wrapper = mount(RelatedInvestigationsCard, { props: { caseId: 'case-a' } })
    await flushPromises()
    expect(wrapper.text()).toContain('含候选')
  })

  it('navigates to the related case overview on click', async () => {
    const wrapper = mount(RelatedInvestigationsCard, { props: { caseId: 'case-a' } })
    await flushPromises()
    await wrapper.find('.relcard__item').trigger('click')
    expect(push).toHaveBeenCalledWith('/investigations/case-b/overview')
  })

  it('shows an empty hint without related investigations', async () => {
    crossApiMock.related.mockResolvedValue([])
    const wrapper = mount(RelatedInvestigationsCard, { props: { caseId: 'case-a' } })
    await flushPromises()
    expect(wrapper.text()).toContain('暂无关联调查')
  })

  it('shows an error message on failure', async () => {
    crossApiMock.related.mockRejectedValue(new Error('boom'))
    const wrapper = mount(RelatedInvestigationsCard, { props: { caseId: 'case-a' } })
    await flushPromises()
    expect(wrapper.text()).toContain('加载关联调查失败')
  })
})