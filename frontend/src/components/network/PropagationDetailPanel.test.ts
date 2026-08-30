import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  confirmPropagationEdge: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}))

import PropagationDetailPanel from './PropagationDetailPanel.vue'
import type { PropagationGraphDTO } from '@/types/api'
import type { PropagationSelection } from './PropagationGraph.vue'

function makeGraph(overrides: Partial<PropagationGraphDTO> = {}): PropagationGraphDTO {
  return {
    nodes: [
      {
        post_id: 'p1',
        role: 'source',
        roles: ['source', 'burst'],
        score: 0.9,
        attributes: {},
        algorithm_version: 'prop-v2',
        platform: 'weibo',
        label: '首发帖子',
        excerpt: '首发内容摘录',
        published_at: '2026-08-01T00:00:00+00:00',
        author_name: '账号A',
      },
    ],
    edges: [
      {
        id: 'edge-1',
        case_id: 'case-1',
        source_post_id: 'p1',
        target_post_id: 'p2',
        relation: 'copy_spread',
        confidence: 0.83,
        feature_scores: { text_sim: 0.83 },
        evidence_ids: ['ev-1'],
        algorithm_version: 'prop-v2',
        human_confirmed: false,
        human_review_state: 'unreviewed',
      },
    ],
    ...overrides,
  }
}

const nodeSelection: PropagationSelection = { type: 'propagation_node', id: 'p1' }
const edgeSelection: PropagationSelection = { type: 'propagation_edge', id: 'edge-1' }

describe('PropagationDetailPanel', () => {
  beforeEach(() => {
    apiMock.confirmPropagationEdge.mockReset()
    apiMock.confirmPropagationEdge.mockResolvedValue({ id: 'edge-1' })
  })

  it('prompts selection when nothing selected', () => {
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph: makeGraph(), selection: null },
    })
    expect(wrapper.text()).toContain('选择节点或传播边')
  })

  it('shows edge detail with confidence, feature scores and evidence ids', () => {
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph: makeGraph(), selection: edgeSelection },
    })
    const text = wrapper.text()
    expect(text).toContain('传播边详情')
    expect(text).toContain('copy_spread')
    expect(text).toContain('83%')
    expect(text).toContain('prop-v2')
    expect(text).toContain('text_sim')
    expect(text).toContain('ev-1')
    // FC1: 三态 badge —— unreviewed / confirmed / rejected 各自准确
    expect(text).toContain('人工未复核（推断关系）')
  })

  it('shows the confirmed badge for confirmed edges', () => {
    const graph = makeGraph()
    graph.edges[0]!.human_review_state = 'confirmed'
    graph.edges[0]!.human_confirmed = true
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph, selection: edgeSelection },
    })
    expect(wrapper.text()).toContain('人工已确认')
    expect(wrapper.text()).not.toContain('人工未复核')
  })

  it('shows the rejected badge for rejected edges after a reload', () => {
    // 刷新后状态仍来自后端数据（human_review_state=rejected），非前端局部记忆
    const graph = makeGraph()
    graph.edges[0]!.human_review_state = 'rejected'
    graph.edges[0]!.human_confirmed = false
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph, selection: edgeSelection },
    })
    expect(wrapper.text()).toContain('人工已驳回')
    expect(wrapper.text()).not.toContain('人工未复核')
    expect(wrapper.text()).not.toContain('人工已确认')
  })

  it('allows re-judging a rejected edge to confirmed through the UI', async () => {
    const graph = makeGraph()
    graph.edges[0]!.human_review_state = 'rejected'
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph, selection: edgeSelection },
    })
    const confirmButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('确认关系成立'))
    await confirmButton!.trigger('click')
    await flushPromises()
    expect(apiMock.confirmPropagationEdge).toHaveBeenCalledWith('case-1', 'edge-1', true, '')
  })

  it('allows re-judging a confirmed edge to rejected through the UI', async () => {
    const graph = makeGraph()
    graph.edges[0]!.human_review_state = 'confirmed'
    graph.edges[0]!.human_confirmed = true
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph, selection: edgeSelection },
    })
    const rejectButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('驳回该关系'))
    await rejectButton!.trigger('click')
    await flushPromises()
    expect(apiMock.confirmPropagationEdge).toHaveBeenCalledWith('case-1', 'edge-1', false, '')
  })

  it('confirms an edge through the existing confirmation API and emits refresh', async () => {
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph: makeGraph(), selection: edgeSelection },
    })
    const confirmButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('确认关系成立'))
    await confirmButton!.trigger('click')
    await flushPromises()
    expect(apiMock.confirmPropagationEdge).toHaveBeenCalledWith(
      'case-1',
      'edge-1',
      true,
      '',
    )
    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })

  it('shows node detail without describing candidates as facts', () => {
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph: makeGraph(), selection: nodeSelection },
    })
    const text = wrapper.text()
    expect(text).toContain('节点详情')
    expect(text).toContain('账号A')
    expect(text).toContain('首发内容摘录')
    // candidate origin 不描述成事实
    expect(text).toContain('算法候选 · 非已证实结论')
  })

  it('navigates to the evidence workspace from edge evidence', async () => {
    const wrapper = mount(PropagationDetailPanel, {
      props: { caseId: 'case-1', graph: makeGraph(), selection: edgeSelection },
    })
    const evidenceButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('Evidence 工作区'))
    await evidenceButton!.trigger('click')
    expect(push).toHaveBeenCalledWith('/investigations/case-1/evidence')
  })
})
