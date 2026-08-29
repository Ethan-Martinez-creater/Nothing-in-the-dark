// Optimization V2 (M4.9)：Finding API 模块。
import { http } from '@/services/api'

export type FindingKind =
  | 'opinion'
  | 'verification'
  | 'propagation'
  | 'narrative'
  | 'integrity'
  | 'manual'

export type FindingStatus =
  | 'candidate'
  | 'under_review'
  | 'verified'
  | 'rejected'
  | 'superseded'

export interface Finding {
  id: string
  case_id: string
  kind: FindingKind
  title: string
  statement: string
  status: FindingStatus
  confidence: number | null
  attributes: Record<string, unknown>
  source_run_id: string | null
  created_at: string
  updated_at: string
}

export interface FindingEvidenceLink {
  evidence_ref: string
  relation: 'supports' | 'contradicts' | 'context'
}

export interface FindingSource {
  source_type: string
  source_id: string
  source_path: string
}

export interface FindingDetail {
  finding: Finding
  evidence_links: FindingEvidenceLink[]
  sources: FindingSource[]
  review: {
    id: string
    status: string
    summary: string
    updated_at: string | null
  } | null
}

export const findingApi = {
  async list(
    caseId: string,
    params?: { kind?: string; status?: string; limit?: number },
  ): Promise<Finding[]> {
    const { data } = await http.get<Finding[]>(`/cases/${caseId}/findings`, {
      params: {
        ...(params?.kind ? { kind: params.kind } : {}),
        ...(params?.status ? { status: params.status } : {}),
        limit: params?.limit ?? 100,
      },
    })
    return data
  },
  async get(caseId: string, findingId: string): Promise<FindingDetail> {
    const { data } = await http.get<FindingDetail>(
      `/cases/${caseId}/findings/${findingId}`,
    )
    return data
  },
  async create(
    caseId: string,
    payload: {
      statement: string
      kind?: string
      confidence?: number | null
      source_type?: string
      source_id?: string
      source_path?: string
    },
  ): Promise<Finding> {
    const { data } = await http.post<Finding>(`/cases/${caseId}/findings`, payload)
    return data
  },
  async updateStatus(
    caseId: string,
    findingId: string,
    status: FindingStatus,
  ): Promise<Finding> {
    const { data } = await http.post<Finding>(
      `/cases/${caseId}/findings/${findingId}/status`,
      { status },
    )
    return data
  },
  async addEvidence(
    caseId: string,
    findingId: string,
    evidenceRef: string,
    relation: FindingEvidenceLink['relation'],
  ): Promise<Finding> {
    const { data } = await http.post<Finding>(
      `/cases/${caseId}/findings/${findingId}/evidence`,
      { evidence_ref: evidenceRef, relation },
    )
    return data
  },
  async removeEvidence(
    caseId: string,
    findingId: string,
    evidenceRef: string,
    relation: FindingEvidenceLink['relation'],
  ): Promise<Finding> {
    const { data } = await http.delete<Finding>(
      `/cases/${caseId}/findings/${findingId}/evidence`,
      { params: { evidence_ref: evidenceRef, relation } },
    )
    return data
  },
  async sync(caseId: string): Promise<{
    created: number
    skipped: number
    unsupported: number
    errors: unknown[]
  }> {
    const { data } = await http.post(`/cases/${caseId}/findings:sync`)
    return data
  },
}
