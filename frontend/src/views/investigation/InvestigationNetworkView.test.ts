import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  getPropagationGraph: vi.fn(),
  confirmPropagationEdge: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

const setUiContext = vi.fn()
vi.mock('@/composables/useInvestigationContext', () => ({
  useInvestigationContext: () => ({ setUiContext }),
}))

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { caseId: 'case-1' } }),
  useRouter: () => ({ push }),
}))

// Alignment/Integrity 面板不在本测试范围
vi.mock('@/components/alignment/AlignmentPanel.vue', () => ({
  default: { name: 'AlignmentPanel', template: '<div data-stub="alignment" />' },
}))
vi.mock('@/components/integrity/IntegrityPanel.vue', () => ({
  default: { name: 'IntegrityPanel', template: '<div data-stub="integrity" />' },
}))

import InvestigationNetworkView from './InvestigationNetworkView.vue'
import type { PropagationGraphDTO } from '@/types/api'

function makeGraph(): PropagationGraphDTO {
  return {
    nodes: [
      {
        post_id: 'p1',
        role: 'source',
        roles: ['source'],
        score: 0.9,
        attributes: {},
        algorithm_version: 'prop-v2',
        platform: 'weibo',
        label: '首发帖子',
        excerpt: '首发内容摘录',
        published_at: null,
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
      },
    ],
  }
}

async function mountView() {
  return mount(InvestigationNetworkView, {
    global: {
      stubs: {
        PropagationGraph: {
          name: 'PropagationGraph',
          template: '<div data-stub="propagation-graph" />',
          emits: ['select'],
          props: {
            graph: { type: Object, default: null },
            loading: { type: Boolean, default: false },
            error: { type: String, default: '' },
          },
        },
      },
    },
  })
}

describe('InvestigationNetworkView', () => {
  beforeEach(() => {
    apiMock.getPropagationGraph.mockReset()
    apiMock.getPropagationGraph.mockResolvedValue(makeGraph())
    apiMock.confirmPropagationEdge.mockReset()
    apiMock.confirmPropagationEdge.mockResolvedValue({ id: 'edge-1' })
    setUiContext.mockReset()
    push.mockReset()
  })

  it('propagation mode loads the real graph and never renders PlatformComparisonCard', async () => {
    const wrapper = await mountView()
    await flushPromises()
    expect(apiMock.getPropagationGraph).toHaveBeenCalledWith('case-1')
    expect(wrapper.find('[data-stub="propagation-graph"]').exists()).toBe(true)
    // C7: Propagation 模式不再挂载 VisualSidebar / PlatformComparisonCard
    expect(
      wrapper.findComponent({ name: 'PlatformComparisonCard' }).exists(),
    ).toBe(false)
  })

  it('shows error state when graph fetch fails', async () => {
    apiMock.getPropagationGraph.mockRejectedValue(new Error('boom'))
    const wrapper = await mountView()
    await flushPromises()
    // error/loading 状态由 PropagationGraph 渲染（stub 内），验证 props 传递
    const graphStub = wrapper.findComponent({ name: 'PropagationGraph' })
    expect(graphStub.props('error')).toContain('传播图加载失败')
    expect(graphStub.props('loading')).toBe(false)
  })

  it('shows empty state when graph has no data', async () => {
    apiMock.getPropagationGraph.mockResolvedValue({ nodes: [], edges: [] })
    const wrapper = await mountView()
    await flushPromises()
    const graphStub = wrapper.findComponent({ name: 'PropagationGraph' })
    expect(graphStub.props('graph')).toEqual({ nodes: [], edges: [] })
  })

  it('forwards node selection into copilot context and detail panel', async () => {
    const wrapper = await mountView()
    await flushPromises()
    const graphStub = wrapper.findComponent({ name: 'PropagationGraph' })
    graphStub.vm.$emit('select', { type: 'propagation_node', id: 'p1' })
    await flushPromises()
    expect(setUiContext).toHaveBeenCalledWith({
      workspace: 'network',
      selected_type: 'propagation_node',
      selected_id: 'p1',
    })
    const detailStub = wrapper.findComponent({ name: 'PropagationDetailPanel' })
    expect(detailStub.props('selection')).toEqual({
      type: 'propagation_node',
      id: 'p1',
    })
    expect(detailStub.props('graph')).toEqual(makeGraph())
  })

  it('confirms an edge via existing API, refreshes graph and updates context', async () => {
    const wrapper = await mountView()
    await flushPromises()
    const graphStub = wrapper.findComponent({ name: 'PropagationGraph' })
    graphStub.vm.$emit('select', { type: 'propagation_edge', id: 'edge-1' })
    await flushPromises()
    // Edge 详情：置信度 / 特征分数 / 证据
    expect(wrapper.text()).toContain('传播边详情')
    expect(wrapper.text()).toContain('83%')
    expect(wrapper.text()).toContain('text_sim')
    expect(wrapper.text()).toContain('ev-1')

    apiMock.getPropagationGraph.mockClear()
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
    expect(apiMock.getPropagationGraph).toHaveBeenCalledWith('case-1')
    expect(setUiContext).toHaveBeenCalledWith({
      workspace: 'network',
      selected_type: 'propagation_edge',
      selected_id: 'edge-1',
    })
  })
})
