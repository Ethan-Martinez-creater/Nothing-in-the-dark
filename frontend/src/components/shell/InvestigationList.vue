<script setup lang="ts">
// Optimization V2 (M1.2)：调查列表（原 App.vue「对话记录」树）。
// 职责：搜索过滤、未分组调查 + 项目分组展示、折叠状态、新建项目内联输入。
// CRUD 动作全部通过 emit 交由宿主处理（数据逻辑在 useInvestigations）。
import { ChevronDown, ChevronRight, FolderPlus, Plus, Search, Trash2 } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import type { CaseRecord, Project } from '@/types/api'

const props = defineProps<{
  cases: CaseRecord[]
  projects: Project[]
  currentCaseId?: string
  error?: string
}>()

const emit = defineEmits<{
  (e: 'open', caseId: string): void
  (e: 'create-in-group', projectId: string | null): void
  (e: 'delete-case', item: CaseRecord): void
  (e: 'create-project', title: string): void
  (e: 'delete-project', project: Project): void
  (e: 'retry'): void
}>()

const searchQuery = ref('')
const collapsedProjects = ref<Set<string>>(new Set())
const collapsedGroup = ref(false)
const newProjectOpen = ref(false)
const newProjectTitle = ref('')
const newProjectInput = ref<HTMLInputElement | null>(null)

const filteredCases = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return props.cases
  return props.cases.filter(
    (item) => item.title.toLowerCase().includes(q) || item.topic.toLowerCase().includes(q),
  )
})

const ungroupedCases = computed(() => filteredCases.value.filter((item) => !item.project_id))

function projectCases(projectId: string): CaseRecord[] {
  return filteredCases.value.filter((item) => item.project_id === projectId)
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

function toggleProject(projectId: string) {
  const next = new Set(collapsedProjects.value)
  if (next.has(projectId)) next.delete(projectId)
  else next.add(projectId)
  collapsedProjects.value = next
}

function openProjectInput() {
  newProjectOpen.value = true
  requestAnimationFrame(() => newProjectInput.value?.focus())
}

function submitProject() {
  const title = newProjectTitle.value.trim()
  if (!title) return
  emit('create-project', title)
  newProjectOpen.value = false
  newProjectTitle.value = ''
}

function closeProjectInput() {
  newProjectOpen.value = false
  newProjectTitle.value = ''
}
</script>

<template>
  <div class="ilist">
    <div class="ilist__toolbar">
      <div class="ilist__search">
        <Search :size="14" />
        <input v-model="searchQuery" type="text" placeholder="搜索调查…" />
      </div>
      <button
        type="button"
        class="ilist__new-project"
        title="新建项目"
        @click="openProjectInput"
      >
        <FolderPlus :size="14" />
        <span>项目</span>
      </button>
    </div>

    <div v-if="newProjectOpen" class="ilist__inline-create">
      <input
        ref="newProjectInput"
        v-model="newProjectTitle"
        type="text"
        placeholder="项目名称，回车创建"
        @keydown.enter="submitProject"
        @keydown.esc="closeProjectInput"
        @blur="closeProjectInput"
      />
    </div>

    <div class="ilist__tree" aria-label="调查列表">
      <template v-if="ungroupedCases.length || projects.length">
        <div class="ilist__group-label">
          <button
            type="button"
            class="ilist__group-toggle"
            :title="collapsedGroup ? '展开调查' : '折叠调查'"
            @click="collapsedGroup = !collapsedGroup"
          >
            <ChevronRight v-if="collapsedGroup" :size="13" />
            <ChevronDown v-else :size="13" />
          </button>
          <span class="ilist__group-title">调查</span>
          <button
            type="button"
            class="ilist__group-add"
            title="新建调查"
            @click="emit('create-in-group', null)"
          >
            <Plus :size="13" />
          </button>
        </div>
        <template v-if="!collapsedGroup">
          <button
            v-for="item in ungroupedCases"
            :key="item.id"
            type="button"
            class="ilist__item"
            :class="{ 'ilist__item--active': currentCaseId === item.id }"
            :title="item.title"
            @click="emit('open', item.id)"
          >
            <span class="ilist__copy">
              <span class="ilist__title">{{ item.title }}</span>
              <span class="ilist__meta">{{ timeAgo(item.updated_at) }}</span>
            </span>
            <span
              class="ilist__delete"
              role="button"
              tabindex="0"
              aria-label="删除调查"
              @click.stop="emit('delete-case', item)"
              @keydown.enter.stop="emit('delete-case', item)"
            >
              <Trash2 :size="14" />
            </span>
          </button>
        </template>
      </template>

      <template v-for="project in projects" :key="project.id">
        <div class="ilist__group-label">
          <button
            type="button"
            class="ilist__group-toggle"
            :title="collapsedProjects.has(project.id) ? '展开项目' : '折叠项目'"
            @click="toggleProject(project.id)"
          >
            <ChevronRight v-if="collapsedProjects.has(project.id)" :size="13" />
            <ChevronDown v-else :size="13" />
          </button>
          <span class="ilist__group-title">{{ project.title }}</span>
          <button
            type="button"
            class="ilist__group-add"
            title="在此项目下新建调查"
            @click="emit('create-in-group', project.id)"
          >
            <Plus :size="13" />
          </button>
          <span
            class="ilist__delete ilist__delete--group"
            role="button"
            tabindex="0"
            aria-label="删除项目"
            @click.stop="emit('delete-project', project)"
            @keydown.enter.stop="emit('delete-project', project)"
          >
            <Trash2 :size="13" />
          </span>
        </div>
        <template v-if="!collapsedProjects.has(project.id)">
          <button
            v-for="item in projectCases(project.id)"
            :key="item.id"
            type="button"
            class="ilist__item ilist__item--indent"
            :class="{ 'ilist__item--active': currentCaseId === item.id }"
            :title="item.title"
            @click="emit('open', item.id)"
          >
            <span class="ilist__copy">
              <span class="ilist__title">{{ item.title }}</span>
              <span class="ilist__meta">{{ timeAgo(item.updated_at) }}</span>
            </span>
            <span
              class="ilist__delete"
              role="button"
              tabindex="0"
              aria-label="删除调查"
              @click.stop="emit('delete-case', item)"
              @keydown.enter.stop="emit('delete-case', item)"
            >
              <Trash2 :size="14" />
            </span>
          </button>
        </template>
      </template>

      <p v-if="error" class="ilist__empty ilist__empty--error">
        {{ error }}
        <button type="button" class="ilist__retry" @click="emit('retry')">重试</button>
      </p>
      <p v-else-if="!filteredCases.length" class="ilist__empty">
        {{ searchQuery ? '没有匹配的调查' : '还没有调查，点击「新建调查」开始分析' }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.ilist {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 0;
}

.ilist__toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 2px;
}

.ilist__search {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-soft);
}

