import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({
  reviewClaim: vi.fn(),
  getEvidenceProvenance: vi.fn(),
}))

vi.mock('@/services/api', () => ({ api: apiMock }))

import EvidenceDetailPanel from './EvidenceDetailPanel.vue'
import type { ClaimEvidence, EvidenceItem, ProvenanceResponse } from '@/types/api'

function makeClaim(): ClaimEvidence {
  return {
    id: 'claim-1',
    text: '官方已确认延期开学',
    status: 'pending',
    verdict: '',
    confidence: 0.7,
    created_at: '2026-08-01T00:00:00+00:00',
    evidence: [],
  }
}

function makeItem(): EvidenceItem {
  return {
    id: 'ev-1',
    case_id: 'case-1',
    claim_id: 'claim-1',
    source_type: 'social_post',
    source_id: 'p-1',
    stance: 'support',
    excerpt: '公告原文摘录',
    relevance: 0.92,
    metadata_json: {
      platform: 'weibo',
      author: '账号A',
      url: 'https://weibo.com/p/1',
    },
    created_at: '2026-08-01T00:00:00+00:00',
  }
}

function makeProvenance(): ProvenanceResponse {
  return {
    root: { type: 'evidence', id: 'ev-1' },
    upstream: [],
    downstream: [
      { type: 'finding', id: 'f-1', relation: 'supports', label: '协同传播结论' },
    ],
    warnings: [],
  }
}

describe('EvidenceDetailPanel', () => {
  beforeEach(() => {
    apiMock.reviewClaim.mockReset()
    apiMock.reviewClaim.mockResolvedValue({ id: 'claim-1', status: 'human_confirmed' })
    apiMock.getEvidenceProvenance.mockReset()
    apiMock.getEvidenceProvenance.mockResolvedValue(makeProvenance())
  })

  it('prompts selection when nothing selected', () => {
    const wrapper = mount(EvidenceDetailPanel, {
      props: { caseId: 'case-1', claim: null, item: null },
    })
    expect(wrapper.text()).toContain('在左侧选择主张或证据')
  })

  it('shows claim full text with stance groups and review actions', async () => {
    const wrapper = mount(EvidenceDetailPanel, {
      props: { caseId: 'case-1', claim: makeClaim(), item: null },
    })
    const text = wrapper.text()
    expect(text).toContain('主张详情')
    expect(text).toContain('官方已确认延期开学')
    expect(text).toContain('支持（0）')
    expect(text).toContain('确认主张')

    const confirmButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '确认主张')
    await confirmButton!.trigger('click')
    await flushPromises()
    expect(apiMock.reviewClaim).toHaveBeenCalledWith('case-1', 'claim-1', true)
    expect(wrapper.emitted('reviewed')).toHaveLength(1)
  })

  it('shows evidence source metadata and related findings via provenance', async () => {
    const wrapper = mount(EvidenceDetailPanel, {
      props: { caseId: 'case-1', claim: null, item: makeItem() },
    })
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('证据详情')
    expect(text).toContain('公告原文摘录')
    expect(text).toContain('微博')
    expect(text).toContain('账号A')
    expect(text).toContain('0.92')
    expect(apiMock.getEvidenceProvenance).toHaveBeenCalledWith('case-1', 'ev-1')
    expect(text).toContain('协同传播结论')
  })

  it('shows no-related-findings hint when provenance downstream is empty', async () => {
    apiMock.getEvidenceProvenance.mockResolvedValue({
      root: { type: 'evidence', id: 'ev-1' },
      upstream: [],
      downstream: [],
      warnings: [],
    })
    const wrapper = mount(EvidenceDetailPanel, {
      props: { caseId: 'case-1', claim: null, item: makeItem() },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('暂无引用该证据的 Finding')
  })
})
