// Optimization V2 (M3.9)：Collection Definition API 模块。
// 新增 API 全部走独立模块，避免继续膨胀 services/api.ts（计划书第 21 节）。
import { http } from '@/services/api'

export interface CollectionDefinition {
  id: string
  case_id: string
  version: number
  status: 'draft' | 'active' | 'superseded'
  goal: string
  platforms: string[]
  platform_queries: Record<string, string[]>
  exclusions: string[]
  filters: Record<string, unknown>
  generated_by_run_id: string | null
  created_at: string
  updated_at: string
}

export interface CollectionDefinitionPayload {
  goal: string
  platforms: string[]
  platform_queries?: Record<string, string[]>
  exclusions?: string[]
  filters?: Record<string, unknown>
}

export const collectionApi = {
  async list(caseId: string): Promise<CollectionDefinition[]> {
    const { data } = await http.get<CollectionDefinition[]>(
      `/cases/${caseId}/collection-definitions`,
    )
    return data
  },
  async getActive(caseId: string): Promise<CollectionDefinition | null> {
    try {
      const { data } = await http.get<CollectionDefinition>(
        `/cases/${caseId}/collection-definitions/active`,
      )
      return data
    } catch (error) {
      const status = (error as { response?: { status?: number } }).response?.status
      if (status === 404) return null
      throw error
    }
  },
  async create(
    caseId: string,
    payload: CollectionDefinitionPayload,
  ): Promise<CollectionDefinition> {
    const { data } = await http.post<CollectionDefinition>(
      `/cases/${caseId}/collection-definitions`,
      payload,
    )
    return data
  },
  async generate(
    caseId: string,
    goal?: string,
  ): Promise<CollectionDefinition> {
    const { data } = await http.post<CollectionDefinition>(
      `/cases/${caseId}/collection-definitions:generate`,
      { goal: goal ?? null },
    )
    return data
  },
  async revise(
    caseId: string,
    definitionId: string,
    payload: Partial<CollectionDefinitionPayload>,
  ): Promise<CollectionDefinition> {
    const { data } = await http.post<CollectionDefinition>(
      `/cases/${caseId}/collection-definitions/${definitionId}:revise`,
      payload,
    )
    return data
  },
  async activate(caseId: string, definitionId: string): Promise<CollectionDefinition> {
    const { data } = await http.post<CollectionDefinition>(
      `/cases/${caseId}/collection-definitions/${definitionId}:activate`,
    )
    return data
  },
}
