import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// jsdom 无 canvas：把 echarts init 替换为 no-op，仅测确认交互。
vi.mock('echarts/core', () => ({
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
  use: vi.fn(),
}))
vi.mock('echarts/charts', () => ({ GraphChart: {} }))
vi.mock('echarts/components', () => ({ LegendComponent: {}, TooltipComponent: {} }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

const apiMock = vi.hoisted(() => ({
  confirmPropagationEdge: vi.fn(),
  listPropagationEdgeStates: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import type { PropagationData } from '@/types/api'

import PropagationCard from './PropagationCard.vue'

function makeData(overrides: Partial<PropagationData> = {}): PropagationData {
  return {
    nodes: [
      { id: 'post-1', platform: 'weibo' },
      { id: 'post-2', platform: 'zhihu' },
    ],
    edges: [
      {
        edge_id: 'edge-1',
        source: 'post-1',
        target: 'post-2',
        relation: 'observed',
        confidence: 0.85,
        reasons: ['时间先后', '内容相同'],
      },
    ],
    origin_candidates: [],
    limitations: [],
    ...overrides,
  }
}

describe('PropagationCard edge confirmation', () => {
  beforeEach(() => {
    apiMock.confirmPropagationEdge.mockReset()
    apiMock.confirmPropagationEdge.mockResolvedValue({ id: 'edge-1' })
    apiMock.listPropagationEdgeStates.mockReset()
    apiMock.listPropagationEdgeStates.mockResolvedValue([])
  })

  it('renders edge rows with relation badge and confidence', () => {
    const wrapper = mount(PropagationCard, {
      props: { data: makeData(), caseId: 'case-1' },
    })
    expect(wrapper.text()).toContain('post-1 → post-2')
    expect(wrapper.text()).toContain('明确')
    expect(wrapper.text()).toContain('85%')
    expect(wrapper.text()).toContain('时间先后；内容相同')
  })

  it('confirms an edge through the API and shows the done state', async () => {
    const wrapper = mount(PropagationCard, {
      props: { data: makeData(), caseId: 'case-1' },
    })
    await wrapper.find('.edge-confirm-yes').trigger('click')
    await flushPromises()
    expect(apiMock.confirmPropagationEdge).toHaveBeenCalledWith(
      'case-1',
      'edge-1',
      true,
      '',
    )
    expect(wrapper.text()).toContain('已确认')
    // 确认后按钮消失。
    expect(wrapper.find('.edge-confirm-yes').exists()).toBe(false)
  })

  it('rejects an edge and shows the rejected state', async () => {
    const wrapper = mount(PropagationCard, {
      props: { data: makeData(), caseId: 'case-1' },
    })
    await wrapper.find('.edge-confirm-actions .danger').trigger('click')
    await flushPromises()
    expect(apiMock.confirmPropagationEdge).toHaveBeenCalledWith(
      'case-1',
      'edge-1',
      false,
      '',
    )
    expect(wrapper.text()).toContain('已驳回')
  })

  it('shows an error message when the confirmation request fails', async () => {
    apiMock.confirmPropagationEdge.mockRejectedValue(new Error('boom'))
    const wrapper = mount(PropagationCard, {
      props: { data: makeData(), caseId: 'case-1' },
    })
    await wrapper.find('.edge-confirm-yes').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('确认提交失败')
  })

  it('flags legacy edges without an edge id', () => {
    const data = makeData()
    delete data.edges[0]!.edge_id
    const wrapper = mount(PropagationCard, {
      props: { data, caseId: 'case-1' },
    })
    expect(wrapper.text()).toContain('旧数据：无边 ID，无法人工确认')
    expect(wrapper.find('.edge-confirm-yes').exists()).toBe(false)
  })

  it('restores persisted confirmation state after reload', async () => {
    apiMock.listPropagationEdgeStates.mockResolvedValue([
      { id: 'edge-1', human_confirmed: true, relation: 'observed', confidence: 0.85 },
    ])
    const wrapper = mount(PropagationCard, {
      props: { data: makeData(), caseId: 'case-1' },
    })
    await flushPromises()
    expect(apiMock.listPropagationEdgeStates).toHaveBeenCalledWith('case-1')
    expect(wrapper.text()).toContain('已确认')
    expect(wrapper.find('.edge-confirm-yes').exists()).toBe(false)
  })
})
