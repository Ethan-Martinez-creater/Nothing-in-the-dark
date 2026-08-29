import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

// jsdom 无 canvas：替换 echarts init，捕获 setOption / click handler。
const setOption = vi.fn()
let clickHandler: ((params: unknown) => void) | null = null

vi.mock('echarts/core', () => ({
  init: vi.fn(() => ({
    setOption,
    off: vi.fn(),
    on: vi.fn((_event: string, handler: (params: unknown) => void) => {
      clickHandler = handler
    }),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
  use: vi.fn(),
}))
vi.mock('echarts/charts', () => ({ GraphChart: {} }))
vi.mock('echarts/components', () => ({ LegendComponent: {}, TooltipComponent: {} }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

import PropagationGraph from './PropagationGraph.vue'
import type { PropagationGraphDTO } from '@/types/api'

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
      {
        post_id: 'p2',
        role: 'hub',
        roles: ['hub'],
        score: 0.4,
        attributes: {},
        algorithm_version: 'prop-v2',
        platform: 'zhihu',
        label: '转发帖',
        excerpt: '',
        published_at: null,
        author_name: '',
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
    ...overrides,
  }
}

interface GraphOption {
  series: Array<{
    data: Array<Record<string, unknown>>
    links: Array<Record<string, unknown>>
  }>
}

function firstGraphOption(): GraphOption {
  const arg = setOption.mock.calls[0]?.[0]
  if (!arg) throw new Error('expected setOption to be called')
  return arg as GraphOption
}

describe('PropagationGraph', () => {
  it('shows loading state', () => {
    const wrapper = mount(PropagationGraph, {
      props: { graph: null, loading: true, error: '' },
    })
    expect(wrapper.text()).toContain('传播图加载中')
  })

  it('shows error state', () => {
    const wrapper = mount(PropagationGraph, {
      props: { graph: null, loading: false, error: '加载失败' },
    })
    expect(wrapper.text()).toContain('加载失败')
  })

  it('shows empty state when no graph data', () => {
    const wrapper = mount(PropagationGraph, {
      props: { graph: { nodes: [], edges: [] }, loading: false, error: '' },
    })
    expect(wrapper.text()).toContain('暂无传播图数据')
  })

  it('maps graph nodes and edges into the chart option', async () => {
    setOption.mockClear()
    mount(PropagationGraph, { props: { graph: makeGraph(), loading: false, error: '' } })
    expect(setOption).toHaveBeenCalled()
    const option = firstGraphOption()
    const series = option.series[0]!
    expect(series.data).toHaveLength(2)
    // 主 role 取最高分 source；尺寸映射 score
    expect(series.data[0]).toMatchObject({ id: 'p1', role: 'source', score: 0.9 })
    // 未确认边 → 推断虚线
    const link = series.links[0]!
    expect(link).toMatchObject({
      id: 'edge-1',
      source: 'p1',
      target: 'p2',
    })
    expect((link.lineStyle as Record<string, unknown>).type).toBe('dashed')
  })

  it('renders confirmed edges as solid lines', async () => {
    setOption.mockClear()
    const graph = makeGraph()
    graph.edges[0]!.human_confirmed = true
    mount(PropagationGraph, { props: { graph, loading: false, error: '' } })
    const option = firstGraphOption()
    const link = option.series[0]!.links[0]!
    expect((link.lineStyle as Record<string, unknown>).type).toBe('solid')
  })

  it('emits node selection on chart click', async () => {
    const wrapper = mount(PropagationGraph, {
      props: { graph: makeGraph(), loading: false, error: '' },
    })
    clickHandler?.({ dataType: 'node', data: { id: 'p1' } })
    expect(wrapper.emitted('select')?.[0]).toEqual([
      { type: 'propagation_node', id: 'p1' },
    ])
  })

  it('emits edge selection on chart click', async () => {
    const wrapper = mount(PropagationGraph, {
      props: { graph: makeGraph(), loading: false, error: '' },
    })
    clickHandler?.({ dataType: 'edge', data: { id: 'edge-1' } })
    expect(wrapper.emitted('select')?.[0]).toEqual([
      { type: 'propagation_edge', id: 'edge-1' },
    ])
  })
})
