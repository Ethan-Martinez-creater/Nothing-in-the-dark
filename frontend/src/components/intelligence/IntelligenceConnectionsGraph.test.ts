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

import IntelligenceConnectionsGraph from './IntelligenceConnectionsGraph.vue'
import type { IntelligenceConnection } from '@/services/api/intelligence'

function makeConnection(overrides: Partial<IntelligenceConnection> = {}): IntelligenceConnection {
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

function lastOption() {
  const calls = vi.mocked(setOption).mock.calls
  return calls[calls.length - 1]![0] as {
    series: Array<{
      links?: Array<{ id?: string; lineStyle?: { type?: string } }>
    }>
  }
}

describe('IntelligenceConnectionsGraph', () => {
  it('renders observed links with solid lines and candidate with dashed', () => {
    mount(IntelligenceConnectionsGraph, {
      props: {
        connections: [
          makeConnection({ id: 'l1', status: 'observed' }),
          makeConnection({ id: 'l2', status: 'candidate', relation_type: 'shared_post' }),
        ],
        caseTitles: { 'case-a': '调查A', 'case-b': '调查B' },
        loading: false,
      },
    })
    const links = lastOption().series[0]!.links!
    const observed = links.find((link) => (link as { id?: string }).id === 'l1')!.lineStyle!
    const candidate = links.find((link) => (link as { id?: string }).id === 'l2')!.lineStyle!
    expect(observed.type).toBe('solid')
    expect(candidate.type).toBe('dashed')
  })

  it('emits select with the connection id on edge click', () => {
    const wrapper = mount(IntelligenceConnectionsGraph, {
      props: {
        connections: [makeConnection()],
        caseTitles: {},
        loading: false,
      },
    })
    clickHandler!({ dataType: 'edge', data: { id: 'link-1' } })
    expect(wrapper.emitted('select')![0]).toEqual(['link-1'])
  })

  it('emits case: prefixed target on node click', () => {
    const wrapper = mount(IntelligenceConnectionsGraph, {
      props: {
        connections: [makeConnection()],
        caseTitles: {},
        loading: false,
      },
    })
    clickHandler!({ dataType: 'node', data: { id: 'case-a' } })
    expect(wrapper.emitted('select')![0]).toEqual(['case:case-a'])
  })

  it('shows an empty hint without connections', () => {
    const wrapper = mount(IntelligenceConnectionsGraph, {
      props: { connections: [], caseTitles: {}, loading: false },
    })
    expect(wrapper.text()).toContain('尚无跨调查关联')
  })
})