.ilist__new-project {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}

.ilist__new-project:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.ilist__search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  color: var(--text);
}

.ilist__tree {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.ilist__group-label {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 4px 4px;
}

.ilist__group-toggle {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-soft);
  cursor: pointer;
}

.ilist__group-title {
  flex: 1;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ilist__group-add {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-soft);
  cursor: pointer;
}

.ilist__group-add:hover,
.ilist__group-toggle:hover {
  background: var(--surface-strong);
  color: var(--text);
}

.ilist__item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  margin: 1px 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.ilist__item:hover {
  background: var(--surface-strong);
}

.ilist__item--active {
  background: rgba(37, 99, 235, 0.1);
}

.ilist__item--indent {
  margin-left: 14px;
  width: calc(100% - 14px);
}

.ilist__copy {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  line-height: 1.3;
}

.ilist__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.ilist__meta {
  font-size: 11px;
  color: var(--text-soft);
}

.ilist__delete {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  color: var(--text-soft);
  cursor: pointer;
  opacity: 0;
  transition: opacity 120ms ease;
}

.ilist__item:hover .ilist__delete,
.ilist__delete--group {
  opacity: 1;
}

.ilist__delete:hover {
  background: rgba(239, 68, 68, 0.12);
  color: var(--red);
}

.ilist__empty {
  padding: 10px 8px;
  font-size: 12px;
  color: var(--text-soft);
}

.ilist__empty--error {
  color: var(--red);
}

.ilist__retry {
  margin-left: 6px;
  border: 0;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  font-size: 12px;
}

.ilist__inline-create {
  padding: 4px 2px;
}

.ilist__inline-create input {
  width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--surface);
  font-size: 12px;
  color: var(--text);
  outline: none;
}
</style>
