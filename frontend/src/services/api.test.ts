import { beforeEach, describe, expect, it, vi } from 'vitest'

// Mock axios BEFORE importing the module under test: the http instance the
// api object calls must be the same object the tests assert on.
const http = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('axios', () => ({
  default: { create: vi.fn(() => http) },
}))

import { api } from './api'

describe('api transport', () => {
  beforeEach(() => {
    http.get.mockReset()
    http.post.mockReset()
  })

  it('sendMessage posts content with approve_crawl flag', async () => {
    const run = { id: 'run-1' }
    http.post.mockResolvedValue({ data: run })
    const result = await api.sendMessage('case-1', '帮我分析', true)
    expect(http.post).toHaveBeenCalledWith('/cases/case-1/messages', {
      content: '帮我分析',
      approve_crawl: true,
    })
    expect(result).toBe(run)
  })

  it('sendMessage defaults approve_crawl to false', async () => {
    http.post.mockResolvedValue({ data: {} })
    await api.sendMessage('case-1', '只是问问')
    expect(http.post).toHaveBeenCalledWith('/cases/case-1/messages', {
      content: '只是问问',
      approve_crawl: false,
    })
  })

  it('sendMessage attaches artifact_id for artifact follow-up', async () => {
    http.post.mockResolvedValue({ data: {} })
    await api.sendMessage('case-1', '解释这个结论', false, 'art-9')
    expect(http.post).toHaveBeenCalledWith('/cases/case-1/messages', {
      content: '解释这个结论',
      approve_crawl: false,
      artifact_id: 'art-9',
    })
  })

  it('steerRun posts a steering instruction to the running run', async () => {
    http.post.mockResolvedValue({ data: { id: 's-1' } })
    const result = await api.steerRun('run-1', '请优先核查官方账号')
    expect(http.post).toHaveBeenCalledWith('/runs/run-1/steering', {
      content: '请优先核查官方账号',
    })
    expect(result).toEqual({ id: 's-1' })
  })

  it('reviewClaim posts the human review decision', async () => {
    http.post.mockResolvedValue({ data: { id: 'claim-1', status: 'human_confirmed' } })
    await api.reviewClaim('case-1', 'claim-1', true, '属实')
    expect(http.post).toHaveBeenCalledWith('/cases/case-1/claims/claim-1/review', {
      confirmed: true,
      note: '属实',
    })
  })

  it('confirmPropagationEdge posts the human confirmation', async () => {
    http.post.mockResolvedValue({ data: { id: 'edge-1' } })
    await api.confirmPropagationEdge('case-1', 'edge-1', true, '人工核实通过')
    expect(http.post).toHaveBeenCalledWith(
      '/cases/case-1/propagation-edges/edge-1/confirmation',
      { confirmed: true, note: '人工核实通过' },
    )
  })

  it('approveRun posts decision with note', async () => {
    http.post.mockResolvedValue({ data: { id: 'run-1' } })
    await api.approveRun('run-1', 'appr-9', true, '允许采集')
    expect(http.post).toHaveBeenCalledWith('/runs/run-1/approve', {
      approval_id: 'appr-9',
      decision: true,
      note: '允许采集',
    })
  })

  it('listRunEvents passes the cursor as after_id', async () => {
    http.get.mockResolvedValue({ data: [] })
    await api.listRunEvents('run-1', 42)
    expect(http.get).toHaveBeenCalledWith('/runs/run-1/events', {
      params: { after_id: 42 },
    })
  })

  it('getRunTrace fetches the full trace', async () => {
    const trace = { run: { id: 'run-1' }, tool_calls: [], model_calls: [], approvals: [], events: [] }
    http.get.mockResolvedValue({ data: trace })
    const result = await api.getRunTrace('run-1')
    expect(http.get).toHaveBeenCalledWith('/runs/run-1/trace')
    expect(result).toBe(trace)
  })

  it('getEvidenceSummary fetches the case evidence summary', async () => {
    const summary = {
      case_id: 'case-1',
      claims: [{ id: 'claim-1', evidence: [] }],
      unassigned: [],
    }
    http.get.mockResolvedValue({ data: summary })
    const result = await api.getEvidenceSummary('case-1')
    expect(http.get).toHaveBeenCalledWith('/cases/case-1/evidence-summary')
    expect(result).toBe(summary)
  })

  describe('runEventStreamUrl', () => {
    it('resolves a relative base URL against window origin', () => {
      const url = api.runEventStreamUrl('run-1', 0)
      expect(url).toBe(`${window.location.origin}/api/v1/runs/run-1/events/stream?cursor=0`)
    })

    it('appends the resume cursor', () => {
      const url = api.runEventStreamUrl('run-1', 7)
      expect(url).toContain('cursor=7')
    })
  })
})
