<script setup lang="ts">
// Optimization V2 (M1.4)：Operational Home v1。
// 首页回答：有哪些调查、有什么需要我处理。产品介绍压缩到次要 About 区。
// M6 接入 Signals 后由 /workspace/overview 聚合端点替换逐项请求。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { Plus, Search } from 'lucide-vue-next'

import { api } from '@/services/api'
import type { CaseRecord, SystemCapabilities } from '@/types/api'

const router = useRouter()

const cases = ref<CaseRecord[]>([])
const capabilities = ref<SystemCapabilities | null>(null)
const pendingApprovalCount = ref(0)
const loading = ref(true)
const error = ref<string | null>(null)
const keyword = ref('')
const creating = ref(false)

const demoMode = computed(() => capabilities.value?.demo_mode !== false)
const llmConfigured = computed(
  () => capabilities.value?.llm_configured ?? capabilities.value?.llm?.configured ?? true,
)

const recentCases = computed(() => {
  const filtered = keyword.value.trim()
    ? cases.value.filter((item) =>
        item.topic.toLowerCase().includes(keyword.value.trim().toLowerCase()),
      )
    : cases.value
  return filtered.slice(0, 8)
})

async function loadData() {
  loading.value = true
  error.value = null
  try {
    const [caseList, capabilitiesData, approvals] = await Promise.all([
      api.listCases(),
      api.getCapabilities().catch(() => null),
      api
        .listApprovals({ status: 'pending' })
        .catch(() => [] as Array<Record<string, unknown>>),
    ])
    cases.value = caseList
    capabilities.value = capabilitiesData
    pendingApprovalCount.value = approvals.length
  } catch {
    error.value = '加载工作台数据失败，请检查后端服务后重试'
  } finally {
    loading.value = false
  }
}

function openInvestigation(caseId: string) {
  router.push(`/investigations/${caseId}/overview`)
}

function openApprovals() {
  router.push('/admin/approvals')
}

async function createInvestigation() {
  creating.value = true
  try {
    const created = await api.createCase({
      topic: `新调查 ${new Date().toLocaleString()}`,
      platforms: ['weibo'],
      description: '',
    })
    router.push(`/investigations/${created.id}/overview`)
  } catch {
    error.value = '创建调查失败，请稍后重试'
  } finally {
    creating.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="home-view">
    <header class="home-view__header">
      <div>
        <h1 class="home-view__title">工作台</h1>
        <p class="home-view__subtitle">
          调查、信号与报告的统一入口 ·
          <span v-if="demoMode" class="home-view__badge">DEMO MODE</span>
          <span v-else class="home-view__badge home-view__badge--real">REAL CRAWL</span>
          <span v-if="!llmConfigured" class="home-view__badge home-view__badge--warn">
            LLM 未配置
          </span>
        </p>
      </div>
      <button
        class="home-view__cta"
        :disabled="creating"
        @click="createInvestigation"
      >
        <Plus :size="16" />
        {{ creating ? '创建中…' : '新建调查' }}
      </button>
    </header>

    <section class="home-view__kpis" aria-label="待处理事项">
      <button class="home-view__kpi" @click="openApprovals">
        <span class="home-view__kpi-value">{{ pendingApprovalCount }}</span>
        <span class="home-view__kpi-label">待审批</span>
      </button>
      <div class="home-view__kpi">
        <span class="home-view__kpi-value">{{ cases.length }}</span>
        <span class="home-view__kpi-label">调查总数</span>
      </div>
    </section>

    <section class="home-view__recent" aria-label="最近调查">
      <div class="home-view__section-head">
        <h2>最近调查</h2>
        <label class="home-view__search">
          <Search :size="14" />
          <input v-model="keyword" type="search" placeholder="搜索调查" />
        </label>
      </div>

      <p v-if="error" class="home-view__error">{{ error }}</p>
      <p v-else-if="loading" class="home-view__hint">正在加载…</p>
      <p v-else-if="recentCases.length === 0" class="home-view__hint">
        尚无调查 — 点击右上角「新建调查」开始
      </p>
      <ul v-else class="home-view__list">
        <li v-for="item in recentCases" :key="item.id">
          <button class="home-view__item" @click="openInvestigation(item.id)">
            <span class="home-view__item-topic">{{ item.topic }}</span>
            <span class="home-view__item-meta">
              {{ item.platforms.join(' · ') }}
            </span>
          </button>
        </li>
      </ul>
    </section>

    <details class="home-view__about">
      <summary>About this workspace</summary>
      <p>
        COIFESP 是一个以调查为中心、以证据为事实底座的社交与叙事情报工作台。
        Agent 负责认知与分析，人类 Review 负责最终判断；所有结论都可回溯到证据与运行轨迹。
      </p>
    </details>
  </div>
</template>

<style scoped>
.home-view {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.home-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.home-view__title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
}

.home-view__subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
}

.home-view__badge {
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.1);
  color: var(--accent-strong);
  font-size: 11px;
  font-weight: 600;
}

.home-view__badge--real {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.home-view__badge--warn {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.home-view__cta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 16px;
  border: none;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.home-view__cta:disabled {
  opacity: 0.6;
  cursor: default;
}

.home-view__kpis {
  display: flex;
  gap: 12px;
}

.home-view__kpi {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 14px 18px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  text-align: left;
}

button.home-view__kpi {
  cursor: pointer;
}

button.home-view__kpi:hover {
  border-color: var(--accent);
}

.home-view__kpi-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--text);
}

.home-view__kpi-label {
  font-size: 13px;
  color: var(--text-muted);
}

.home-view__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.home-view__section-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.home-view__search {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-soft);
}

.home-view__search input {
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  color: var(--text);
  min-width: 160px;
}

.home-view__error {
  color: var(--red);
  font-size: 13px;
}

.home-view__hint {
  color: var(--text-muted);
  font-size: 13px;
}

.home-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.home-view__item {
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
  transition: border-color 0.15s ease;
}

.home-view__item:hover {
  border-color: var(--accent);
}

.home-view__item-topic {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.home-view__item-meta {
  font-size: 12px;
  color: var(--text-muted);
}

.home-view__about {
  border-top: 1px solid var(--border);
  padding-top: 12px;
  font-size: 13px;
  color: var(--text-muted);
}

.home-view__about summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--text-soft);
}

.home-view__about p {
  margin: 8px 0 0;
  line-height: 1.6;
}
</style>
