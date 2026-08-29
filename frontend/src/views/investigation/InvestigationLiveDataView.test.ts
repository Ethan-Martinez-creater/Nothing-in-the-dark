import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listCasePosts: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

const setUiContext = vi.fn()
vi.mock('@/composables/useInvestigationContext', () => ({
  useInvestigationContext: () => ({ setUiContext }),
}))

vi.mock('@/components/media/MediaPanel.vue', () => ({
  default: { name: 'MediaPanel', template: '<div data-stub="media" />' },
}))
vi.mock('@/components/platform/PlatformComparisonCard.vue', () => ({
  default: { name: 'PlatformComparisonCard', template: '<div data-stub="comparison" />' },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { caseId: 'case-1' } }),
}))

import InvestigationLiveDataView from './InvestigationLiveDataView.vue'
import type { PostsPageDTO, SocialPostDTO } from '@/types/api'

function makePost(): SocialPostDTO {
  return {
    id: 'post-1',
    platform: 'weibo',
    native_id: 'n1',
    content_type: 'post',
    title: '官方公告',
    content: '延期开学的正式通知全文',
    author_name: '账号A',
    source_url: '',
    published_at: '2026-08-01T08:00:00+00:00',
    engagement: {},
  }
}

function makePage(): PostsPageDTO {
  return { posts: [makePost()], limit: 50, offset: 0, has_more: false }
}

describe('InvestigationLiveDataView', () => {
  beforeEach(() => {
    apiMock.listCasePosts.mockReset()
    apiMock.listCasePosts.mockResolvedValue(makePage())
    setUiContext.mockReset()
  })

  it('defaults to Posts tab and renders the raw posts list', async () => {
    const wrapper = mount(InvestigationLiveDataView)
    await flushPromises()
    expect(apiMock.listCasePosts).toHaveBeenCalled()
    expect(wrapper.text()).toContain('官方公告')
    expect(wrapper.find('[data-stub="comparison"]').exists()).toBe(false)
  })

  it('sends post selection into copilot context', async () => {
    const wrapper = mount(InvestigationLiveDataView)
    await flushPromises()
    await wrapper.find('.plist__item').trigger('click')
    expect(setUiContext).toHaveBeenCalledWith({
      workspace: 'live_data',
      selected_type: 'social_post',
      selected_id: 'post-1',
    })
  })

  it('switches to media and comparison tabs', async () => {
    const wrapper = mount(InvestigationLiveDataView)
    await flushPromises()
    const buttons = wrapper.findAll('.ilive__tab')
    await buttons[1]!.trigger('click')
    expect(wrapper.find('[data-stub="media"]').exists()).toBe(true)
    await buttons[2]!.trigger('click')
    expect(wrapper.find('[data-stub="comparison"]').exists()).toBe(true)
  })
})
