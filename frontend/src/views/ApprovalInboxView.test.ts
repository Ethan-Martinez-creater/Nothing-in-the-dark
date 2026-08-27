import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listApprovals: vi.fn(),
  getApprovalStats: vi.fn(),
  decideApproval: vi.fn(),
  expireOverdueApprovals: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import ApprovalInboxView from '@/views/ApprovalInboxView.vue'

function makeApproval(overrides: Record<string, unknown> = {}) {
  return {
    id: 'appr-1',
    run_id: 'run-1',
    action: 'collect_social_posts',
    reason: '真实采集需要人工批准',
    status: 'pending',
    approval_type: 'tool_execution',
    risk_level: 'high',
    scope: 'case',
    requested_action: 'collect_social_posts',
    redacted_preview: '平台 weibo，条数上限 150',
    allowed_decisions: ['approve', 'edit_and_approve', 'reject', 'cancel'],
    expires_at: '2026-08-24T00:00:00Z',
    decision_payload: {},
    decided_at: null,
    actor: null,
    created_at: '2026-08-23T00:00:00Z',
    request_summary: '平台: weibo',
    approval_kind: 'collect',
    ...overrides,
  }
}

const stats = {
  total: 2,
  decided: 1,
  approved: 1,
  approved_with_edits: 0,
  rejected: 0,
  expired: 0,
  cancelled: 0,
  approval_rate: 0.5,
  edit_rate: 0,
  rejection_rate: 0,
  expiry_rate: 0,
}

async function mountView() {
  const wrapper = mount(ApprovalInboxView)
  await flushPromises()
  return wrapper
}

describe('ApprovalInboxView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.listApprovals.mockResolvedValue([makeApproval()])
    apiMock.getApprovalStats.mockResolvedValue(stats)
    apiMock.decideApproval.mockResolvedValue(makeApproval({ status: 'approved' }))
    apiMock.expireOverdueApprovals.mockResolvedValue({ expired: 1 })
  })

  it('加载并渲染审批列表与统计', async () => {
    const wrapper = await mountView()
    expect(apiMock.listApprovals).toHaveBeenCalled()
    expect(apiMock.getApprovalStats).toHaveBeenCalled()
    expect(wrapper.text()).toContain('collect_social_posts')
    expect(wrapper.text()).toContain('真实采集需要人工批准')
    expect(wrapper.text()).toContain('50.0%') // 批准率
  })

  it('空状态渲染', async () => {
    apiMock.listApprovals.mockResolvedValue([])
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('没有符合筛选条件的审批')
  })

  it('展开详情后可批准并调用 decideApproval', async () => {
    const wrapper = await mountView()
    const card = wrapper.find('.card-main')
    await card.trigger('click')
    await flushPromises()
    const approveBtn = wrapper.findAll('.decision-actions .btn').find((b) => b.text().includes('批准'))
    expect(approveBtn).toBeTruthy()
    await approveBtn!.trigger('click')
    await flushPromises()
    expect(apiMock.decideApproval).toHaveBeenCalledWith(
      'appr-1',
      expect.objectContaining({ decision: 'approve' }),
    )
  })

  it('清理过期按钮调用 expireOverdueApprovals', async () => {
    const wrapper = await mountView()
    const btn = wrapper.findAll('.header-actions .btn').find((b) => b.text().includes('清理过期'))
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()
    expect(apiMock.expireOverdueApprovals).toHaveBeenCalled()
  })
})
