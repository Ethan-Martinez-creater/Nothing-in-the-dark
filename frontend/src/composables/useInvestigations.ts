// Optimization V2 (M1.2)：调查/项目数据逻辑从 App.vue 抽离。
// App.vue 保留 provide('refreshCases') 转发（CaseWorkspaceView inject 的是
// App 层，与列表组件不是祖先链）。
import { computed, ref } from 'vue'
import type { Ref } from 'vue'

import { api } from '@/services/api'
import type { CaseRecord, Project } from '@/types/api'

export interface CreateCasePayload {
  topic: string
  description: string
  platforms: string[]
  time_start?: string
  time_end?: string
}

export function useInvestigations() {
  const cases: Ref<CaseRecord[]> = ref([])
  const projects: Ref<Project[]> = ref([])
  const listError = ref('')
  const loading = ref(false)

  const sortedCases = computed(() =>
    [...cases.value].sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? '')),
  )

  async function loadData() {
    loading.value = true
    try {
      const [caseList, projectList] = await Promise.all([api.listCases(), api.listProjects()])
      cases.value = caseList
      projects.value = projectList
      listError.value = ''
    } catch {
      listError.value = '调查列表加载失败，请检查后端服务后重试。'
    } finally {
      loading.value = false
    }
  }

  async function createCase(
    payload: CreateCasePayload,
    projectId: string | null = null,
  ): Promise<CaseRecord> {
    const record = await api.createCase({
      ...payload,
      project_id: projectId ?? undefined,
    })
    await loadData()
    return record
  }

  async function deleteCase(item: CaseRecord) {
    await api.deleteCase(item.id)
    // 先本地移除再向服务端校准：即使后续 loadData 出现竞态，列表也立即消失。
    cases.value = cases.value.filter((candidate) => candidate.id !== item.id)
    await loadData()
  }

  async function createProject(title: string): Promise<void> {
    await api.createProject(title)
    await loadData()
  }

  async function deleteProject(project: Project) {
    await api.deleteProject(project.id)
    projects.value = projects.value.filter((candidate) => candidate.id !== project.id)
    cases.value = cases.value.filter((candidate) => candidate.project_id !== project.id)
    await loadData()
  }

  return {
    cases,
    projects,
    sortedCases,
    listError,
    loading,
    loadData,
    createCase,
    deleteCase,
    createProject,
    deleteProject,
  }
}
