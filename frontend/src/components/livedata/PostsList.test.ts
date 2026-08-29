import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listCasePosts: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import PostsList from './PostsList.vue'
import type { SocialPostDTO } from '@/types/api'

function makePost(overrides: Partial<SocialPostDTO> = {}): SocialPostDTO {
  return {
    id: 'post-1',
    platform: 'weibo',
    native_id: 'n1',
    content_type: 'post',
    title: '官方公告',
    content: '延期开学的正式通知全文',
    author_name: '账号A',
    source_url: 'https://weibo.com/n1',
    published_at: '2026-08-01T08:00:00+00:00',
    engagement: {},
    ...overrides,
  }
}

describe('PostsList', () => {
  beforeEach(() => {
    apiMock.listCasePosts.mockReset()
    apiMock.listCasePosts.mockResolvedValue({
      posts: [makePost()],
      limit: 50,
      offset: 0,
      has_more: false,
    })
  })

  it('loads and renders posts with platform, author, time and source link', async () => {
    const wrapper = mount(PostsList, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(apiMock.listCasePosts).toHaveBeenCalledWith('case-1', expect.anything())
    const text = wrapper.text()
    expect(text).toContain('官方公告')
    expect(text).toContain('weibo')
    expect(text).toContain('账号A')
    const link = wrapper.find('a.plist__link')
    expect(link.attributes('href')).toBe('https://weibo.com/n1')
  })

  it('emits post selection for copilot context', async () => {
    const wrapper = mount(PostsList, { props: { caseId: 'case-1' } })
    await flushPromises()
    await wrapper.find('.plist__item').trigger('click')
    expect(wrapper.emitted('selectPost')?.[0]?.[0]).toMatchObject({ id: 'post-1' })
  })

  it('applies platform and keyword filters', async () => {
    const wrapper = mount(PostsList, { props: { caseId: 'case-1' } })
    await flushPromises()
    await wrapper.find('select').setValue('zhihu')
    await wrapper.find('button.ghost-button').trigger('click')
    expect(apiMock.listCasePosts).toHaveBeenLastCalledWith(
      'case-1',
      expect.objectContaining({ platform: 'zhihu' }),
    )
  })

  it('shows error state on fetch failure', async () => {
    apiMock.listCasePosts.mockRejectedValue(new Error('boom'))
    const wrapper = mount(PostsList, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('帖子列表加载失败')
  })

  it('shows empty state when no posts', async () => {
    apiMock.listCasePosts.mockResolvedValue({
      posts: [],
      limit: 50,
      offset: 0,
      has_more: false,
    })
    const wrapper = mount(PostsList, { props: { caseId: 'case-1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('暂无帖子')
  })

  it('loads more pages when has_more', async () => {
    apiMock.listCasePosts.mockResolvedValue({
      posts: [makePost()],
      limit: 50,
      offset: 0,
      has_more: true,
    })
    const wrapper = mount(PostsList, { props: { caseId: 'case-1' } })
    await flushPromises()
    const moreButton = wrapper.find('.plist__more')
    await moreButton.trigger('click')
    await flushPromises()
    expect(apiMock.listCasePosts).toHaveBeenLastCalledWith(
      'case-1',
      expect.objectContaining({ offset: 1 }),
    )
  })
})
