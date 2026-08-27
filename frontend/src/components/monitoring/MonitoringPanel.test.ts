import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MonitoringPanel from './MonitoringPanel.vue'

const apiMock = vi.hoisted(() => ({
  listMonitors: vi.fn(),
  listAlerts: vi.fn(),
  listMonitorRules: vi.fn(),
  listMonitorExecutions: vi.fn(),
  createMonitor: vi.fn(),
  pauseMonitor: vi.fn(),
  resumeMonitor: vi.fn(),
  runMonitorNow: vi.fn(),
  acknowledgeAlert: vi.fn(),
  resolveAlert: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

function monitorFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'm1',
    case_id: 'c1',
    name: '每日监测',
    enabled: true,
    schedule_type: 'interval',
    interval_seconds: 3600,
    cron: null,
    timezone: 'Asia/Shanghai',
    query_spec: {},
    platforms: ['weibo'],
    account_watchlist: [],
    lookback_seconds: 3600,
    analysis_policy: {},
    version: 1,
    created_at: '2026-08-20T00:00:00Z',
    updated_at: '2026-08-20T00:00:00Z',
    ...overrides,
  }
}

describe('MonitoringPanel', () => {
  beforeEach(() => {
    apiMock.listMonitors.mockResolvedValue([])
    apiMock.listAlerts.mockResolvedValue([])
    apiMock.listMonitorRules.mockResolvedValue([])
    apiMock.listMonitorExecutions.mockResolvedValue([])
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders empty state when no monitors', async () => {
    const wrapper = mount(MonitoringPanel, {
      props: { caseId: 'c1', open: true },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('尚未创建监测')
    expect(wrapper.text()).toContain('暂无告警')
  })

  it('renders error state with retry when load fails', async () => {
    apiMock.listMonitors.mockRejectedValueOnce(new Error('boom'))
    const wrapper = mount(MonitoringPanel, {
      props: { caseId: 'c1', open: true },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('加载监测数据失败')
    expect(wrapper.text()).toContain('重试')
  })

  it('renders monitor list and its metadata', async () => {
    apiMock.listMonitors.mockResolvedValue([monitorFixture()])
    const wrapper = mount(MonitoringPanel, {
      props: { caseId: 'c1', open: true },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('每日监测')
    expect(wrapper.text()).toContain('运行中')
    expect(wrapper.text()).toContain('微博')
  })

  it('renders alert inbox with acknowledge action', async () => {
    apiMock.listAlerts.mockResolvedValue([
      {
        id: 'a1',
        monitor_id: 'm1',
        rule_id: 'r1',
        fingerprint: 'f',
        cooldown_bucket: 'b',
        first_seen_at: '2026-08-20T00:00:00Z',
        last_seen_at: '2026-08-20T00:00:00Z',
        trigger_count: 1,
        status: 'open',
        evidence_refs: {},
        metric_snapshot: {},
        explanation: '帖子量突增',
        acknowledged_by: null,
        acknowledged_at: null,
        created_at: '2026-08-20T00:00:00Z',
        updated_at: '2026-08-20T00:00:00Z',
      },
    ])
    const wrapper = mount(MonitoringPanel, {
      props: { caseId: 'c1', open: true },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('帖子量突增')
    expect(wrapper.text()).toContain('确认')
  })
})
