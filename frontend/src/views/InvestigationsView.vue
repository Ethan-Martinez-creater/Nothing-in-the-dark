<script setup lang="ts">
// Optimization V2 (M1.1)：调查列表页骨架。
// 侧栏由 GlobalSidebar/InvestigationList 承担（M1.2），本页提供全局列表视图。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { Plus } from 'lucide-vue-next'

import { api } from '@/services/api'
import type { CaseRecord } from '@/types/api'

const router = useRouter()
const cases = ref<CaseRecord[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

const sorted = computed(() =>
  [...cases.value].sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? '')),
)

async function load() {
  loading.value = true
  error.value = null
  try {
    cases.value = await api.listCases()
  } catch {
    error.value = '加载调查列表失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function open(caseId: string) {
  router.push(`/investigations/${caseId}/overview`)
}

onMounted(load)
</script>

<template>
  <div class="investigations-view">
    <header class="investigations-view__header">
      <div>
        <h1 class="investigations-view__title">调查</h1>
        <p class="investigations-view__subtitle">全部调查案例</p>
      </div>
      <button class="investigations-view__cta" @click="router.push('/')">
        <Plus :size="16" />
        新建调查
      </button>
    </header>

    <p v-if="error" class="investigations-view__error">{{ error }}</p>
    <p v-else-if="loading" class="investigations-view__hint">正在加载…</p>
    <p v-else-if="sorted.length === 0" class="investigations-view__hint">
      尚无调查 — 从首页「新建调查」开始
    </p>
    <ul v-else class="investigations-view__list">
      <li v-for="item in sorted" :key="item.id">
        <button class="investigations-view__card" @click="open(item.id)">
          <span class="investigations-view__card-topic">{{ item.topic }}</span>
          <span class="investigations-view__card-meta">
            {{ item.platforms.join(' · ') }}
          </span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.investigations-view {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px 24px 40px;
}

.investigations-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}

.investigations-view__title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
}

.investigations-view__subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}

.investigations-view__cta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 16px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.investigations-view__error {
  color: var(--red);
  font-size: 13px;
}

.investigations-view__hint {
  color: var(--text-muted);
  font-size: 13px;
}

.investigations-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.investigations-view__card {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  cursor: pointer;
}

.investigations-view__card:hover {
  border-color: var(--accent);
}

.investigations-view__card-topic {
  font-size: 14px;
  font-weight: 600;
}

.investigations-view__card-meta {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
