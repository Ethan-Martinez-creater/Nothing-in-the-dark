import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import UnassignedEvidenceList from './UnassignedEvidenceList.vue'
import type { EvidenceItem } from '@/types/api'

function makeItem(overrides: Partial<EvidenceItem> = {}): EvidenceItem {
  return {
    id: 'ev-u1',
    case_id: 'case-1',
    claim_id: null,
    source_type: 'social_post',
    source_id: 'p-9',
    stance: 'context',
    excerpt: '未分组证据摘录',
    relevance: 0.4,
    metadata_json: { platform: 'weibo', author: '路人甲' },
    created_at: '2026-08-01T00:00:00+00:00',
    ...overrides,
  }
}

describe('UnassignedEvidenceList', () => {
  it('renders stance, excerpt, source type and metadata without faking titles', () => {
    const wrapper = mount(UnassignedEvidenceList, {
      props: { items: [makeItem()] },
    })
    const text = wrapper.text()
    expect(text).toContain('背景')
    expect(text).toContain('未分组证据摘录')
    expect(text).toContain('social_post')
    expect(text).toContain('微博 · 路人甲')
    expect(text).toContain('相关度 0.40')
  })

  it('emits the selected item on click', async () => {
    const item = makeItem()
    const wrapper = mount(UnassignedEvidenceList, {
      props: { items: [item] },
    })
    await wrapper.find('.uev__item').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([item])
  })

  it('shows an empty hint instead of fabricating rows', () => {
    const wrapper = mount(UnassignedEvidenceList, { props: { items: [] } })
    expect(wrapper.text()).toContain('暂无未归属证据')
  })
})
