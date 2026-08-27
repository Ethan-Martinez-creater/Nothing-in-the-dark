import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { EvidenceSummary } from '@/types/api'

import EvidenceSidebar from './EvidenceSidebar.vue'

function makeSummary(overrides: Partial<EvidenceSummary> = {}): EvidenceSummary {
  return {
    case_id: 'case-1',
    claims: [
      {
        id: 'claim-1',
        text: '官方账号未发布相关公告',
        status: 'open',
        verdict: null,
        confidence: 0,
        created_at: '2026-08-01T00:00:00Z',
        evidence: [
          {
            id: 'ev-1',
            case_id: 'case-1',
            claim_id: 'claim-1',
            source_type: 'post',
            source_id: 'post-1',
            stance: 'oppose',
            excerpt: '官方账号发布辟谣声明',
            relevance: 0.9,
            metadata_json: {},
            created_at: '2026-08-01T00:00:00Z',
          },
          {
            id: 'ev-2',
            case_id: 'case-1',
            claim_id: 'claim-1',
            source_type: 'post',
            source_id: 'post-2',
            stance: 'support',
            excerpt: '多账号引用旧闻',
            relevance: 0.4,
            metadata_json: {},
            created_at: '2026-08-01T00:00:00Z',
          },
        ],
      },
      {
        id: 'claim-2',
        text: '传播规模超过百万',
        status: 'verified',
        verdict: 'refuted',
        confidence: 0.9,
        created_at: '2026-08-01T00:00:00Z',
        evidence: [],
      },
    ],
    unassigned: [
      {
        id: 'ev-9',
        case_id: 'case-1',
        claim_id: null,
        source_type: 'profile',
        source_id: 'profile-1',
        stance: 'context',
        excerpt: '无主张归属的背景资料',
        relevance: 0.2,
        metadata_json: {},
        created_at: '2026-08-01T00:00:00Z',
      },
    ],
    ...overrides,
  }
}

describe('EvidenceSidebar', () => {
  it('offers human review actions on each claim', () => {
    const wrapper = mount(EvidenceSidebar, {
      props: { open: true, summary: makeSummary() },
    })
    const buttons = wrapper.findAll('.review-actions button')
    expect(buttons.length).toBeGreaterThanOrEqual(2)
    expect(wrapper.text()).toContain('确认')
    expect(wrapper.text()).toContain('驳回')
  })

  it('renders claim verdict chips and stance labels', () => {
    const wrapper = mount(EvidenceSidebar, {
      props: { open: true, summary: makeSummary() },
    })
    expect(wrapper.text()).toContain('官方账号未发布相关公告')
    expect(wrapper.text()).toContain('待核查')
    expect(wrapper.text()).toContain('已反驳')
    expect(wrapper.text()).toContain('90%')
    expect(wrapper.text()).toContain('支持')
    expect(wrapper.text()).toContain('反驳')
    expect(wrapper.text()).toContain('相关度 0.90')
    expect(wrapper.find('.stance-support').exists()).toBe(true)
    expect(wrapper.find('.stance-oppose').exists()).toBe(true)
  })

  it('renders unassigned evidence in its own section', () => {
    const wrapper = mount(EvidenceSidebar, {
      props: { open: true, summary: makeSummary() },
    })
    expect(wrapper.text()).toContain('COLLECTED (1)')
    expect(wrapper.text()).toContain('无主张归属的背景资料')
    expect(wrapper.text()).toContain('背景')
  })

  it('shows empty state with a run-analysis call to action', () => {
    const wrapper = mount(EvidenceSidebar, {
      props: {
        open: true,
        summary: { case_id: 'case-1', claims: [], unassigned: [] },
      },
    })
    expect(wrapper.text()).toContain('暂无证据数据')
    expect(wrapper.text()).toContain('发起含事实核查的分析')
  })

  it('emits runAnalysis from the empty-state call to action', async () => {
    const wrapper = mount(EvidenceSidebar, {
      props: {
        open: true,
        summary: { case_id: 'case-1', claims: [], unassigned: [] },
      },
    })
    await wrapper.find('.evidence-empty-guide button').trigger('click')
    expect(wrapper.emitted('runAnalysis')).toHaveLength(1)
  })

  it('labels collected social posts with platform and author', () => {
    const wrapper = mount(EvidenceSidebar, {
      props: {
        open: true,
        summary: {
          case_id: 'case-1',
          claims: [],
          unassigned: [
            {
              id: 'ev-10',
              case_id: 'case-1',
              claim_id: null,
              source_type: 'social_post',
              source_id: 'post-10',
              stance: 'context',
              excerpt: '采集到的微博帖子内容',
              relevance: 0.5,
              metadata_json: { platform: 'weibo', author: '现场观察员' },
              created_at: '2026-08-01T00:00:00Z',
            },
          ],
        },
      },
    })
    expect(wrapper.text()).toContain('微博 · 现场观察员')
    expect(wrapper.text()).toContain('采集到的微博帖子内容')
  })

  it('emits close when the close button is clicked', async () => {
    const wrapper = mount(EvidenceSidebar, {
      props: { open: true, summary: makeSummary() },
    })
    await wrapper.find('.evidence-header button').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('does not render when closed', () => {
    const wrapper = mount(EvidenceSidebar, {
      props: { open: false, summary: makeSummary() },
    })
    // 面板改为 v-if 渲染：未打开时组件完全不挂载。
    expect(wrapper.find('.evidence-sidebar').exists()).toBe(false)
  })
})
