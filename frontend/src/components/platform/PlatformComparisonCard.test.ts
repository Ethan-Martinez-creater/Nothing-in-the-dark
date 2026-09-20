import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// jsdom 不实现 canvas：真实 echarts/zrender 一旦渲染，canvas context 为
// null，会抛 "Cannot read properties of null (reading 'clearRect')" 未捕获
// 异常并使 vitest 以非零码退出（CI run 35491425408 的失败根因）。与其他
// 图表组件测试一致，用 stub 隔离；组件自身的 dispose 生命周期由下方
// unmount 用例覆盖。
const chartMock = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
}))
vi.mock('echarts/core', () => ({
  init: vi.fn(() => chartMock),
  use: vi.fn(),
}))
vi.mock('echarts/charts', () => ({ BarChart: {} }))
vi.mock('echarts/components', () => ({
  GridComponent: {},
  LegendComponent: {},
  TooltipComponent: {},
}))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

const apiMock = vi.hoisted(() => ({
  getPlatformComparison: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import PlatformComparisonCard from './PlatformComparisonCard.vue'

const comparisonPayload = {
  platforms: ['weibo'],
  participation: [],
  sentiment: [],
  timeline: [],
  topic_terms: [],
  common_terms: [],
  insights: [],
}

describe('PlatformComparisonCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('retries after a failed comparison load', async () => {
    apiMock.getPlatformComparison.mockRejectedValueOnce(new Error('down'))
    const wrapper = mount(PlatformComparisonCard, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('平台对比数据加载失败')

    apiMock.getPlatformComparison.mockResolvedValue(comparisonPayload)
    await wrapper.find('.modal-error button').trigger('click')
    await flushPromises()
    expect(apiMock.getPlatformComparison).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('平台对比数据加载失败')
  })

  it('disposes the chart instance on unmount', async () => {
    apiMock.getPlatformComparison.mockResolvedValue(comparisonPayload)
    const wrapper = mount(PlatformComparisonCard, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(chartMock.setOption).toHaveBeenCalled()

    wrapper.unmount()
    expect(chartMock.dispose).toHaveBeenCalled()
  })
})
