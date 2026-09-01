// Async progressive collection run API 模块。
// CollectionRun 是后台异步采集的运行记录（审批冻结 snapshot 的可恢复执行）。
import { http } from '@/services/api'

export type CollectionRunStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled'

export type CollectionRunPhase = 'discovery' | 'deep'

export interface CollectionRunPlatform {
  status: string
  attempts: number
  posts_collected: number
  comments_collected: number
  started_at: string | null
  completed_at: string | null
  error_code: string | null
  error_message: string | null
}

export interface CollectionRun {
  id: string
  case_id: string
  phase: CollectionRunPhase
  status: CollectionRunStatus
  posts_collected: number
  comments_collected: number
  collection_definition_id: string | null
  collection_definition_version: number | null
  trigger_run_id: string | null
  trigger_tool_call_id: string | null
  approval_id: string | null
  platforms: string[]
  platform_progress: Record<string, CollectionRunPlatform>
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export const ACTIVE_RUN_STATUSES: CollectionRunStatus[] = ['queued', 'running']

export function isActiveCollectionRun(run: CollectionRun): boolean {
  return ACTIVE_RUN_STATUSES.includes(run.status)
}

export const collectionRunApi = {
  async list(
    caseId: string,
    params: {
      active?: boolean
      status?: string
      phase?: string
      limit?: number
    } = {},
  ): Promise<CollectionRun[]> {
    const { data } = await http.get<CollectionRun[]>(
      `/cases/${caseId}/collection-runs`,
      { params },
    )
    return data
  },
  async get(caseId: string, runId: string): Promise<CollectionRun> {
    const { data } = await http.get<CollectionRun>(
      `/cases/${caseId}/collection-runs/${runId}`,
    )
    return data
  },
  async cancel(caseId: string, runId: string): Promise<CollectionRun> {
    const { data } = await http.post<CollectionRun>(
      `/cases/${caseId}/collection-runs/${runId}:cancel`,
    )
    return data
  },
}
