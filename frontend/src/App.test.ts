import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const apiMock = vi.hoisted(() => ({
  listCases: vi.fn(),
  listProjects: vi.fn(),
  createCase: vi.fn(),
  createProject: vi.fn(),
  deleteCase: vi.fn(),
  deleteProject: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import CaseComposer from '@/components/CaseComposer.vue'
import App from '@/App.vue'

function makeCase(overrides: Record<string, unknown> = {}) {
  return {
    id: 'case-1',
    title: '暴雨泄洪谣言案例',
    topic: '暴雨泄洪',
    description: '',
    status: 'ready',
    platforms: ['weibo', 'bilibili'],
    time_range: { start: null, end: null },
    project_id: null,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

async function mountApp(initialPath = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div>home</div>' } },
      { path: '/cases/:caseId', component: { template: '<div>workspace</div>' } },
      { path: '/approvals', component: { template: '<div>approvals</div>' } },
      { path: '/reviews', component: { template: '<div>reviews</div>' } },
      { path: '/resilience', component: { template: '<div>resilience</div>' } },
      { path: '/memories', component: { template: '<div>memories</div>' } },
      { path: '/observability', component: { template: '<div>observability</div>' } },
      { path: '/goals', component: { template: '<div>goals</div>' } },
      { path: '/subscriptions', component: { template: '<div>subscriptions</div>' } },
      { path: '/narratives', component: { template: '<div>narratives</div>' } },
      { path: '/semantics', component: { template: '<div>semantics</div>' } },
      { path: '/security', component: { template: '<div>security</div>' } },
    ],
  })
  router.push(initialPath)
  await router.isReady()
  const wrapper = mount(App, {
    global: {
      plugins: [router],
      stubs: {
        RouterView: { template: '<div class="router-view-stub" />' },
        SkillsPanel: { template: '<div class="skills-stub" />' },
      },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

describe('App conversation sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.listProjects.mockResolvedValue([
      {
        id: 'proj-1',
        title: '灾害舆情项目',
        created_at: '2026-08-01T00:00:00Z',
        updated_at: '2026-08-01T00:00:00Z',
      },
    ])
    apiMock.listCases.mockResolvedValue([
      makeCase(),
      makeCase({ id: 'case-2', title: '华为事件', updated_at: '2026-08-02T00:00:00Z' }),
      makeCase({
        id: 'case-4',
        title: '项目内对话',
        project_id: 'proj-1',
        updated_at: '2026-08-02T00:00:00Z',
      }),
    ])
    apiMock.createCase.mockResolvedValue(makeCase({ id: 'case-3' }))
    apiMock.createProject.mockResolvedValue({
      id: 'proj-2',
      title: '新项目',
      created_at: '',
      updated_at: '',
    })
    apiMock.deleteCase.mockResolvedValue(undefined)
    apiMock.deleteProject.mockResolvedValue(undefined)
  })

  it('renders projects and conversations grouped from API', async () => {
    const { wrapper } = await mountApp()
    expect(apiMock.listCases).toHaveBeenCalled()
    expect(apiMock.listProjects).toHaveBeenCalled()
    expect(wrapper.text()).toContain('灾害舆情项目')
    // 未分组对话 + 项目内对话
    expect(wrapper.findAll('.conversation-item')).toHaveLength(3)
    expect(wrapper.text()).toContain('项目内对话')
  })

  it('filters conversations by search query', async () => {
    const { wrapper } = await mountApp()
    await wrapper.find('.sidebar-search input').setValue('华为')
    expect(wrapper.findAll('.conversation-item')).toHaveLength(1)
    expect(wrapper.text()).toContain('华为事件')
  })

  it('collapses and expands project and conversation groups', async () => {
    const { wrapper } = await mountApp()
    expect(wrapper.text()).toContain('项目内对话')
    // 第一个 toggle = 对话组（折叠未分组对话）
    await wrapper.findAll('.group-toggle')[0]!.trigger('click')
    expect(wrapper.text()).not.toContain('暴雨泄洪谣言案例')
    await wrapper.findAll('.group-toggle')[0]!.trigger('click')
    expect(wrapper.text()).toContain('暴雨泄洪谣言案例')
    // 第二个 toggle = 项目组
    await wrapper.findAll('.group-toggle')[1]!.trigger('click')
    expect(wrapper.text()).not.toContain('项目内对话')
    await wrapper.findAll('.group-toggle')[1]!.trigger('click')
    expect(wrapper.text()).toContain('项目内对话')
  })

  it('keeps governance controls collapsed by default and lets users expand them', async () => {
    const { wrapper } = await mountApp()
    const toggle = wrapper.find('.governance-toggle')

    const links = wrapper.find('#governance-links')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect((links.element as HTMLElement).style.display).toBe('none')

    await toggle.trigger('click')

    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect((links.element as HTMLElement).style.display).not.toBe('none')
    expect(wrapper.text()).toContain('审批箱')
  })

  it('creates a project from the inline input', async () => {
    const { wrapper } = await mountApp()
    await wrapper.findAll('.tool-button')[1]!.trigger('click') // 新建项目
    await wrapper.find('.inline-create input').setValue('新项目')
    await wrapper.find('.inline-create input').trigger('keydown.enter')
    await flushPromises()
    expect(apiMock.createProject).toHaveBeenCalledWith('新项目')
  })

  it('opens the new-chat modal and creates a case', async () => {
    const { wrapper, router } = await mountApp()
    await wrapper.findAll('.tool-button')[0]!.trigger('click') // 新建会话
    expect(wrapper.find('.modal-overlay').exists()).toBe(true)
    const composer = wrapper.findComponent(CaseComposer)
    composer.vm.$emit('submit', {
      topic: '新案例',
      description: '',
      platforms: ['weibo'],
    })
    await flushPromises()
    expect(apiMock.createCase).toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/cases/case-3')
  })

  it('deletes a conversation after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper } = await mountApp()
    await wrapper.findAll('.conversation-delete')[0]!.trigger('click')
    await flushPromises()
    expect(apiMock.deleteCase).toHaveBeenCalledWith('case-1')
    vi.restoreAllMocks()
  })

  it('navigates to another conversation when deleting the currently open one', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { wrapper, router } = await mountApp('/cases/case-1')
    await wrapper.findAll('.conversation-delete')[0]!.trigger('click') // case-1
    await flushPromises()
    expect(apiMock.deleteCase).toHaveBeenCalledWith('case-1')
    // 删除当前打开的会话：优先切到同分组（未分组「对话」）的下一个会话。
    expect(router.currentRoute.value.path).toBe('/cases/case-2')
    vi.restoreAllMocks()
  })

  it('returns to the dashboard when the last conversation is deleted', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    apiMock.listCases.mockResolvedValue([makeCase()])
    const { wrapper, router } = await mountApp('/cases/case-1')
    await wrapper.findAll('.conversation-delete')[0]!.trigger('click') // 唯一的 case-1
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    vi.restoreAllMocks()
  })

  it('keeps the conversation and project group labels when lists are empty', async () => {
    apiMock.listCases.mockResolvedValue([])
    const { wrapper } = await mountApp()
    await flushPromises()
    // 「对话」分组标签无条件保留：会话全部删完后仍可在此新建会话。
    expect(wrapper.findAll('.group-title').some((node) => node.text() === '对话')).toBe(true)
    // 项目标签仍显示（项目本身未被删除）。
    expect(wrapper.text()).toContain('灾害舆情项目')
    expect(wrapper.text()).toContain('还没有会话，点击「新建会话」开始分析')
  })

  it('opens the skills panel', async () => {
    const { wrapper } = await mountApp()
    await wrapper.findAll('.tool-button')[2]!.trigger('click') // 技能
    expect(wrapper.find('.skills-stub').exists()).toBe(true)
  })

  it('shows a retryable error when the conversation list fails', async () => {
    apiMock.listCases.mockRejectedValueOnce(new Error('down'))
    const { wrapper } = await mountApp()
    expect(wrapper.text()).toContain('会话列表加载失败')
    expect(wrapper.find('.sidebar-retry').exists()).toBe(true)

    apiMock.listCases.mockResolvedValue([makeCase()])
    await wrapper.find('.sidebar-retry').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('暴雨泄洪谣言案例')
    expect(wrapper.text()).not.toContain('会话列表加载失败')
  })
})
