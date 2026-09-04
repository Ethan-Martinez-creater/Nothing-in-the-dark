// V3 Intelligence Depth：全局情报工作台 API 模块（Investigation Quality /
// Workspace Entities / Cross-Investigation Connections）。
import { http } from '@/services/api'

// ---- Investigation Quality（V3 §6/§23）----

export interface QualityDimension {
  key: string
  label: string
  weight: number
  score: number | null
  available: boolean
  metrics: Record<string, unknown>
}

export interface QualityGap {
  code: string
  severity: string
  object_type: string
  object_id: string | null
  message: string
  action: Record<string, unknown>
}

export interface InvestigationQuality {
  case_id: string
  overall_score: number | null
  grade: string
  dimensions: QualityDimension[]
  gaps: QualityGap[]
  warnings: Array<Record<string, unknown>>
  disclaimer: string
  computed_at: string
  algorithm_version: string
  input_fingerprint: string
}

export interface QualityAttentionItem {
  case_id: string
  overall_score: number | null
  grade: string
  computed_at: string
}

export const qualityApi = {
  async get(caseId: string): Promise<InvestigationQuality> {
    const { data } = await http.get<InvestigationQuality>(`/cases/${caseId}/quality`)
    return data
  },
  async refresh(caseId: string): Promise<InvestigationQuality> {
    const { data } = await http.post<InvestigationQuality>(`/cases/${caseId}/quality:refresh`)
    return data
  },
  async needsAttention(limit = 5): Promise<QualityAttentionItem[]> {
    const { data } = await http.get<QualityAttentionItem[]>('/quality/needs-attention', {
      params: { limit },
    })
    return data
  },
}

// ---- Cross-Investigation（V3 §42/§43）----

export interface RelatedInvestigation {
  case_id: string
  title: string
  relation_types: string[]
  relation_count: number
  max_score: number
  shared_actor_count: number
  shared_post_count: number
  shared_media_count: number
  shared_content_count: number
  has_candidate_relation: boolean
}

export interface IntelligenceConnection {
  id: string
  left_case_id: string
  right_case_id: string
  left_title: string | null
  right_title: string | null
  relation_type: string
  status: string
  score: number | null
  evidence_count: number
  algorithm_version: string
}

export const crossApi = {
  async connections(params?: {
    status?: string
    relation_type?: string
    limit?: number
  }): Promise<IntelligenceConnection[]> {
    const { data } = await http.get<IntelligenceConnection[]>('/intelligence/connections', {
      params: {
        ...(params?.status ? { status: params.status } : {}),
        ...(params?.relation_type ? { relation_type: params.relation_type } : {}),
        limit: params?.limit ?? 200,
      },
    })
    return data
  },
  async between(leftCaseId: string, rightCaseId: string): Promise<{ links: IntelligenceConnection[] }> {
    const { data } = await http.get<{ links: IntelligenceConnection[] }>(
      `/intelligence/connections/${leftCaseId}/${rightCaseId}`,
    )
    return data
  },
  async related(caseId: string, limit = 100): Promise<RelatedInvestigation[]> {
    const { data } = await http.get<RelatedInvestigation[]>(
      `/cases/${caseId}/related-investigations`,
      { params: { limit } },
    )
    return data
  },
}

// ---- Workspace Entities（V3 §33）----

export interface WorkspaceEntitySummary {
  entity_id: string
  entity_type: string
  canonical_name: string
  platforms: string[]
  investigation_count: number
  post_count: number
  comment_count: number
  last_seen_at: string | null
  risk_summary: string | null
}

export interface WorkspaceEntityProfile {
  entity_id: string
  component_key: string
  entity_ids: string[]
  entity_type: string
  canonical_name: string
  aliases: string[]
  platform_identities: Array<Record<string, string>>
  investigation_count: number
  investigations: string[]
  post_count: number
  comment_count: number
  engagement_total: number
  first_seen_at: string | null
  last_seen_at: string | null
  recent_posts: Array<Record<string, unknown>>
  risk_assessments: Array<Record<string, unknown>>
  unresolved_local_risk: Array<Record<string, unknown>>
  coordination_memberships: Array<Record<string, unknown>>
  algorithm_version: string
}

export const entityApi = {
  async list(params?: {
    query?: string
    platform?: string
    min_investigations?: number
    limit?: number
    offset?: number
  }): Promise<{ items: WorkspaceEntitySummary[]; total: number }> {
    const { data } = await http.get<{ items: WorkspaceEntitySummary[]; total: number }>(
      '/intelligence/entities',
      {
        params: {
          ...(params?.query ? { query: params.query } : {}),
          ...(params?.platform ? { platform: params.platform } : {}),
          ...(params?.min_investigations ? { min_investigations: params.min_investigations } : {}),
          limit: params?.limit ?? 50,
          offset: params?.offset ?? 0,
        },
      },
    )
    return data
  },
  async profile(entityId: string): Promise<WorkspaceEntityProfile> {
    const { data } = await http.get<WorkspaceEntityProfile>(`/intelligence/entities/${entityId}`)
    return data
  },
  async caseEntities(caseId: string): Promise<{ items: WorkspaceEntitySummary[]; total: number }> {
    const { data } = await http.get<{ items: WorkspaceEntitySummary[]; total: number }>(
      `/cases/${caseId}/entities`,
    )
    return data
  },
}
