import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listPlatformAuth: vi.fn(),
  createLoginSession: vi.fn(),
  getLoginSession: vi.fn(),
  cancelLoginSession: vi.fn(),
  validatePlatform: vi.fn(),
  revokePlatform: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import PlatformAccountsView from '@/views/PlatformAccountsView.vue'

const FIVE_PLATFORMS = [
  'weibo',
  'bilibili',
  'tieba',
  'zhihu',
  'douyin',
]

function makeItem(platform: string, status = 'missing') {
  return {
    platform,
    status,
    last_validated_at: status === 'active' ? '2026-09-09T00:00:00Z' : null,
    expires_at: null,
    account_label: null,
  }
}

function makeSession(overrides: Record<string, unknown> = {}) {
  return {
    session_id: 'sess-1',
    platform: 'weibo',
    status: 'starting',
    expires_at: '2026-09-09T01:00:00Z',
    qr_code: null,
    error: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  apiMock.listPlatformAuth.mockResolvedValue({
    items: FIVE_PLATFORMS.map((p) => makeItem(p)),
  })
  apiMock.createLoginSession.mockResolvedValue(makeSession())
  apiMock.getLoginSession.mockResolvedValue(makeSession())
  apiMock.cancelLoginSession.mockResolvedValue({ ok: true })
  apiMock.revokePlatform.mockResolvedValue({ ok: true })
  apiMock.validatePlatform.mockResolvedValue({
    platform: 'weibo',
    result: 'validation_not_implemented',
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

async function mountView() {
  const wrapper = mount(PlatformAccountsView)
  await flushPromises()
  return wrapper
}

describe('PlatformAccountsView', () => {
  it('渲染五个平台的状态列表', async () => {
    apiMock.listPlatformAuth.mockResolvedValue({
      items: [
        makeItem('weibo', 'active'),
        makeItem('bilibili', 'invalid'),
        makeItem('tieba', 'missing'),
        makeItem('zhihu', 'missing'),
        makeItem('douyin', 'active'),
      ],
    })
    const wrapper = await mountView()
    const cards = wrapper.findAll('.pauth__card')
    expect(cards.length).toBe(5)
    expect(wrapper.text()).toContain('微博')
    expect(wrapper.text()).toContain('已登录')
    expect(wrapper.text()).toContain('登录已失效')
  })

  it('点击扫码登录调用 start API 并打开 Modal', async () => {
    const wrapper = await mountView()
    const loginButtons = wrapper.findAll('.pauth__btn--primary')
    await loginButtons[0]!.trigger('click')
    expect(apiMock.createLoginSession).toHaveBeenCalledWith('weibo')
    expect(wrapper.find('.pauth__modal').exists()).toBe(true)
  })

  it('waiting_scan 状态显示二维码', async () => {
    apiMock.createLoginSession.mockResolvedValue(makeSession())
    apiMock.getLoginSession.mockResolvedValue(
      makeSession({
        status: 'waiting_scan',
        qr_code: 'data:image/png;base64,qr-data',
      }),
    )
    const wrapper = await mountView()
    await wrapper.findAll('.pauth__btn--primary')[0]!.trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    const img = wrapper.find('.pauth__modal-qr img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('data:image/png;base64,qr-data')
  })

  it('authenticated 自动关闭 Modal 并刷新列表', async () => {
    apiMock.getLoginSession.mockResolvedValue(
      makeSession({ status: 'authenticated' }),
    )
    const wrapper = await mountView()
    await wrapper.findAll('.pauth__btn--primary')[0]!.trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(wrapper.find('.pauth__modal').exists()).toBe(false)
    expect(apiMock.listPlatformAuth).toHaveBeenCalledTimes(2) // 初始 + 登录成功刷新
  })

  it('expired 状态提示二维码过期', async () => {
    apiMock.getLoginSession.mockResolvedValue(
      makeSession({ status: 'expired', error: null }),
    )
    const wrapper = await mountView()
    await wrapper.findAll('.pauth__btn--primary')[0]!.trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(wrapper.find('.pauth__modal').exists()).toBe(true)
    expect(wrapper.text()).toContain('二维码已过期')
  })

  it('关闭 Modal 触发 cancel 会话', async () => {
    const wrapper = await mountView()
    await wrapper.findAll('.pauth__btn--primary')[0]!.trigger('click')
    await wrapper.find('.pauth__modal-close').trigger('click')
    await flushPromises()
    expect(apiMock.cancelLoginSession).toHaveBeenCalledWith('sess-1')
    expect(wrapper.find('.pauth__modal').exists()).toBe(false)
  })

  it('退出按钮调用 revoke', async () => {
    apiMock.listPlatformAuth.mockResolvedValue({
      items: [makeItem('weibo', 'active')],
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountView()
    const revokeButton = wrapper.findAll('.pauth__btn--danger')[0]!
    await revokeButton.trigger('click')
    await flushPromises()
    expect(apiMock.revokePlatform).toHaveBeenCalledWith('weibo')
  })
})
