import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const apiMock = vi.hoisted(() => ({ getCapabilities: vi.fn() }))
vi.mock('@/services/api', () => ({ api: apiMock }))

const workspaceApiMock = vi.hoisted(() => ({ overview: vi.fn() }))
vi.mock('@/services/api/signals', () => ({
  workspaceApi: workspaceApiMock,
}))

import HomeView from './HomeView.vue'

function makeOverview(overrides: Record<string, unknown> = {}) {
  return {
    counts: {
      investigations: 3,
      open_signals: 1,
      pending_approvals: 0,
      running_runs: 0,
    },
    recent_investigations: [],
    top_signals: [],
    recent_reports: [],
    investigations_needing_attention: [
      {
        case_id: 'case-weak',
        title: '待关注调查',
        grade: 'weak',
        overall_score: 41.3,
        computed_at: '2026-09-01T10:00:00+00:00',
      },
    ],
    quality_unassessed_count: 2,
    ...overrides,
  }
}

describe('HomeView', () => {
  beforeEach(() => {
    push.mockReset()
    apiMock.getCapabilities.mockReset()
    workspaceApiMock.overview.mockReset()
    apiMock.getCapabilities.mockResolvedValue({ demo_mode: true })
    workspaceApiMock.overview.mockResolvedValue(makeOverview())
  })

  it('renders the unassessed quality count KPI', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    const kpis = wrapper.findAll('.home-view__kpi')
    const unassessed = kpis[kpis.length - 1]!
    expect(unassessed.text()).toContain('2')
    expect(unassessed.text()).toContain('待评估质量')
    expect(unassessed.classes()).toContain('home-view__kpi--warn')
  })

  it('renders investigations needing attention with computed_at and grade', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    const panel = wrapper.find('.home-view__attention')
    expect(panel.exists()).toBe(true)
    expect(wrapper.text()).toContain('质量需关注')
    expect(wrapper.text()).toContain('待关注调查')
    expect(wrapper.text()).toContain('弱')
    expect(wrapper.text()).toContain('41.3')
    expect(wrapper.text()).toContain('评估于')
  })

  it('navigates to the case overview when an attention item is clicked', async () => {
    const wrapper = mount(HomeView)
    await flushPromises()

    await wrapper.find('.home-view__attention').trigger('click')
    expect(push).toHaveBeenCalledWith('/investigations/case-weak/overview')
  })

  it('hides the attention panel when there are none', async () => {
    workspaceApiMock.overview.mockResolvedValue(
      makeOverview({ investigations_needing_attention: [] }),
    )
    const wrapper = mount(HomeView)
    await flushPromises()
    expect(wrapper.find('.home-view__attention').exists()).toBe(false)
  })

  it('renders a zero unassessed count without warning styling', async () => {
    workspaceApiMock.overview.mockResolvedValue(makeOverview({ quality_unassessed_count: 0 }))
    const wrapper = mount(HomeView)
    await flushPromises()
    const kpis = wrapper.findAll('.home-view__kpi')
    const unassessed = kpis[kpis.length - 1]!
    expect(unassessed.text()).toContain('0')
    expect(unassessed.classes()).not.toContain('home-view__kpi--warn')
  })
})