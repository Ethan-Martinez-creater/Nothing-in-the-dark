import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import GlobalSidebar from '@/components/shell/GlobalSidebar.vue'

const routes = [
  { path: '/', component: { template: '<div />' } },
  { path: '/signals', component: { template: '<div />' } },
  { path: '/investigations', component: { template: '<div />' } },
  { path: '/investigations/:caseId/overview', component: { template: '<div />' } },
  { path: '/reports', component: { template: '<div />' } },
  {
    path: '/admin',
    component: { template: '<div />' },
    children: [
      { path: 'approvals', component: { template: '<div />' } },
      { path: 'reviews', component: { template: '<div />' } },
      { path: 'memories', component: { template: '<div />' } },
      { path: 'security', component: { template: '<div />' } },
      { path: 'observability', component: { template: '<div />' } },
      { path: 'resilience', component: { template: '<div />' } },
    ],
  },
]

// 注意：初始导航完成后再 mount，后续导航用 mount 后的 await router.push(...)
// 触发响应式更新（mount 前发起的 push 在 jsdom 下存在竞态，不可靠）。
async function mountSidebar(initialPath = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(initialPath)
  const wrapper = mount(GlobalSidebar, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('GlobalSidebar', () => {
  it('renders the four primary nav entries', async () => {
    const { wrapper } = await mountSidebar()
    const labels = wrapper.findAll('.gsidebar__nav-item').map((node) => node.text())
    expect(labels).toEqual(['首页', '信号', '调查', '报告'])
  })

  it('keeps administration collapsed by default and expands on demand', async () => {
    const { wrapper } = await mountSidebar()
    const toggle = wrapper.find('.gsidebar__admin-toggle')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('#gsidebar-admin-links').isVisible()).toBe(false)

    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    const links = wrapper.findAll('.gsidebar__admin-link').map((node) => node.text())
    expect(links).toEqual(['审批', '审核', '记忆', '安全', '可观测', '韧性'])
  })

  it('emits new-investigation when the CTA is clicked', async () => {
    const { wrapper } = await mountSidebar()
    await wrapper.findAll('.gsidebar__tool')[0]!.trigger('click')
    expect(wrapper.emitted('new-investigation')).toHaveLength(1)
  })

  it('marks the investigations nav item active on investigation routes', async () => {
    const { wrapper, router } = await mountSidebar('/')
    await router.push('/investigations/case-1/overview')
    await flushPromises()
    const items = wrapper.findAll('.gsidebar__nav-item')
    const investigations = items.find((node) => node.text() === '调查')!
    expect(investigations.classes()).toContain('gsidebar__nav-item--active')
  })
})
