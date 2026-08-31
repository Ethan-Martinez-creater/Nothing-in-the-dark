<script setup lang="ts">
// Optimization V2 (M3.9 + C9.2)：Investigation Overview。
// Scope（Active Collection Definition）+ 当前状态 + Plan 区域（M5.7 迁入
// 的显式目标与计划图）。Copilot 由 Shell 右侧提供。
// 支持编辑调查元数据（标题/主题/说明/平台/时间范围），便于创建后修正范围。
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Pencil, X } from 'lucide-vue-next'

import CollectionDefinitionCard from '@/components/collection/CollectionDefinitionCard.vue'
import GoalPlanPanel from '@/components/goals/GoalPlanPanel.vue'
import { api } from '@/services/api'
import type { AgentRun, Artifact, CaseRecord } from '@/types/api'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const investigation = ref<CaseRecord | null>(null)
const runs = ref<AgentRun[]>([])
const artifacts = ref<Artifact[]>([])
const loading = ref(true)

const activeRuns = computed(() =>
  runs.value.filter((run) => ['pending', 'running', 'waiting_approval'].includes(run.status)),
)

// ---- 编辑调查信息 ----
const editOpen = ref(false)
const saving = ref(false)
const editError = ref('')
const editForm = reactive({
  title: '',
  topic: '',
  description: '',
  platforms: [] as string[],
  timeStart: '',
  timeEnd: '',
})
const PLATFORM_OPTIONS = [
  { id: 'weibo', label: '微博' },
  { id: 'douyin', label: '抖音' },
  { id: 'bilibili', label: '哔哩哔哩' },
  { id: 'zhihu', label: '知乎' },
  { id: 'tieba', label: '百度贴吧' },
]

function openEdit() {
  const c = investigation.value
  if (!c) return
  editForm.title = c.title
  editForm.topic = c.topic
  editForm.description = c.description
  editForm.platforms = [...c.platforms]
  editForm.timeStart = c.time_range?.start?.slice(0, 10) ?? ''
  editForm.timeEnd = c.time_range?.end?.slice(0, 10) ?? ''
  editError.value = ''
  editOpen.value = true
}

function toggleEditPlatform(platform: string) {
  editForm.platforms = editForm.platforms.includes(platform)
    ? editForm.platforms.filter((item) => item !== platform)
    : [...editForm.platforms, platform]
}

