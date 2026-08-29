import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listCases: vi.fn(),
  listSubscriptions: vi.fn(),
  listNotificationEndpoints: vi.fn(),
  listDeliveries: vi.fn(),
  listNotifications: vi.fn(),
  createSubscription: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import AdministrationNotificationsView from './AdministrationNotificationsView.vue'

describe('AdministrationNotificationsView', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    apiMock.listCases.mockResolvedValue([
      { id: 'case-1', title: '调查A' },
    ])
    apiMock.listSubscriptions.mockResolvedValue({
      subscriptions: [
        { id: 'sub-1', name: ' critical 通知', channel: 'inbox', severity: 'critical', event_filters: [], enabled: true },
      ],
    })
    apiMock.listNotificationEndpoints.mockResolvedValue({ endpoints: [] })
    apiMock.listDeliveries.mockResolvedValue({ deliveries: [] })
    apiMock.listNotifications.mockResolvedValue({ events: [] })
  })

  it('loads subscriptions after case selection', async () => {
    const wrapper = mount(AdministrationNotificationsView)
    await flushPromises()
    await wrapper.find('select').setValue('case-1')
    await wrapper.find('select').trigger('change')
    await flushPromises()
    expect(apiMock.listSubscriptions).toHaveBeenCalledWith('case-1')
    expect(wrapper.text()).toContain('critical 通知')
  })

  it('switches between subscriptions / endpoints / deliveries tabs', async () => {
    const wrapper = mount(AdministrationNotificationsView)
    await flushPromises()
    await wrapper.find('select').setValue('case-1')
    await wrapper.find('select').trigger('change')
    await flushPromises()
    const tabs = wrapper.findAll('.anot__tab')
    await tabs[1]!.trigger('click')
    expect(wrapper.text()).toContain('Webhook 端点')
    await tabs[2]!.trigger('click')
    expect(wrapper.text()).toContain('暂无投递记录')
  })

  it('creates a subscription', async () => {
    apiMock.createSubscription.mockResolvedValue({ id: 'sub-2' })
    const wrapper = mount(AdministrationNotificationsView)
    await flushPromises()
    await wrapper.find('select').setValue('case-1')
    await wrapper.find('select').trigger('change')
    await flushPromises()
    const createButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('创建订阅'))
    await createButton!.trigger('click')
    await flushPromises()
    expect(apiMock.createSubscription).toHaveBeenCalledWith(
      'case-1',
      expect.objectContaining({ severity: 'warning', channel: 'inbox' }),
    )
  })

  it('does not include a share tab (share moved to Reports)', async () => {
    const wrapper = mount(AdministrationNotificationsView)
    await flushPromises()
    expect(wrapper.text()).not.toContain('分享')
  })
})
