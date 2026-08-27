<script setup lang="ts">
import {
  Boxes,
  ChevronDown,
  ChevronRight,
  FolderPlus,
  MessageSquarePlus,
  Plus,
  Search,
  Trash2,
} from 'lucide-vue-next'
import { computed, onMounted, provide, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import CaseComposer from '@/components/CaseComposer.vue'
import SkillsPanel from '@/components/SkillsPanel.vue'
import { api } from '@/services/api'
import type { CaseRecord, Project, SystemCapabilities } from '@/types/api'

const router = useRouter()
const route = useRoute()

const cases = ref<CaseRecord[]>([])
const projects = ref<Project[]>([])
// 后端运行模式（demo/real）：驱动顶栏徽标与新建会话提示，与采集链路一致。
const capabilities = ref<SystemCapabilities | null>(null)
const demoMode = computed(() => capabilities.value?.demo_mode !== false)
const llmConfigured = computed(
  () => capabilities.value?.llm_configured ?? capabilities.value?.llm?.configured ?? true,
)
const searchQuery = ref('')
const newChatOpen = ref(false)
const newProjectOpen = ref(false)
const newProjectTitle = ref('')
const newProjectInput = ref<HTMLInputElement | null>(null)
const skillsOpen = ref(false)
const creating = ref(false)
const createError = ref('')
const listError = ref('')
const deleting = ref<string | null>(null)
// 新建会话时归属的项目（项目旁 + 或对话旁 + 预选）
const createInProject = ref<string | null>(null)
// 折叠的项目 id 集合 / 「对话」分组折叠
const collapsedProjects = ref<Set<string>>(new Set())
const collapsedConversations = ref(false)
// 治理入口较多，默认收起以优先保证会话列表的可用空间。
const governanceCollapsed = ref(true)

const filteredCases = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return cases.value
  return cases.value.filter(
    (item) => item.title.toLowerCase().includes(q) || item.topic.toLowerCase().includes(q),
  )
})

// 未分类对话 + 按项目分组的对话
const ungroupedCases = computed(() => filteredCases.value.filter((item) => !item.project_id))

function projectCases(projectId: string): CaseRecord[] {
  return filteredCases.value.filter((item) => item.project_id === projectId)
}

const currentCaseId = computed(() => route.params.caseId as string | undefined)

async function loadData() {
  try {
    const [caseList, projectList] = await Promise.all([api.listCases(), api.listProjects()])
    cases.value = caseList
    projects.value = projectList
    listError.value = ''
  } catch {
    listError.value = '会话列表加载失败，请检查后端服务后重试。'
  }
}

provide('refreshCases', loadData)

async function loadCapabilities() {
  try {
    capabilities.value = await api.getCapabilities()
  } catch {
    // 拉取失败按默认（演示模式）展示，不阻塞
  }
}

function openCase(caseId: string) {
  if (route.params.caseId === caseId) return
  void router.push(`/cases/${caseId}`)
}

function openNewChat(projectId: string | null = null) {
  createInProject.value = projectId
  createError.value = ''
  newChatOpen.value = true
}

async function createCase(payload: {
  topic: string
  description: string
  platforms: string[]
  time_start?: string
  time_end?: string
}) {
  creating.value = true
  createError.value = ''
  try {
    const record = await api.createCase({
      ...payload,
      project_id: createInProject.value ?? undefined,
    })
    newChatOpen.value = false
    createInProject.value = null
    await loadData()
    void router.push(`/cases/${record.id}`)
  } catch {
    createError.value = '创建会话失败，请检查后端服务后重试。'
  } finally {
    creating.value = false
  }
}

async function createProject() {
  const title = newProjectTitle.value.trim()
  if (!title) return
  try {
    await api.createProject(title)
    newProjectTitle.value = ''
    newProjectOpen.value = false
    await loadData()
  } catch {
    window.alert('创建项目失败。')
  }
}

