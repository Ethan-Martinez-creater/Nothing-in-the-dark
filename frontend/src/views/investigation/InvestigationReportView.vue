<script setup lang="ts">
// Optimization V2 (M7.6/M7.7)：调查报告工作区。
// Agent report artifact → Create Draft → 结构化编辑 → Publish（gate 校验）
// → Export HTML。Artifact 历史保持不可变（Part VIII 迁移矩阵）。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/services/api'
import {
  reportApi,
  type ReportDocument,
  type ReportStatus,
} from '@/services/api/reports'
import type { Artifact } from '@/types/api'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const reportArtifacts = ref<Artifact[]>([])
const documents = ref<ReportDocument[]>([])
const selected = ref<ReportDocument | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const editing = ref(false)
const editTitle = ref('')
const editSummary = ref('')

const statusLabels: Record<ReportStatus, string> = {
  draft: '草稿',
  in_review: '审核中',
  published: '已发布',
  archived: '已归档',
}

const activeReport = computed(() => {
  const editable = documents.value.find(
    (item) => item.status === 'draft' || item.status === 'in_review',
  )
  return editable ?? documents.value[0] ?? null
})

const latestArtifact = computed(() => reportArtifacts.value[0] ?? null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const [artifacts, docs] = await Promise.all([
      api.listArtifacts(caseId.value),
      reportApi.list(),
    ])
    reportArtifacts.value = artifacts.filter((item) => item.kind === 'report')
    documents.value = docs.filter((item) => item.case_id === caseId.value)
    selected.value = activeReport.value
  } catch {
    error.value = '报告加载失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function createDraft() {
  if (!latestArtifact.value) return
  error.value = null
  try {
    const created = await reportApi.importFromArtifact(
      caseId.value,
      latestArtifact.value.id,
    )
    notice.value = '草稿已创建，可开始编辑。'
    await load()
    selected.value = created
  } catch {
    error.value = '创建草稿失败。'
  }
}

function startEdit() {
  if (!selected.value) return
  editTitle.value = selected.value.title
  editSummary.value = selected.value.content_json.executive_summary ?? ''
  editing.value = true
}

async function saveEdit() {
  if (!selected.value) return
  error.value = null
  try {
    const updated = await reportApi.update(selected.value.id, {
      expected_lock_version: selected.value.lock_version,
      title: editTitle.value,
      content: {
        ...selected.value.content_json,
        executive_summary: editSummary.value,
      },
    })
    editing.value = false
    selected.value = updated
    notice.value = '草稿已保存。'
    await load()
    selected.value = updated
  } catch (err) {
    const code = (err as { response?: { data?: { code?: string } } }).response?.data?.code
    error.value = code === 'report_version_conflict'
      ? '报告已被他人修改，请刷新最新版本后重试。'
      : '保存失败，请重试。'
  }
}

async function transition(status: 'in_review' | 'published' | 'archived') {
  if (!selected.value) return
  error.value = null
  try {
    const action =
      status === 'in_review'
        ? reportApi.submitReview(caseId.value, selected.value.id)
        : status === 'published'
          ? reportApi.publish(caseId.value, selected.value.id)
          : reportApi.archive(caseId.value, selected.value.id)
    const updated = await action
    selected.value = updated
    notice.value = `报告状态：${statusLabels[status]}。`
    await load()
  } catch (err) {
    const code = (err as { response?: { data?: { code?: string } } }).response?.data?.code
    error.value = code === 'report_publish_validation_failed'
      ? '发布校验失败：请检查标题、摘要/章节与引用是否有效。'
      : '状态变更失败。'
  }
}

async function revise() {
  if (!selected.value) return
  try {
    const revision = await reportApi.revise(caseId.value, selected.value.id)
    notice.value = '已创建新修订草稿。'
    await load()
    selected.value = revision
    editing.value = true
  } catch {
    error.value = '创建修订失败。'
  }
}

onMounted(load)
</script>

