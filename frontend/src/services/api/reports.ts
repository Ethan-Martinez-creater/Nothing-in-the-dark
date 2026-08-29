// Optimization V2 (M7.6)：Report Document API 模块。
import { http } from '@/services/api'

export type ReportStatus = 'draft' | 'in_review' | 'published' | 'archived'

export interface ReportDocument {
  id: string
  family_id: string
  case_id: string
  source_artifact_id: string
  supersedes_id: string | null
  status: ReportStatus
  title: string
  content_json: {
    title?: string
    executive_summary?: string
    sections?: Array<{ title: string; content: string }>
    citation_links?: unknown[]
    disclaimer?: string
    source_finding_ids?: string[]
  }
  lock_version: number
  published_at: string | null
  created_at: string
  updated_at: string
}

export interface ReportContentInput {
  title?: string
  executive_summary?: string
  sections?: Array<{ title: string; content: string }>
  citation_links?: unknown[]
  disclaimer?: string
  source_finding_ids?: string[]
}

export const reportApi = {
  async list(status?: ReportStatus): Promise<ReportDocument[]> {
    const { data } = await http.get<ReportDocument[]>('/reports', {
      params: status ? { status_filter: status } : {},
    })
    return data
  },
  async get(reportId: string): Promise<ReportDocument> {
    const { data } = await http.get<ReportDocument>(`/reports/${reportId}`)
    return data
  },
  async importFromArtifact(caseId: string, artifactId: string): Promise<ReportDocument> {
    const { data } = await http.post<ReportDocument>(
      `/cases/${caseId}/reports:from-artifact`,
      { artifact_id: artifactId },
    )
    return data
  },
  async update(
    reportId: string,
    payload: { expected_lock_version: number; title?: string; content?: ReportContentInput },
  ): Promise<ReportDocument> {
    const { data } = await http.patch<ReportDocument>(`/reports/${reportId}`, payload)
    return data
  },
  async submitReview(caseId: string, reportId: string): Promise<ReportDocument> {
    const { data } = await http.post<ReportDocument>(
      `/cases/${caseId}/reports/${reportId}:submit-review`,
    )
    return data
  },
  async publish(caseId: string, reportId: string): Promise<ReportDocument> {
    const { data } = await http.post<ReportDocument>(
      `/cases/${caseId}/reports/${reportId}:publish`,
    )
    return data
  },
  async archive(caseId: string, reportId: string): Promise<ReportDocument> {
    const { data } = await http.post<ReportDocument>(
      `/cases/${caseId}/reports/${reportId}:archive`,
    )
    return data
  },
  async revise(caseId: string, reportId: string): Promise<ReportDocument> {
    const { data } = await http.post<ReportDocument>(
      `/cases/${caseId}/reports/${reportId}:revise`,
    )
    return data
  },
  downloadUrl(reportId: string): string {
    return `/api/v1/reports/${reportId}/download`
  },
}