// 新建项目：点击按钮打开并聚焦；点击别处（失焦）或 Esc 关闭。
function openProjectInput() {
  newProjectOpen.value = true
  requestAnimationFrame(() => newProjectInput.value?.focus())
}

function closeProjectInput() {
  newProjectOpen.value = false
  newProjectTitle.value = ''
}

async function deleteCase(item: CaseRecord) {
  if (deleting.value) return
  if (!window.confirm(`删除对话「${item.title}」？此操作不可恢复。`)) return
  deleting.value = item.id
  try {
    await api.deleteCase(item.id)
    // 先本地移除再向服务端校准：即使后续 loadData 出现竞态，列表也
    // 立即消失，无需刷新页面。
    cases.value = cases.value.filter((candidate) => candidate.id !== item.id)
    if (currentCaseId.value === item.id) {
      // 删除的正是当前打开的会话：优先切到同分组（同为未分组「对话」或
      // 同一项目）的会话，没有则任意其他会话，全部删完则回首页展示系统介绍。
      const remaining = cases.value
      const sameGroup = remaining.filter((candidate) => candidate.project_id === item.project_id)
      const next = sameGroup[0] ?? remaining[0]
      if (next) {
        void router.push(`/cases/${next.id}`)
      } else {
        void router.push('/')
      }
    }
    await loadData()
  } catch {
    window.alert('删除失败，请重试。')
  } finally {
    deleting.value = null
  }
}

async function deleteProject(project: Project) {
  if (!window.confirm(`删除项目「${project.title}」及其全部对话？此操作不可恢复。`)) return
  try {
    await api.deleteProject(project.id)
    // 本地移除项目及其下会话，再向服务端校准。
    projects.value = projects.value.filter((candidate) => candidate.id !== project.id)
    cases.value = cases.value.filter((candidate) => candidate.project_id !== project.id)
    await loadData()
  } catch {
    window.alert('删除项目失败。')
  }
}

