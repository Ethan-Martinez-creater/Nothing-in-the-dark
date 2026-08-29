<script setup lang="ts">
// Optimization V2 (M3.9)：采集定义卡片（Overview 的 Scope/Collection 区块）。
// ACTIVE 版本视觉明确；draft 不会被误认为当前采集规则。
import { computed, onMounted, ref } from 'vue'

import { History, Pencil, Sparkles } from 'lucide-vue-next'

import {
  collectionApi,
  type CollectionDefinition,
} from '@/services/api/collections'
import CollectionDefinitionEditor from '@/components/collection/CollectionDefinitionEditor.vue'
import CollectionVersionList from '@/components/collection/CollectionVersionList.vue'

const props = defineProps<{ caseId: string; casePlatforms: string[] }>()

const active = ref<CollectionDefinition | null>(null)
const versions = ref<CollectionDefinition[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const editorOpen = ref(false)
const historyOpen = ref(false)
const generating = ref(false)

const statusText = computed(() => {
  if (!active.value) return '尚未创建采集定义'
  return `ACTIVE · v${active.value.version}`
})

async function load() {
  loading.value = true
  error.value = null
  try {
    const [activeDefinition, list] = await Promise.all([
      collectionApi.getActive(props.caseId),
      collectionApi.list(props.caseId),
    ])
    active.value = activeDefinition
    versions.value = list
  } catch {
    error.value = '采集定义加载失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function generateSuggestion() {
  generating.value = true
  error.value = null
  try {
    const draft = await collectionApi.generate(props.caseId)
    notice.value = `已生成建议方案（draft v${draft.version}），确认后激活。`
    await load()
    editorOpen.value = true
  } catch {
    error.value = '生成建议失败，请稍后重试。'
  } finally {
    generating.value = false
  }
}

async function handleSave(payload: {
  goal: string
  platforms: string[]
  platform_queries: Record<string, string[]>
  exclusions: string[]
}) {
  error.value = null
  try {
    if (editorSource.value) {
      await collectionApi.revise(props.caseId, editorSource.value.id, payload)
    } else {
      await collectionApi.create(props.caseId, payload)
    }
    editorOpen.value = false
    editorSource.value = null
    await load()
  } catch (err) {
    const code = (err as { response?: { data?: { code?: string } } }).response?.data?.code
    error.value = code === 'collection_version_conflict'
      ? '版本冲突，请刷新后重试。'
      : '保存失败，请检查输入后重试。'
  }
}

async function handleActivate(definition: CollectionDefinition) {
  error.value = null
  try {
    await collectionApi.activate(props.caseId, definition.id)
    await load()
  } catch (err) {
    const code = (err as { response?: { data?: { code?: string } } }).response?.data?.code
    error.value = code === 'collection_not_draft'
      ? '只有 draft 版本可以激活。'
      : '激活失败，请重试。'
  }
}

const editorSource = ref<CollectionDefinition | null>(null)

function openEditor(source: CollectionDefinition | null) {
  editorSource.value = source
  editorOpen.value = true
}

onMounted(load)
</script>

<template>
  <section class="ccard" aria-label="采集定义">
    <header class="ccard__head">
      <div>
        <h2 class="ccard__title">采集范围</h2>
        <p class="ccard__status" :class="{ 'ccard__status--active': !!active }">
          {{ statusText }}
        </p>
      </div>
      <div class="ccard__actions">
        <button
          v-if="active"
          type="button"
          class="ccard__btn"
          @click="openEditor(active)"
        >
          <Pencil :size="14" />
          编辑新版本
        </button>
        <button
          v-else
          type="button"
          class="ccard__btn ccard__btn--primary"
          :disabled="generating"
          @click="generateSuggestion"
        >
          <Sparkles :size="14" />
          {{ generating ? '生成中…' : '生成建议采集方案' }}
        </button>
        <button
          v-if="versions.length > 0"
          type="button"
          class="ccard__btn"
          @click="historyOpen = !historyOpen"
        >
          <History :size="14" />
          历史版本（{{ versions.length }}）
        </button>
      </div>
    </header>

    <p v-if="error" class="ccard__error">{{ error }}</p>
    <p v-if="notice" class="ccard__notice">{{ notice }}</p>

    <p v-if="loading" class="ccard__hint">正在加载…</p>

    <template v-else>
      <div v-if="active" class="ccard__body">
        <p class="ccard__goal">{{ active.goal }}</p>
        <div
          v-for="platform in active.platforms"
          :key="platform"
          class="ccard__platform"
        >
          <span class="ccard__platform-name">{{ platform }}</span>
          <span class="ccard__keywords">
            <span
              v-for="query in active.platform_queries[platform] ?? []"
              :key="query"
              class="ccard__chip"
            >
              + {{ query }}
            </span>
            <span v-if="!(active.platform_queries[platform] ?? []).length" class="ccard__chip">
              + {{ active.goal }}（回退主题）
            </span>
          </span>
        </div>
        <div v-if="active.exclusions.length" class="ccard__exclusions">
          <span
            v-for="exclusion in active.exclusions"
            :key="exclusion"
            class="ccard__chip ccard__chip--excluded"
          >
            - {{ exclusion }}
          </span>
        </div>
      </div>
      <p v-else class="ccard__hint">
        尚无采集定义 — 采集将按主题自动生成检索词；创建定义后可精确控制各平台关键词。
      </p>

      <CollectionDefinitionEditor
        v-if="editorOpen"
        :case-platforms="casePlatforms"
        :source="editorSource"
        @save="handleSave"
        @cancel="editorOpen = false"
      />

      <CollectionVersionList
        v-if="historyOpen"
        :versions="versions"
        @activate="handleActivate"
      />
    </template>
  </section>
</template>

<style scoped>
.ccard {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ccard__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.ccard__title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}

.ccard__status {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}

.ccard__status--active {
  color: var(--green);
  font-weight: 700;
}

.ccard__actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.ccard__btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.ccard__btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.ccard__btn--primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.ccard__goal {
  margin: 0;
  font-size: 14px;
  color: var(--text);
}

.ccard__platform {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 6px;
}

.ccard__platform-name {
  min-width: 72px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}

.ccard__keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ccard__chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--accent-strong);
  font-size: 12px;
}

.ccard__chip--excluded {
  background: rgba(239, 68, 68, 0.08);
  color: var(--red);
}

.ccard__exclusions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.ccard__error {
  margin: 0;
  font-size: 12px;
  color: var(--red);
}

.ccard__notice {
  margin: 0;
  font-size: 12px;
  color: var(--green);
}

.ccard__hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}
</style>
