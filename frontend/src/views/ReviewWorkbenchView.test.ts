import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  listCases: vi.fn(),
  listReviewQueue: vi.fn(),
  reviewClaimItem: vi.fn(),
  reviewReleaseItem: vi.fn(),
  reviewDecide: vi.fn(),
  reviewReopen: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import ReviewWorkbenchView from '@/views/ReviewWorkbenchView.vue'

const caseRecord = { id: 'case-1', title: '并发案例' }

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 'rv-1',
    case_id: 'case-1',
    object_type: 'finding',
    object_id: 'finding-1',
    priority: 0,
    status: 'in_review',
    risk_level: 'medium',
    queue: 'default',
    summary: '并发审核结论',
    current_version: 7,
    decisions: [],
    comments: [],
    ...overrides,
  }
}

async function mountView() {
  const wrapper = mount(ReviewWorkbenchView)
  await flushPromises()
  return wrapper
}

async function expandAndApprove(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('.card-main').trigger('click')
  await flushPromises()
  const approveBtn = wrapper
    .findAll('.decide-actions .btn')
    .find((b) => b.text().includes('接受'))
  expect(approveBtn).toBeTruthy()
  await approveBtn!.trigger('click')
  await flushPromises()
}

describe('ReviewWorkbenchView concurrency', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.listCases.mockResolvedValue([caseRecord])
    apiMock.listReviewQueue.mockResolvedValue({
      total: 1,
      items: [makeItem()],
    })
    apiMock.reviewDecide.mockResolvedValue(makeItem({ status: 'accepted' }))
    apiMock.reviewClaimItem.mockResolvedValue(makeItem({ status: 'in_review' }))
  })

  it('F1: 队列展示真实 current_version（版本 v7）', async () => {
    const wrapper = await mountView()
    expect(apiMock.listReviewQueue).toHaveBeenCalled()
    expect(wrapper.text()).toContain('版本 v7')
  })

  it('F2: 点击接受提交 expected_version', async () => {
    const wrapper = await mountView()
    await expandAndApprove(wrapper)
    expect(apiMock.reviewDecide).toHaveBeenCalledWith(
      'case-1',
      'rv-1',
      expect.objectContaining({
        decision: 'approved',
        expected_version: 7,
      }),
    )
  })

  it('F3: review_version_conflict 显示冲突文案并 reload queue，不自动重试', async () => {
    apiMock.reviewDecide.mockRejectedValue({
      response: { data: { code: 'review_version_conflict' } },
    })
    const wrapper = await mountView()
    const queueCallsBefore = apiMock.listReviewQueue.mock.calls.length
    await expandAndApprove(wrapper)

    expect(wrapper.text()).toContain('该审核项已被其他操作更新，请基于最新状态重新审核。')
    // 冲突后自动 reload queue（不自动重试旧 decision）。
    expect(apiMock.listReviewQueue.mock.calls.length).toBeGreaterThan(
      queueCallsBefore,
    )
    expect(apiMock.reviewDecide).toHaveBeenCalledTimes(1)
  })
})
