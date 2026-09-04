import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

vi.mock('@/components/intelligence/IntelligenceConnectionsGraph.vue', () => ({
  default: { name: 'IntelligenceConnectionsGraph', template: '<div data-stub="graph" />' },
}))

const crossApiMock = vi.hoisted(() => ({
  connections: vi.fn(),
  between: vi.fn(),
  related: vi.fn(),
}))
const entityApiMock = vi.hoisted(() => ({
  list: vi.fn(),
  profile: vi.fn(),
  caseEntities: vi.fn(),
}))
vi.mock('@/services/api/intelligence', () => ({
  crossApi: crossApiMock,
  entityApi: entityApiMock,
}))

import IntelligenceView from './IntelligenceView.vue'

function makeConnection(overrides: Record<string, unknown> = {}) {
  return {
    id: 'link-1',
    left_case_id: 'case-a',
    right_case_id: 'case-b',
    left_title: '调查A',
    right_title: '调查B',
    relation_type: 'shared_actor',
    status: 'observed',
    score: 0.82,
    evidence_count: 3,
    algorithm_version: 'cross-intel-1.0.0',
    ...overrides,
  }
}

function makeEntity(overrides: Record<string, unknown> = {}) {
  return {
    entity_id: 'ent-1',
    entity_type: 'account',
    canonical_name: '账号甲',
    platforms: ['weibo'],
    investigation_count: 2,
    post_count: 5,
    comment_count: 1,
    last_seen_at: '2026-08-20T00:00:00+00:00',
    risk_summary: null,
    ...overrides,
  }
}

describe('IntelligenceView', () => {
  beforeEach(() => {
    push.mockReset()
    crossApiMock.connections.mockReset()
    crossApiMock.related.mockReset()
    entityApiMock.list.mockReset()
    entityApiMock.profile.mockReset()
    crossApiMock.connections.mockResolvedValue([makeConnection()])
    entityApiMock.list.mockResolvedValue({ items: [makeEntity()], total: 1 })
    entityApiMock.profile.mockResolvedValue({
      entity_id: 'ent-1',
      component_key: 'ent-1',
      entity_ids: ['ent-1'],
      entity_type: 'account',
      canonical_name: '账号甲',
      aliases: ['甲'],
      platform_identities: [{ platform: 'weibo', native_id: 'wb-1' }],
      investigation_count: 2,
      investigations: ['case-a'],
      post_count: 5,
      comment_count: 1,
      engagement_total: 300,
      first_seen_at: null,
      last_seen_at: '2026-08-20T00:00:00+00:00',
      recent_posts: [],
      risk_assessments: [],
      unresolved_local_risk: [],
      coordination_memberships: [],
      algorithm_version: 'workspace-entity-1.0.0',
    })
  })

  it('loads connections by default and renders observed/candidate visuals', async () => {
    crossApiMock.connections.mockResolvedValue([
      makeConnection({ id: 'l1', status: 'observed' }),
      makeConnection({ id: 'l2', status: 'candidate', relation_type: 'shared_post' }),
    ])
    const wrapper = mount(IntelligenceView)
    await flushPromises()

    expect(crossApiMock.connections).toHaveBeenCalled()
    expect(wrapper.find('[data-stub="graph"]').exists()).toBe(true)
    const statuses = wrapper.findAll('.intelview__item-status').map((node) => node.attributes('data-status'))
    expect(statuses).toContain('observed')
    expect(statuses).toContain('candidate')
  })

  it('filters connections by status and relation type', async () => {
    const wrapper = mount(IntelligenceView)
    await flushPromises()
    crossApiMock.connections.mockClear()

    await wrapper.findAll('.intelview__filter')[0]!.setValue('candidate')
    await flushPromises()
    expect(crossApiMock.connections).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'candidate' }),
    )

    crossApiMock.connections.mockClear()
    await wrapper.findAll('.intelview__filter')[1]!.setValue('shared_actor')
    await flushPromises()
    expect(crossApiMock.connections).toHaveBeenLastCalledWith(
      expect.objectContaining({ relation_type: 'shared_actor' }),
    )
  })

  it('shows connection detail and navigates to a case', async () => {
    const wrapper = mount(IntelligenceView)
    await flushPromises()

    await wrapper.find('.intelview__item').trigger('click')
    expect(wrapper.text()).toContain('关联详情')
    expect(wrapper.text()).toContain('共享账号')

    const links = wrapper.findAll('.intelview__link')
    await links[0]!.trigger('click')
    expect(push).toHaveBeenCalledWith('/investigations/case-a/overview')
  })

  it('renders an empty hint when no connections match', async () => {
    crossApiMock.connections.mockResolvedValue([])
    const wrapper = mount(IntelligenceView)
    await flushPromises()
    expect(wrapper.text()).toContain('无匹配的跨调查关联')
  })

  it('shows a connection error state', async () => {
    crossApiMock.connections.mockRejectedValue(new Error('boom'))
    const wrapper = mount(IntelligenceView)
    await flushPromises()
    expect(wrapper.text()).toContain('加载跨调查关联失败')
  })

  it('switches to entities tab and loads the entity list', async () => {
    const wrapper = mount(IntelligenceView)
    await flushPromises()

    const tabs = wrapper.findAll('.intelview__tab')
    await tabs[1]!.trigger('click')
    await flushPromises()

    expect(entityApiMock.list).toHaveBeenCalled()
    expect(wrapper.text()).toContain('账号甲')
  })

  it('loads and renders an entity profile on selection', async () => {
    const wrapper = mount(IntelligenceView)
    await flushPromises()

    await wrapper.findAll('.intelview__tab')[1]!.trigger('click')
    await flushPromises()

    await wrapper.find('.intelview__item').trigger('click')
    await flushPromises()

    expect(entityApiMock.profile).toHaveBeenCalledWith('ent-1')
    expect(wrapper.text()).toContain('账号甲')
    expect(wrapper.text()).toContain('别名')
    expect(wrapper.text()).toContain('甲')
  })

  it('shows the entity empty hint', async () => {
    entityApiMock.list.mockResolvedValue({ items: [], total: 0 })
    const wrapper = mount(IntelligenceView)
    await flushPromises()
    await wrapper.findAll('.intelview__tab')[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('暂无实体')
  })
})