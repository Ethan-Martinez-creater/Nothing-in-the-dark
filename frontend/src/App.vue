<script setup lang="ts">
// Optimization V2 (M1.2)：App 只承担 capability bootstrap、shell 组装与全局 modal。
// 一级导航 → GlobalSidebar，调查树 → InvestigationList，数据逻辑 → useInvestigations。
import { computed, onMounted, provide, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import CaseComposer from '@/components/CaseComposer.vue'
import GlobalSidebar from '@/components/shell/GlobalSidebar.vue'
import GlobalTopbar from '@/components/shell/GlobalTopbar.vue'
import InvestigationList from '@/components/shell/InvestigationList.vue'
import SkillsPanel from '@/components/SkillsPanel.vue'
import { useInvestigations } from '@/composables/useInvestigations'
import { api } from '@/services/api'
import type { CaseRecord, Project, SystemCapabilities } from '@/types/api'

const route = useRoute()
const router = useRouter()

const {
  cases,
  projects,
  listError,
  loadData,
  createCase,
  deleteCase: removeCase,
  createProject,
  deleteProject,
} = useInvestigations()

const capabilities = ref<SystemCapabilities | null>(null)
const demoMode = computed(() => capabilities.value?.demo_mode !== false)
const llmConfigured = computed(
  () => capabilities.value?.llm_configured ?? capabilities.value?.llm?.configured ?? true,
)

const currentCaseId = computed(() => route.params.caseId as string | undefined)
const caseTitle = computed(
  () => cases.value.find((item) => item.id === currentCaseId.value)?.title ?? null,
)

// 新建调查 modal 状态（create-in-group 携带目标项目，保持原「项目旁 +」行为）
const newChatOpen = ref(false)
const createInProject = ref<string | null>(null)
const creating = ref(false)
const createError = ref('')
const skillsOpen = ref(false)
const deleting = ref<string | null>(null)

provide('refreshCases', loadData)

async function loadCapabilities() {
  try {
    capabilities.value = await api.getCapabilities()
  } catch {
    // 拉取失败按默认（演示模式）展示，不阻塞
  }
}

function openInvestigation(caseId: string) {
  if (route.params.caseId === caseId) return
  void router.push(`/investigations/${caseId}/overview`)
}

function openNewInvestigation(projectId: string | null = null) {
  createInProject.value = projectId
  createError.value = ''
  newChatOpen.value = true
}

async function submitCase(payload: {
  topic: string
  description: string
  platforms: string[]
  time_start?: string
  time_end?: string
}) {
  creating.value = true
  createError.value = ''
  try {
    const record = await createCase(payload, createInProject.value)
    newChatOpen.value = false
    createInProject.value = null
    void router.push(`/investigations/${record.id}/overview`)
  } catch {
    createError.value = '创建调查失败，请检查后端服务后重试。'
  } finally {
    creating.value = false
  }
}

async function submitProject(title: string) {
  try {
    await createProject(title)
  } catch {
    window.alert('创建项目失败。')
  }
}

async function handleDeleteCase(item: CaseRecord) {
  if (deleting.value) return
  if (!window.confirm(`删除调查「${item.title}」？此操作不可恢复。`)) return
  deleting.value = item.id
  // 先基于本地列表计算跳转目标，再执行删除（removeCase 内部会重新拉取列表，
  // 若在其后计算会把已删条目算回来）。
  const remaining = cases.value.filter((candidate) => candidate.id !== item.id)
  const sameGroup = remaining.filter((candidate) => candidate.project_id === item.project_id)
  const next = sameGroup[0] ?? remaining[0]
  try {
    await removeCase(item)
    if (currentCaseId.value === item.id) {
      // 删除的正是当前打开的调查：优先切同分组，其次任意其他调查，删完回首页。
      if (next) {
        void router.push(`/investigations/${next.id}/overview`)
      } else {
        void router.push('/')
      }
    }
  } catch {
    window.alert('删除失败，请重试。')
  } finally {
    deleting.value = null
  }
}

async function handleDeleteProject(project: Project) {
  if (!window.confirm(`删除项目「${project.title}」及其全部调查？此操作不可恢复。`)) return
  try {
    await deleteProject(project)
  } catch {
    window.alert('删除项目失败。')
  }
}

onMounted(() => {
  void loadData()
  void loadCapabilities()
})
router.afterEach(() => void loadData())
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <GlobalSidebar
        @new-investigation="openNewInvestigation()"
        @new-project="openNewInvestigation(null)"
        @open-skills="skillsOpen = true"
      >
        <InvestigationList
          :cases="cases"
          :projects="projects"
          :current-case-id="currentCaseId"
          :error="listError"
          @open="openInvestigation"
          @create-in-group="openNewInvestigation"
          @delete-case="handleDeleteCase"
          @create-project="submitProject"
          @delete-project="handleDeleteProject"
          @retry="loadData"
        />
        <template #footer>
          <div class="runtime-card">
            <div class="runtime-row">
              <span class="status-dot"></span>
              <span>本地开发环境</span>
            </div>
            <small>LangGraph · FastAPI · Vue</small>
          </div>
        </template>
      </GlobalSidebar>
    </aside>

    <main class="main-shell">
      <GlobalTopbar
        :case-title="caseTitle"
        :case-id="currentCaseId ?? null"
        :demo-mode="demoMode"
        :llm-configured="llmConfigured"
      />
      <RouterView />
    </main>

    <div v-if="newChatOpen" class="modal-overlay" @click.self="newChatOpen = false">
      <div class="modal-card">
        <div class="modal-head">
          <h3>新建调查</h3>
          <button type="button" class="icon-button" aria-label="关闭" @click="newChatOpen = false">
            ✕
          </button>
        </div>
        <CaseComposer :submitting="creating" :demo-mode="demoMode" @submit="submitCase" />
        <p v-if="createError" class="modal-error">{{ createError }}</p>
      </div>
    </div>

    <SkillsPanel v-if="skillsOpen" @close="skillsOpen = false" />
  </div>
</template>
