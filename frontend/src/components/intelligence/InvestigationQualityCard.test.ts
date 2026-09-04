import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import InvestigationQualityCard from './InvestigationQualityCard.vue'
import type { InvestigationQuality } from '@/services/api/intelligence'

function makeQuality(overrides: Partial<InvestigationQuality> = {}): InvestigationQuality {
  return {
    case_id: 'case-1',
    overall_score: 68.4,
    grade: 'needs_attention',
    dimensions: [
      { key: 'collection', label: '数据采集', weight: 25, score: 0.8, available: true, metrics: {} },
      { key: 'evidence', label: '证据链', weight: 25, score: 0.6, available: true, metrics: {} },
      { key: 'finding_support', label: '结论支撑', weight: 20, score: 0.5, available: true, metrics: {} },
      { key: 'review_resolution', label: '审核闭环', weight: 10, score: 0.7, available: true, metrics: {} },
      { key: 'provenance', label: '来源追溯', weight: 10, score: 0.9, available: true, metrics: {} },
      { key: 'report_citation', label: '报告引用', weight: 10, score: null, available: false, metrics: {} },
    ],
    gaps: [
      { code: 'g1', severity: 'warning', object_type: 'finding', object_id: 'f1', message: '结论缺少支撑链接', action: {} },
      { code: 'g2', severity: 'critical', object_type: 'claim', object_id: null, message: '声明缺少证据', action: {} },
      { code: 'g3', severity: 'critical', object_type: 'claim', object_id: null, message: '声明二缺少证据', action: {} },
    ],
    warnings: [],
    disclaimer: 'Quality Score 表示调查完整度与准备度，不代表事实真实性。',
    computed_at: '2026-09-01T10:00:00+00:00',
    algorithm_version: 'quality-1.0.0',
    input_fingerprint: 'abc1234567890def',
    ...overrides,
  }
}

describe('InvestigationQualityCard', () => {
  it('renders all six dimensions with scores', () => {
    const wrapper = mount(InvestigationQualityCard, {
      props: { quality: makeQuality(), loading: false, error: '', refreshing: false },
    })
    const labels = wrapper.findAll('.iqcard__dim-label').map((node) => node.text())
    expect(labels).toEqual([
      '数据采集',
      '证据链',
      '结论支撑',
      '审核闭环',
      '来源追溯',
      '报告引用',
    ])
    expect(wrapper.text()).toContain('68.4')
    expect(wrapper.text()).toContain('需关注')
  })

  it('renders a — score for unavailable dimensions', () => {
    const wrapper = mount(InvestigationQualityCard, {
      props: { quality: makeQuality(), loading: false, error: '', refreshing: false },
    })
    const scores = wrapper.findAll('.iqcard__dim-score').map((node) => node.text())
    expect(scores).toContain('—')
  })

  it('sorts top gaps by severity and caps at five', () => {
    const quality = makeQuality({
      gaps: [
        { code: 'w1', severity: 'warning', object_type: 'finding', object_id: null, message: '警告一', action: {} },
        { code: 'c1', severity: 'critical', object_type: 'claim', object_id: null, message: '严重一', action: {} },
        { code: 'i1', severity: 'info', object_type: 'case', object_id: null, message: '提示一', action: {} },
        { code: 'c2', severity: 'critical', object_type: 'claim', object_id: null, message: '严重二', action: {} },
        { code: 'w2', severity: 'warning', object_type: 'finding', object_id: null, message: '警告二', action: {} },
        { code: 'c3', severity: 'critical', object_type: 'claim', object_id: null, message: '严重三', action: {} },
        { code: 'w3', severity: 'warning', object_type: 'finding', object_id: null, message: '警告三', action: {} },
      ],
    })
    const wrapper = mount(InvestigationQualityCard, {
      props: { quality, loading: false, error: '', refreshing: false },
    })
    const messages = wrapper.findAll('.iqcard__gap-msg').map((node) => node.text())
    expect(messages).toEqual(['严重一', '严重二', '严重三', '警告一', '警告二'])
    const severities = wrapper.findAll('.iqcard__gap-sev').map((node) => node.attributes('data-severity'))
    expect(severities[0]).toBe('critical')
  })

  it('shows insufficient_data grade with a dash score', () => {
    const wrapper = mount(InvestigationQualityCard, {
      props: {
        quality: makeQuality({ overall_score: null, grade: 'insufficient_data', gaps: [] }),
        loading: false,
        error: '',
        refreshing: false,
      },
    })
    expect(wrapper.text()).toContain('数据不足')
    expect(wrapper.find('.iqcard__score-value').text()).toBe('—')
  })

  it('renders disclaimer, computed_at and algorithm version', () => {
    const wrapper = mount(InvestigationQualityCard, {
      props: { quality: makeQuality(), loading: false, error: '', refreshing: false },
    })
    expect(wrapper.text()).toContain('不代表事实真实性')
    expect(wrapper.text()).toContain('quality-1.0.0')
    expect(wrapper.text()).toContain('评估于')
  })

  it('emits refresh on button click', () => {
    const wrapper = mount(InvestigationQualityCard, {
      props: { quality: makeQuality(), loading: false, error: '', refreshing: false },
    })
    wrapper.find('.iqcard__refresh').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })

  it('shows loading and error states', () => {
    const loading = mount(InvestigationQualityCard, {
      props: { quality: null, loading: true, error: '', refreshing: false },
    })
    expect(loading.text()).toContain('正在评估')

    const error = mount(InvestigationQualityCard, {
      props: { quality: null, loading: false, error: '质量评估加载失败。', refreshing: false },
    })
    expect(error.text()).toContain('质量评估加载失败。')
  })
})