import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const setOption = vi.fn()
vi.mock('echarts/core', () => ({
  init: vi.fn(() => ({
    setOption,
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
  use: vi.fn(),
}))
vi.mock('echarts/charts', () => ({ BarChart: {}, LineChart: {} }))
vi.mock('echarts/components', () => ({
  GridComponent: {},
  LegendComponent: {},
  TooltipComponent: {},
}))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

const apiMock = vi.hoisted(() => ({
  getPostStats: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

const setUiContext = vi.fn()
vi.mock('@/composables/useInvestigationContext', () => ({
  useInvestigationContext: () => ({ setUiContext }),
}))

vi.mock('@/views/NarrativeTimelineView.vue', () => ({
  default: {
    name: 'NarrativeTimelineView',
    template: '<div data-stub="narrative" />',
  },
}))

import TimelineWorkspaceContent from './TimelineWorkspaceContent.vue'
import type { PostsStatsDTO } from '@/types/api'

function makeStats(): PostsStatsDTO {
  return {
    total: 4,
    volume_by_day: [
      { day: '2026-08-01', count: 3 },
      { day: '2026-08-02', count: 1 },
    ],
    platform_by_day: [
      { platform: 'weibo', day: '2026-08-01', count: 2 },
      { platform: 'weibo', day: '2026-08-02', count: 1 },
      { platform: 'zhihu', day: '2026-08-01', count: 1 },
    ],
  }
}

describe('TimelineWorkspaceContent', () => {
  beforeEach(() => {
    apiMock.getPostStats.mockReset()
    apiMock.getPostStats.mockResolvedValue(makeStats())
    setOption.mockClear()
    setUiContext.mockReset()
  })

  it('loads stats and renders the volume chart by default', async () => {
    const wrapper = mount(TimelineWorkspaceContent, {
      props: { caseId: 'case-1', setTimeRange: true },
    })
    await flushPromises()
    expect(apiMock.getPostStats).toHaveBeenCalledWith('case-1')
    expect(wrapper.text()).toContain('Volume Timeline')
    expect(wrapper.text()).toContain('Platform Timeline')
    expect(wrapper.text()).toContain('Narrative Timeline')
    expect(setOption).toHaveBeenCalled()
    const option = setOption.mock.calls[0]?.[0] as {
      series: Array<{ data: number[] }>
    }
    expect(option.series[0]?.data).toEqual([3, 1])
  })

  it('renders platform stacked lines on tab switch', async () => {
    const wrapper = mount(TimelineWorkspaceContent, {
      props: { caseId: 'case-1', setTimeRange: true },
    })
    await flushPromises()
    setOption.mockClear()
    const tabs = wrapper.findAll('.twc__tab')
    await tabs[1]!.trigger('click')
    expect(setOption).toHaveBeenCalled()
    const option = setOption.mock.calls[0]?.[0] as {
      xAxis: { data: string[] }
      series: Array<{ name: string; data: number[] }>
    }
    expect(option.xAxis.data).toEqual(['2026-08-01', '2026-08-02'])
    expect(option.series.map((item) => item.name)).toEqual(['weibo', 'zhihu'])
  })

  it('emits time range into copilot context and filters charts', async () => {
    const wrapper = mount(TimelineWorkspaceContent, {
      props: { caseId: 'case-1', setTimeRange: true },
    })
    await flushPromises()
    const dates = wrapper.findAll('.twc__date')
    await dates[0]!.setValue('2026-08-02')
    expect(setUiContext).toHaveBeenCalledWith({
      workspace: 'timeline',
      time_range: { start: '2026-08-02', end: undefined },
    })
    // 过滤后 volume 只剩 08-02
    const lastCall = setOption.mock.calls[setOption.mock.calls.length - 1]?.[0] as
      | { series: Array<{ data: number[] }> }
      | undefined
    expect(lastCall?.series[0]?.data).toEqual([1])
  })

  it('shows error state when stats fetch fails', async () => {
    apiMock.getPostStats.mockRejectedValue(new Error('boom'))
    const wrapper = mount(TimelineWorkspaceContent, {
      props: { caseId: 'case-1', setTimeRange: true },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('时间聚合数据加载失败')
  })

  it('renders narrative timeline stub on narrative tab', async () => {
    const wrapper = mount(TimelineWorkspaceContent, {
      props: { caseId: 'case-1', setTimeRange: true },
    })
    await flushPromises()
    const tabs = wrapper.findAll('.twc__tab')
    await tabs[2]!.trigger('click')
    expect(wrapper.find('[data-stub="narrative"]').exists()).toBe(true)
  })
})