function toggleProject(projectId: string) {
  const next = new Set(collapsedProjects.value)
  if (next.has(projectId)) next.delete(projectId)
  else next.add(projectId)
  collapsedProjects.value = next
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const minutes = Math.max(1, Math.floor((Date.now() - then) / 60000))
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return new Date(iso).toLocaleDateString('zh-CN')
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
      <div class="brand">
        <div class="brand-mark">C</div>
        <div class="brand-copy">
          <strong>COIFESP</strong>
          <span>Social Intelligence</span>
        </div>
      </div>

      <!-- 上部：搜索 / 技能 / 新建等可点击区域 -->
      <div class="sidebar-tools">
        <div class="sidebar-search">
          <Search :size="14" />
          <input v-model="searchQuery" type="text" placeholder="搜索会话…" />
        </div>
        <button type="button" class="tool-button" @click="openNewChat()">
          <MessageSquarePlus :size="15" />
          <span>新建会话</span>
        </button>
        <div class="tool-row">
          <button type="button" class="tool-button" @click="openProjectInput">
            <FolderPlus :size="15" />
            <span>新建项目</span>
          </button>
          <button type="button" class="tool-button" @click="skillsOpen = true">
            <Boxes :size="15" />
            <span>技能</span>
          </button>
        </div>
        <div v-if="newProjectOpen" class="inline-create">
          <input
            ref="newProjectInput"
            v-model="newProjectTitle"
            type="text"
            placeholder="项目名称，回车创建"
            @keydown.enter="createProject"
            @keydown.esc="closeProjectInput"
            @blur="closeProjectInput"
          />
        </div>
      </div>

      <!-- 下部：对话 / 项目两级记录列表 -->
      <div class="conversation-list" aria-label="对话记录">
        <template v-if="ungroupedCases.length || projects.length">
          <!-- 「对话」分组标签始终保留：会话全部删完后仍可在此新建会话 -->
          <div class="group-label">
            <button
              type="button"
              class="group-toggle"
              :title="collapsedConversations ? '展开对话' : '折叠对话'"
              @click="collapsedConversations = !collapsedConversations"
            >
              <ChevronRight v-if="collapsedConversations" :size="13" />
              <ChevronDown v-else :size="13" />
            </button>
            <span class="group-title">对话</span>
            <button type="button" class="group-add" title="新建对话" @click="openNewChat()">
              <Plus :size="13" />
            </button>
          </div>
          <template v-if="!collapsedConversations">
            <button
              v-for="item in ungroupedCases"
              :key="item.id"
              type="button"
              class="conversation-item"
              :class="{ active: currentCaseId === item.id }"
              :title="item.title"
              @click="openCase(item.id)"
            >
              <div class="conversation-copy">
                <span class="conversation-title">{{ item.title }}</span>
                <span class="conversation-meta">{{ timeAgo(item.updated_at) }}</span>
              </div>
              <span
                class="conversation-delete"
                role="button"
                tabindex="0"
                aria-label="删除对话"
                @click.stop="deleteCase(item)"
                @keydown.enter.stop="deleteCase(item)"
              >
                <Trash2 :size="14" />
              </span>
            </button>
          </template>
        </template>

        <template v-for="project in projects" :key="project.id">
          <div class="group-label">
            <button
              type="button"
              class="group-toggle"
              :title="collapsedProjects.has(project.id) ? '展开项目' : '折叠项目'"
              @click="toggleProject(project.id)"
            >
              <ChevronRight v-if="collapsedProjects.has(project.id)" :size="13" />
              <ChevronDown v-else :size="13" />
            </button>
            <span class="group-title">{{ project.title }}</span>
            <button
              type="button"
              class="group-add"
              title="在此项目下新建对话"
              @click="openNewChat(project.id)"
            >
              <Plus :size="13" />
            </button>
            <span
              class="conversation-delete group-del"
              role="button"
              tabindex="0"
              aria-label="删除项目"
              @click.stop="deleteProject(project)"
              @keydown.enter.stop="deleteProject(project)"
            >
              <Trash2 :size="13" />
            </span>
          </div>
          <template v-if="!collapsedProjects.has(project.id)">
            <button
              v-for="item in projectCases(project.id)"
              :key="item.id"
              type="button"
              class="conversation-item conversation-item-indent"
              :class="{ active: currentCaseId === item.id }"
              :title="item.title"
              @click="openCase(item.id)"
            >
              <div class="conversation-copy">
                <span class="conversation-title">{{ item.title }}</span>
                <span class="conversation-meta">{{ timeAgo(item.updated_at) }}</span>
              </div>
              <span
                class="conversation-delete"
                role="button"
                tabindex="0"
                aria-label="删除对话"
                @click.stop="deleteCase(item)"
                @keydown.enter.stop="deleteCase(item)"
              >
                <Trash2 :size="14" />
              </span>
            </button>
          </template>
        </template>

        <p v-if="listError" class="conversation-empty conversation-error">
          {{ listError }}
          <button type="button" class="sidebar-retry" @click="loadData">重试</button>
        </p>
        <p v-else-if="!filteredCases.length" class="conversation-empty">
          {{ searchQuery ? '没有匹配的会话' : '还没有会话，点击「新建会话」开始分析' }}
        </p>
      </div>

      <nav class="sidebar-governance" aria-label="治理与控制">
        <button
          type="button"
          class="governance-toggle"
          :aria-expanded="!governanceCollapsed"
          aria-controls="governance-links"
          @click="governanceCollapsed = !governanceCollapsed"
        >
          <span class="governance-title">治理与控制</span>
          <ChevronRight v-if="governanceCollapsed" :size="14" />
          <ChevronDown v-else :size="14" />
        </button>
        <div v-show="!governanceCollapsed" id="governance-links" class="governance-links">
          <RouterLink
            to="/approvals"
            class="gov-link"
            :class="{ active: route.name === 'approval-inbox' }"
            >审批箱</RouterLink
          >
          <RouterLink
            to="/reviews"
            class="gov-link"
            :class="{ active: route.name === 'review-workbench' }"
            >审核工作台</RouterLink
          >
          <RouterLink
            to="/resilience"
            class="gov-link"
            :class="{ active: route.name === 'resilience-console' }"
            >事故处置台</RouterLink
          >
          <RouterLink
            to="/memories"
            class="gov-link"
            :class="{ active: route.name === 'memory-governance' }"
            >记忆治理</RouterLink
          >
          <RouterLink
            to="/observability"
            class="gov-link"
            :class="{ active: route.name === 'observability' }"
            >可观测性</RouterLink
          >
          <RouterLink
            to="/goals"
            class="gov-link"
            :class="{ active: route.name === 'goal-planning' }"
            >目标与计划</RouterLink
          >
          <RouterLink
            to="/subscriptions"
            class="gov-link"
            :class="{ active: route.name === 'subscriptions' }"
            >订阅与协作</RouterLink
          >
          <RouterLink
            to="/narratives"
            class="gov-link"
            :class="{ active: route.name === 'narrative-timeline' }"
            >叙事时间线</RouterLink
          >
          <RouterLink
            to="/semantics"
            class="gov-link"
            :class="{ active: route.name === 'semantic-annotations' }"
            >语义标注</RouterLink
          >
          <RouterLink
            to="/security"
            class="gov-link"
            :class="{ active: route.name === 'security-events' }"
            >安全治理</RouterLink
          >
        </div>
      </nav>

      <div class="sidebar-footer">
        <div class="runtime-card">
          <div class="runtime-row">
            <span class="status-dot"></span>
            <span>本地开发环境</span>
          </div>
          <small>LangGraph · FastAPI · Vue</small>
        </div>
      </div>
    </aside>

    <main class="main-shell">
      <header class="topbar">
        <div class="topbar-breadcrumb">
          <RouterLink to="/" class="topbar-home">工作台</RouterLink>
          <template v-if="currentCaseId">
            <span class="topbar-sep">/</span>
            <span class="topbar-case">会话 {{ currentCaseId.slice(0, 8).toUpperCase() }}</span>
          </template>
        </div>
        <div class="topbar-status">
          <span v-if="demoMode" class="demo-badge">DEMO MODE</span>
          <span v-else class="demo-badge real-badge">REAL CRAWL</span>
          <span v-if="!llmConfigured" class="demo-badge llm-missing-badge">LLM 未配置</span>
          <span>v0.1.0</span>
        </div>
      </header>
      <RouterView />
    </main>

    <div v-if="newChatOpen" class="modal-overlay" @click.self="newChatOpen = false">
      <div class="modal-card">
        <div class="modal-head">
          <h3>新建会话</h3>
          <button type="button" class="icon-button" aria-label="关闭" @click="newChatOpen = false">
            ✕
          </button>
        </div>
        <CaseComposer :submitting="creating" :demo-mode="demoMode" @submit="createCase" />
        <p v-if="createError" class="modal-error">{{ createError }}</p>
      </div>
    </div>

    <SkillsPanel v-if="skillsOpen" @close="skillsOpen = false" />
  </div>
</template>

<style scoped>
.sidebar-governance {
  margin-top: auto;
  padding: 10px 12px 6px;
  border-top: 1px solid var(--border);
}
.governance-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  padding: 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-soft);
  cursor: pointer;
}
.governance-toggle:hover {
  background: var(--surface-strong);
  color: var(--text);
}
.governance-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.governance-links {
  max-height: min(390px, 50vh);
  overflow-y: auto;
  padding-top: 4px;
}
.gov-link {
  display: flex;
  align-items: center;
  padding: 7px 10px;
  margin: 2px 0;
  border-radius: 8px;
  font-size: 13px;
  color: var(--text-muted);
  transition:
    background 120ms ease,
    color 120ms ease;
}
.gov-link:hover {
  background: var(--surface-strong);
  color: var(--text);
}
.gov-link.active {
  background: rgba(37, 99, 235, 0.1);
  color: var(--cyan-strong);
  font-weight: 600;
}
</style>
