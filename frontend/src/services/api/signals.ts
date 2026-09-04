// Optimization V2 (M6.3)：Signals + Workspace Overview API 模块。
import { http } from '@/services/api'

export interface Signal {
  id: string
  source_type: string
  source_id: string
  case_id: string
  case_title: string
  signal_type: string
  severity: string
  status: string
  title: string
  why_it_matters: string
  confidence: number | null
  evidence_refs: Record<string, unknown>
  trigger_count: number
  first_seen_at: string | null
  detected_at: string
  updated_at: string
  // V3 §57 additive 字段（Monitor 信号为默认值）
  related_case_ids: string[]
  source_label: string
  detector_version: string | null
  detector_active: boolean | null
}

export interface WorkspaceOverview {
  counts: {
    investigations: number
    open_signals: number
    pending_approvals: number
    running_runs: number
  }
  recent_investigations: Array<{
    id: string
    title: string
    topic: string
    platforms: string[]
    status: string
    updated_at: string
  }>
  top_signals: Array<{
    id: string
    signal_type: string
    severity: string
    status: string
    title: string
    why_it_matters: string
    case_id: string
    case_title: string
    detected_at: string
  }>
  recent_reports: Array<{
    artifact_id: string
    case_id: string
    title: string
    created_at: string
  }>
  // V3 §44：Home Quality 聚合（只读持久化 Quality，前端不逐 Case 重算）。
  investigations_needing_attention: Array<{
    case_id: string
    title: string
    grade: string
    overall_score: number | null
    computed_at: string
  }>
  quality_unassessed_count: number
}

export const signalApi = {
  async list(params?: {
    status?: string
    severity?: string
    case_id?: string
    signal_type?: string
    source_type?: string
    detector_active?: boolean
    limit?: number
  }): Promise<Signal[]> {
    const { data } = await http.get<Signal[]>('/signals', {
      params: {
        ...(params?.status ? { status: params.status } : {}),
        ...(params?.severity ? { severity: params.severity } : {}),
        ...(params?.case_id ? { case_id: params.case_id } : {}),
        ...(params?.signal_type ? { signal_type: params.signal_type } : {}),
        ...(params?.source_type ? { source_type: params.source_type } : {}),
        ...(params?.detector_active !== undefined
          ? { detector_active: params.detector_active }
          : {}),
        limit: params?.limit ?? 100,
      },
    })
    return data
  },
  async get(signalId: string): Promise<Signal> {
    const { data } = await http.get<Signal>(`/signals/${signalId}`)
    return data
  },
  async acknowledge(signalId: string): Promise<Signal> {
    const { data } = await http.post<Signal>(`/signals/${signalId}:acknowledge`)
    return data
  },
  async resolve(signalId: string): Promise<Signal> {
    const { data } = await http.post<Signal>(`/signals/${signalId}:resolve`)
    return data
  },
  async suppress(signalId: string): Promise<Signal> {
    const { data } = await http.post<Signal>(`/signals/${signalId}:suppress`)
    return data
  },
}

export const workspaceApi = {
  async overview(): Promise<WorkspaceOverview> {
    const { data } = await http.get<WorkspaceOverview>('/workspace/overview')
    return data
  },
}