async function saveEdit() {
  if (!editForm.title.trim() || editForm.platforms.length === 0) {
    editError.value = '标题不能为空，且至少选择一个平台。'
    return
  }
  saving.value = true
  editError.value = ''
  try {
    const updated = await api.updateCase(caseId.value, {
      title: editForm.title.trim(),
      topic: editForm.topic.trim(),
      description: editForm.description.trim(),
      platforms: editForm.platforms,
      time_start: editForm.timeStart || undefined,
      time_end: editForm.timeEnd || undefined,
    })
    investigation.value = updated
    editOpen.value = false
  } catch (e) {
    const code = (e as { response?: { data?: { code?: string } } }).response?.data?.code
    editError.value =
      code === 'unsupported_platforms'
        ? '包含不支持的平台。'
        : '保存失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const [record, runList, artifactList] = await Promise.all([
      api.getCase(caseId.value),
      api.listCaseRuns(caseId.value),
      api.listArtifacts(caseId.value),
    ])
    investigation.value = record
    runs.value = runList
    artifacts.value = artifactList
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="ioverview">
    <p v-if="loading" class="ioverview__hint">正在加载…</p>

    <template v-else>
      <div class="ioverview__header">
        <button class="ioverview__edit-btn" type="button" @click="openEdit">
          <Pencil :size="13" /> 编辑调查信息
        </button>
      </div>

      <section class="ioverview__status" aria-label="当前状态">
        <div class="ioverview__stat">
          <span class="ioverview__stat-value">{{ activeRuns.length }}</span>
          <span class="ioverview__stat-label">进行中的分析</span>
        </div>
        <div class="ioverview__stat">
          <span class="ioverview__stat-value">{{ runs.length }}</span>
          <span class="ioverview__stat-label">历史分析</span>
        </div>
        <div class="ioverview__stat">
          <span class="ioverview__stat-value">{{ artifacts.length }}</span>
          <span class="ioverview__stat-label">分析成果</span>
        </div>
      </section>

      <CollectionDefinitionCard
        :case-id="caseId"
        :case-platforms="investigation?.platforms ?? []"
      />

      <section class="ioverview__plan" aria-label="调查计划">
        <h3 class="ioverview__plan-title">Plan · 目标与计划图</h3>
        <GoalPlanPanel :case-id="caseId" />
      </section>

      <div v-if="editOpen" class="ioverview__modal" role="dialog" aria-modal="true" aria-label="编辑调查信息">
        <div class="ioverview__modal-card">
          <div class="ioverview__modal-head">
            <h3>编辑调查信息</h3>
            <button class="ioverview__modal-close" type="button" aria-label="关闭" @click="editOpen = false">
              <X :size="16" />
            </button>
          </div>

          <label class="ioverview__field">
            <span>标题</span>
            <input v-model="editForm.title" type="text" />
          </label>
          <label class="ioverview__field">
            <span>主题词</span>
            <input v-model="editForm.topic" type="text" placeholder="用于检索与 Agent 分析的默认主题" />
          </label>
          <label class="ioverview__field">
            <span>补充说明</span>
            <textarea v-model="editForm.description" rows="3"></textarea>
          </label>

          <div class="ioverview__field">
            <span>平台</span>
            <div class="ioverview__platforms">
              <button
                v-for="platform in PLATFORM_OPTIONS"
                :key="platform.id"
                type="button"
                class="ioverview__platform"
                :class="{ selected: editForm.platforms.includes(platform.id) }"
                @click="toggleEditPlatform(platform.id)"
              >
                {{ platform.label }}
              </button>
            </div>
          </div>

          <div class="ioverview__field">
            <span>时间范围 <small>可选</small></span>
            <div class="ioverview__dates">
              <input v-model="editForm.timeStart" type="date" />
              <span>至</span>
              <input v-model="editForm.timeEnd" type="date" />
            </div>
          </div>

          <p v-if="editError" class="ioverview__modal-error">{{ editError }}</p>

          <div class="ioverview__modal-actions">
            <button class="btn ghost" type="button" @click="editOpen = false">取消</button>
            <button class="btn primary" type="button" :disabled="saving" @click="saveEdit">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ioverview {
  max-width: 880px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.ioverview__hint {
  color: var(--text-muted);
  font-size: 13px;
}

.ioverview__status {
  display: flex;
  gap: 12px;
}

.ioverview__stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.ioverview__stat-value {
  font-size: 22px;
  font-weight: 700;
}

.ioverview__stat-label {
  font-size: 12px;
  color: var(--text-muted);
}

.ioverview__plan {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.ioverview__plan-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.ioverview__header {
  display: flex;
  justify-content: flex-end;
}

.ioverview__edit-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
}

.ioverview__modal {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
  z-index: 100;
}

.ioverview__modal-card {
  width: min(520px, 92vw);
  max-height: 86vh;
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ioverview__modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ioverview__modal-head h3 {
  margin: 0;
  font-size: 16px;
}

.ioverview__modal-close {
  border: none;
  background: none;
  cursor: pointer;
  color: var(--text-muted);
  padding: 4px;
}

.ioverview__field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
}

.ioverview__field input,
.ioverview__field textarea {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 8px 10px;
  font-size: 13px;
  color: var(--text);
  font-family: inherit;
}

.ioverview__platforms {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ioverview__platform {
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text-muted);
}

.ioverview__platform.selected {
  background: var(--cyan);
  border-color: var(--cyan);
  color: #fff;
}

.ioverview__dates {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ioverview__modal-error {
  color: #b91c1c;
  font-size: 13px;
  margin: 0;
}

.ioverview__modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
