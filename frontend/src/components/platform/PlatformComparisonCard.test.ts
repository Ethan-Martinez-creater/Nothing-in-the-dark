import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  getPlatformComparison: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import PlatformComparisonCard from './PlatformComparisonCard.vue'

describe('PlatformComparisonCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('retries after a failed comparison load', async () => {
    apiMock.getPlatformComparison.mockRejectedValueOnce(new Error('down'))
    const wrapper = mount(PlatformComparisonCard, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('平台对比数据加载失败')

    apiMock.getPlatformComparison.mockResolvedValue({
      platforms: ['weibo'],
      participation: [],
      sentiment: [],
      timeline: [],
      topic_terms: [],
      common_terms: [],
      insights: [],
    })
    await wrapper.find('.modal-error button').trigger('click')
    await flushPromises()
    expect(apiMock.getPlatformComparison).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('平台对比数据加载失败')
  })
})
