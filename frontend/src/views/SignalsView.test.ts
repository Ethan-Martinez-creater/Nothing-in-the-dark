import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const signalApiMock = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  acknowledge: vi.fn(),
  resolve: vi.fn(),
  suppress: vi.fn(),
}))
vi.mock('@/services/api/signals', () => ({
  signalApi: signalApiMock,
}))

import SignalsView from './SignalsView.vue'
import type { Signal } from '@/services/api/signals'

function makeSignal(overrides: Partial<Signal> = {}): Signal {
  return {
    id: 'sig-1',
    source_type: 'derived',
    source_id: 'subj-1',
    case_id: 'case-a',
    case_title: '调查A',
    signal_type: 'media_reuse',
    severity: 'warning',
    status: 'open',
    title: '同一媒体素材在多个调查中复用',
    why_it_matters: '相同媒体素材出现在多个调查中',
    confidence: null,
    evidence_refs: {},
    trigger_count: 1,
    first_seen_at: '2026-09-01T00:00:00+00:00',
    detected_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-01T00:00:00+00:00',
    related_case_ids: ['case-a', 'case-b'],
    source_label: 'Media reuse',
    detector_version: 'advanced-signal-1.0.0',
    detector_active: true,
    ...overrides,
  }
}

describe('SignalsView (V3 §59)', () => {
  beforeEach(() => {
    push.mockReset()
    signalApiMock.list.mockReset()
    signalApiMock.acknowledge.mockReset()
    signalApiMock.resolve.mockReset()
    signalApiMock.suppress.mockReset()
    signalApiMock.list.mockResolvedValue([makeSignal()])
    signalApiMock.acknowledge.mockImplementation(async (id: string) =>
      makeSignal({ id, status: 'acknowledged' }),
    )
    signalApiMock.resolve.mockImplementation(async (id: string) =>
      makeSignal({ id, status: 'resolved' }),
    )
    signalApiMock.suppress.mockImplementation(async (id: string) =>
      makeSignal({ id, status: 'suppressed' }),
    )
  })

  it('shows the global intelligence inbox subtitle', async () => {
    const wrapper = mount(SignalsView)
    await flushPromises()
    expect(wrapper.text()).toContain('全局情报信号收件箱')
  })

  it('offers the V3 source filter options', async () => {
    const wrapper = mount(SignalsView)
    await flushPromises()
    const options = wrapper
      .findAll('.sigview__filter option')
      .map((node) => node.text())
    expect(options).toContain('Monitor')
    expect(options).toContain('Coordination')
    expect(options).toContain('Actor recurrence')
    expect(options).toContain('Media reuse')
    expect(options).toContain('Cross-case overlap')
  })

  it('passes source_type to the API when filtered', async () => {
    const wrapper = mount(SignalsView)
    await flushPromises()
    signalApiMock.list.mockClear()

    const filters = wrapper.findAll('.sigview__filter')
    await filters[2]!.setValue('actor_recurrence')
    await flushPromises()
    expect(signalApiMock.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ source_type: 'actor_recurrence' }),
    )
  })

  it('renders derived signal detail with detector state and related cases', async () => {
    const wrapper = mount(SignalsView)
    await flushPromises()

    await wrapper.find('.sigview__card').trigger('click')
    expect(wrapper.text()).toContain('Media reuse')
    expect(wrapper.text()).toContain('advanced-signal-1.0.0')
    expect(wrapper.text()).toContain('active')
    expect(wrapper.text()).toContain('case-b')
    expect(wrapper.text()).toContain('关联调查')
  })

  it('shows 条件已消失 when inactive and resolved', async () => {
    signalApiMock.list.mockResolvedValue([
      makeSignal({ detector_active: false, status: 'resolved' }),
    ])
    const wrapper = mount(SignalsView)
    await flushPromises()

    await wrapper.find('.sigview__card').trigger('click')
    expect(wrapper.text()).toContain('条件已消失')
    expect(wrapper.text()).toContain('inactive')
  })

  it('shows the empty hint without signals', async () => {
    signalApiMock.list.mockResolvedValue([])
    const wrapper = mount(SignalsView)
    await flushPromises()
    expect(wrapper.text()).toContain('当前没有信号')
  })

  it('shows an error state', async () => {
    signalApiMock.list.mockRejectedValue(new Error('boom'))
    const wrapper = mount(SignalsView)
    await flushPromises()
    expect(wrapper.text()).toContain('信号加载失败')
  })

  it('resolves a derived signal through the API and refreshes', async () => {
    const wrapper = mount(SignalsView)
    await flushPromises()

    await wrapper.find('.sigview__card').trigger('click')
    signalApiMock.list.mockClear()
    await wrapper.find('.sigview__act').trigger('click') // 确认（open → acknowledge）
    await flushPromises()

    expect(signalApiMock.acknowledge).toHaveBeenCalledWith('sig-1')
    expect(signalApiMock.list).toHaveBeenCalled() // 刷新列表
  })
})