<template>
  <div class="irep">
    <header class="irep__head">
      <h2 class="irep__title">报告</h2>
      <div class="irep__actions">
        <button
          v-if="!activeReport && latestArtifact"
          type="button"
          class="irep__btn irep__btn--primary"
          @click="createDraft"
        >
          从 Agent 报告创建草稿
        </button>
        <a
          v-if="selected"
          class="irep__btn"
          :href="reportApi.downloadUrl(selected.id)"
        >
          导出 HTML
        </a>
      </div>
    </header>

    <p v-if="error" class="irep__error">{{ error }}</p>
    <p v-if="notice" class="irep__notice">{{ notice }}</p>
    <p v-if="loading" class="irep__hint">正在加载…</p>

    <template v-else>
      <p
        v-if="!latestArtifact"
        class="irep__hint"
      >
        尚无 Agent 报告 Artifact — 在 Copilot 中运行报告分析后即可创建草稿。
      </p>

      <section v-if="selected" class="irep__doc">
        <header class="irep__doc-head">
          <span class="irep__status" :data-status="selected.status">
            {{ statusLabels[selected.status] }}
          </span>
          <span class="irep__version">v{{ selected.lock_version }}</span>
        </header>

        <template v-if="editing">
          <label class="irep__field">
            <span>标题</span>
            <input v-model="editTitle" type="text" />
          </label>
          <label class="irep__field">
            <span>执行摘要</span>
            <textarea v-model="editSummary" rows="4" />
          </label>
          <div class="irep__actions">
            <button type="button" class="irep__btn irep__btn--primary" @click="saveEdit">
              保存（乐观锁 v{{ selected.lock_version }}）
            </button>
            <button type="button" class="irep__btn" @click="editing = false">取消</button>
          </div>
        </template>

        <template v-else>
          <h3 class="irep__doc-title">{{ selected.title }}</h3>
          <p class="irep__summary">{{ selected.content_json.executive_summary }}</p>

          <section
            v-for="(section, index) in selected.content_json.sections ?? []"
            :key="index"
            class="irep__section"
          >
            <h4>{{ section.title }}</h4>
            <p>{{ section.content }}</p>
          </section>

          <p class="irep__citations">
            引用：{{ (selected.content_json.citation_links ?? []).length }} 条
          </p>

          <div class="irep__actions">
            <button
              v-if="selected.status === 'draft'"
              type="button"
              class="irep__btn"
              @click="startEdit"
            >
              编辑
            </button>
            <button
              v-if="selected.status === 'draft'"
              type="button"
              class="irep__btn"
              @click="transition('in_review')"
            >
              提交审核
            </button>
            <button
              v-if="selected.status === 'draft' || selected.status === 'in_review'"
              type="button"
              class="irep__btn irep__btn--primary"
              @click="transition('published')"
            >
              发布
            </button>
            <button
              v-if="selected.status === 'published' || selected.status === 'archived'"
              type="button"
              class="irep__btn"
              @click="revise"
            >
              修改（创建新修订）
            </button>
            <button
              v-if="selected.status === 'published'"
              type="button"
              class="irep__btn"
              @click="transition('archived')"
            >
              归档
            </button>
          </div>
          <p class="irep__disclaimer">{{ selected.content_json.disclaimer }}</p>
        </template>
      </section>
    </template>
  </div>
</template>

<style scoped>
.irep {
  max-width: 880px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.irep__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.irep__title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}

.irep__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.irep__btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
}

.irep__btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.irep__btn--primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.irep__error {
  margin: 0;
  color: var(--red);
  font-size: 13px;
}

.irep__notice {
  margin: 0;
  color: var(--green);
  font-size: 13px;
}

.irep__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.irep__doc {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.irep__doc-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.irep__status {
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
}

.irep__status[data-status='published'] {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.irep__status[data-status='in_review'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.irep__version {
  font-size: 12px;
  color: var(--text-soft);
}

.irep__doc-title {
  margin: 0;
  font-size: 18px;
}

.irep__summary {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text);
}

.irep__section h4 {
  margin: 0 0 4px;
  font-size: 13px;
  color: var(--text-muted);
}

.irep__section p {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text);
}

.irep__citations {
  margin: 0;
  font-size: 12px;
  color: var(--text-soft);
}

.irep__disclaimer {
  margin: 0;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  color: var(--text-soft);
}

.irep__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-muted);
}

.irep__field input,
.irep__field textarea {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px;
  font-size: 13px;
  font-family: inherit;
}
</style>
