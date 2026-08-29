import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  reviewClaim: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import EvidenceClaimList from './EvidenceClaimList.vue'
import type { ClaimEvidence } from '@/types/api'

function makeClaim(overrides: Partial<ClaimEvidence> = {}): ClaimEvidence {
  return {
    id: 'claim-1',
    text: '官方已确认延期开学',
    status: 'pending',
    verdict: '',
    confidence: 0.7,
    created_at: '2026-08-01T00:00:00+00:00',
    evidence: [
      {
        id: 'ev-1',
        case_id: 'case-1',
        claim_id: 'claim-1',
        source_type: 'social_post',
        source_id: 'p-1',
        stance: 'support',
        excerpt: '公告原文摘录',
        relevance: 0.92,
        metadata_json: { platform: 'weibo', author: '账号A' },
        created_at: '2026-08-01T00:00:00+00:00',
      },
      {
        id: 'ev-2',
        case_id: 'case-1',
        claim_id: 'claim-1',
        source_type: 'social_post',
        source_id: 'p-2',
        stance: 'oppose',
        excerpt: '质疑评论摘录',
        relevance: 0.61,
        metadata_json: {},
        created_at: '2026-08-01T00:00:00+00:00',
      },
    ],
    ...overrides,
  }
}

describe('EvidenceClaimList', () => {
  beforeEach(() => {
    apiMock.reviewClaim.mockReset()
    apiMock.reviewClaim.mockResolvedValue({ id: 'claim-1', status: 'human_confirmed' })
  })

  it('renders claims with stance-grouped evidence', () => {
    const wrapper = mount(EvidenceClaimList, {
      props: { claims: [makeClaim()], caseId: 'case-1' },
    })
    const text = wrapper.text()
    expect(text).toContain('官方已确认延期开学')
    expect(text).toContain('支持')
    expect(text).toContain('反驳')
    expect(text).toContain('公告原文摘录')
    expect(text).toContain('0.92')
  })

  it('emits selection events for claim and evidence', async () => {
    const wrapper = mount(EvidenceClaimList, {
      props: { claims: [makeClaim()], caseId: 'case-1' },
    })
    await wrapper.find('.ecl__claim').trigger('click')
    expect(wrapper.emitted('selectClaim')?.[0]?.[0]).toMatchObject({ id: 'claim-1' })

    await wrapper.find('.ecl__item').trigger('click')
    const payload = wrapper.emitted('selectEvidence')?.[0]?.[0] as {
      claim: { id: string }
      item: { id: string }
    }
    expect(payload.claim.id).toBe('claim-1')
    expect(payload.item.id).toBe('ev-1')
  })

  it('submits human review and notifies parent', async () => {
    const wrapper = mount(EvidenceClaimList, {
      props: { claims: [makeClaim()], caseId: 'case-1' },
    })
    const confirmButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '确认')
    await confirmButton!.trigger('click')
    await flushPromises()
    expect(apiMock.reviewClaim).toHaveBeenCalledWith('case-1', 'claim-1', true)
    expect(wrapper.emitted('reviewed')).toHaveLength(1)
    // 状态更新为人工确认
    expect(wrapper.text()).toContain('人工确认')
  })

  it('shows empty hint when no claims match the filter', () => {
    const wrapper = mount(EvidenceClaimList, {
      props: { claims: [], caseId: 'case-1' },
    })
    expect(wrapper.text()).toContain('当前筛选下没有主张')
  })
})
