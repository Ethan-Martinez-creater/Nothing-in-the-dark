<script setup lang="ts">
// Optimization V2 (M3.9)：采集定义编辑器（创建/修订 draft）。
// 本轮只做结构化关键词 + exclusions，不做 Boolean 查询语言 builder。
import { computed, reactive } from 'vue'

import { Plus, X } from 'lucide-vue-next'

import type { CollectionDefinition } from '@/services/api/collections'

const props = defineProps<{
  casePlatforms: string[]
  source: CollectionDefinition | null
}>()

const emit = defineEmits<{
  (
    e: 'save',
    payload: {
      goal: string
      platforms: string[]
      platform_queries: Record<string, string[]>
      exclusions: string[]
    },
  ): void
  (e: 'cancel'): void
}>()

const form = reactive({
  goal: props.source?.goal ?? '',
  platforms: new Set<string>(
    props.source?.platforms ?? (props.casePlatforms[0] ? [props.casePlatforms[0]] : []),
  ),
  queries: Object.fromEntries(
    props.casePlatforms.map((platform) => [
      platform,
      [...(props.source?.platform_queries[platform] ?? [])],
    ]),
  ) as Record<string, string[]>,
  newQuery: Object.fromEntries(props.casePlatforms.map((p) => [p, ''])) as Record<string, string>,
  exclusions: [...(props.source?.exclusions ?? [])],
  newExclusion: '',
})

const goalError = computed(() =>
  form.goal.trim() ? '' : '调查目标不能为空',
)

function togglePlatform(platform: string) {
  if (form.platforms.has(platform)) form.platforms.delete(platform)
  else form.platforms.add(platform)
}

function addQuery(platform: string) {
  const value = (form.newQuery[platform] ?? '').trim()
  if (!value) return
  const list = form.queries[platform] ?? []
  if (!list.includes(value)) list.push(value)
  form.queries[platform] = list
  form.newQuery[platform] = ''
}

function removeQuery(platform: string, query: string) {
  form.queries[platform] = (form.queries[platform] ?? []).filter((item) => item !== query)
}

function addExclusion() {
  const value = form.newExclusion.trim()
  if (value && !form.exclusions.includes(value)) form.exclusions.push(value)
  form.newExclusion = ''
}

function submit() {
  if (goalError.value) return
  const platformQueries: Record<string, string[]> = {}
  for (const platform of form.platforms) {
    platformQueries[platform] = form.queries[platform] ?? []
  }
  emit('save', {
    goal: form.goal.trim(),
    platforms: [...form.platforms],
    platform_queries: platformQueries,
    exclusions: form.exclusions,
  })
}
</script>

<template>
  <div class="cedit">
    <h3 class="cedit__title">
      {{ source ? `基于 v${source.version} 创建新版本` : '创建采集定义（draft）' }}
    </h3>

    <label class="cedit__field">
      <span>调查目标</span>
      <textarea v-model="form.goal" rows="2" placeholder="本调查要回答的核心问题" />
      <span v-if="goalError" class="cedit__error">{{ goalError }}</span>
    </label>

    <fieldset class="cedit__field">
      <legend>平台</legend>
      <label
        v-for="platform in casePlatforms"
        :key="platform"
        class="cedit__platform"
      >
        <input
          type="checkbox"
          :checked="form.platforms.has(platform)"
          @change="togglePlatform(platform)"
        />
        {{ platform }}
      </label>
    </fieldset>

    <div
      v-for="platform in [...form.platforms]"
      :key="platform"
      class="cedit__queries"
    >
      <span class="cedit__queries-label">{{ platform }}</span>
      <div class="cedit__chips">
        <span v-for="query in form.queries[platform]" :key="query" class="cedit__chip">
          {{ query }}
          <button type="button" @click="removeQuery(platform, query)">
            <X :size="11" />
          </button>
        </span>
        <span class="cedit__add">
          <input
            v-model="form.newQuery[platform]"
            type="text"
            placeholder="添加关键词"
            @keydown.enter.prevent="addQuery(platform)"
          />
          <button type="button" @click="addQuery(platform)">
            <Plus :size="13" />
          </button>
        </span>
      </div>
    </div>

    <div class="cedit__queries">
      <span class="cedit__queries-label">排除词</span>
      <div class="cedit__chips">
        <span
          v-for="exclusion in form.exclusions"
          :key="exclusion"
          class="cedit__chip cedit__chip--excluded"
        >
          {{ exclusion }}
          <button
            type="button"
            @click="form.exclusions = form.exclusions.filter((item) => item !== exclusion)"
          >
            <X :size="11" />
          </button>
        </span>
        <span class="cedit__add">
          <input
            v-model="form.newExclusion"
            type="text"
            placeholder="添加排除词"
            @keydown.enter.prevent="addExclusion"
          />
          <button type="button" @click="addExclusion">
            <Plus :size="13" />
          </button>
        </span>
      </div>
    </div>

    <div class="cedit__actions">
      <button type="button" class="cedit__save" :disabled="!!goalError" @click="submit">
        保存为 draft
      </button>
      <button type="button" class="cedit__cancel" @click="emit('cancel')">取消</button>
    </div>
  </div>
</template>

<style scoped>
.cedit {
  border: 1px solid var(--accent);
  border-radius: 12px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--surface-muted);
}

.cedit__title {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
}

.cedit__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 0;
  padding: 0;
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}

.cedit__field textarea {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}

.cedit__field legend {
  padding: 0;
}

.cedit__platform {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-right: 12px;
  font-size: 13px;
  color: var(--text);
}

.cedit__error {
  color: var(--red);
  font-size: 11px;
}

.cedit__queries {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.cedit__queries-label {
  min-width: 72px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  padding-top: 4px;
}

.cedit__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.cedit__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--accent-strong);
  font-size: 12px;
}

.cedit__chip--excluded {
  background: rgba(239, 68, 68, 0.08);
  color: var(--red);
}

.cedit__chip button {
  display: grid;
  place-items: center;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 0;
}

.cedit__add input {
  width: 120px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 3px 8px;
  font-size: 12px;
}

.cedit__add button {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
}

.cedit__actions {
  display: flex;
  gap: 8px;
}

.cedit__save {
  padding: 7px 14px;
  border: 0;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.cedit__save:disabled {
  opacity: 0.5;
  cursor: default;
}

.cedit__cancel {
  padding: 7px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
}
</style>
