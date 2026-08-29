<script setup lang="ts">
// Optimization V2 (M6.6)：Operational Home v2。
// 聚合端点 /workspace/overview 提供全部数据（禁止 N+1）。
// 结构：KPI 行 → Open/Critical Signals → Active/Recent Investigations → Recent Reports。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { Plus, Search } from 'lucide-vue-next'

import { api } from '@/services/api'
import {
  workspaceApi,
  type WorkspaceOverview,
} from '@/services/api/signals'
import type { SystemCapabilities } from '@/types/api'

const router = useRouter()

const overview = ref<WorkspaceOverview | null>(null)
const capabilities = ref<SystemCapabilities | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const keyword = ref('')
const creating = ref(false)

const demoMode = computed(() => capabilities.value?.demo_mode !== false)
const llmConfigured = computed(
  () => capabilities.value?.llm_configured ?? capabilities.value?.llm?.configured ?? true,
)

const recentCases = computed(() => {
  const items = overview.value?.recent_investigations ?? []
  const q = keyword.value.trim().toLowerCase()
  return q ? items.filter((item) => item.topic.toLowerCase().includes(q)) : items
})

const severityLabels: Record<string, string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const [data, caps] = await Promise.all([
      workspaceApi.overview(),
      api.getCapabilities().catch(() => null),
    ])
    overview.value = data
    capabilities.value = caps
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

function openSignals() {
  router.push('/signals')
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

onMounted(load)
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
      <button class="home-view__cta" :disabled="creating" @click="createInvestigation">
        <Plus :size="16" />
        {{ creating ? '创建中…' : '新建调查' }}
      </button>
    </header>

    <p v-if="error" class="home-view__error">{{ error }}</p>
    <p v-else-if="loading" class="home-view__hint">正在加载…</p>

    <template v-else-if="overview">
      <!-- KPI 行 -->
      <section class="home-view__kpis" aria-label="运营概览">
        <button class="home-view__kpi home-view__kpi--link" @click="openSignals">
          <span class="home-view__kpi-value">{{ overview.counts.open_signals }}</span>
          <span class="home-view__kpi-label">Open Signals</span>
        </button>
        <div class="home-view__kpi">
          <span class="home-view__kpi-value">{{ overview.counts.investigations }}</span>
          <span class="home-view__kpi-label">调查总数</span>
        </div>
        <button class="home-view__kpi home-view__kpi--link" @click="openApprovals">
          <span class="home-view__kpi-value">{{ overview.counts.pending_approvals }}</span>
          <span class="home-view__kpi-label">待审批</span>
        </button>
        <div class="home-view__kpi">
          <span class="home-view__kpi-value">{{ overview.counts.running_runs }}</span>
          <span class="home-view__kpi-label">运行中的 Agent</span>
        </div>
      </section>

      <div class="home-view__columns">
        <!-- Open/Critical Signals -->
        <section class="home-view__panel" aria-label="关键信号">
          <div class="home-view__panel-head">
            <h2>关键信号</h2>
            <button type="button" class="home-view__more" @click="openSignals">
              全部信号
            </button>
          </div>
          <p v-if="overview.top_signals.length === 0" class="home-view__hint">
            尚无信号 — 创建调查后配置持续监测。
          </p>
          <ul v-else class="home-view__signal-list">
            <li v-for="signal in overview.top_signals" :key="signal.id">
              <button type="button" class="home-view__signal" @click="openSignals">
                <span class="home-view__severity" :data-severity="signal.severity">
                  {{ severityLabels[signal.severity] ?? signal.severity }}
                </span>
                <span class="home-view__signal-body">
                  <span class="home-view__signal-title">{{ signal.title }}</span>
                  <span class="home-view__signal-meta">{{ signal.case_title }}</span>
                </span>
              </button>
            </li>
          </ul>
        </section>

        <!-- Active/Recent Investigations -->
        <section class="home-view__panel" aria-label="最近调查">
          <div class="home-view__panel-head">
            <h2>最近调查</h2>
            <label class="home-view__search">
              <Search :size="14" />
              <input v-model="keyword" type="search" placeholder="搜索调查" />
            </label>
          </div>
          <p v-if="recentCases.length === 0" class="home-view__hint">
            尚无调查 — 点击右上角「新建调查」开始
          </p>
          <ul v-else class="home-view__case-list">
            <li v-for="item in recentCases" :key="item.id">
              <button
                type="button"
                class="home-view__case"
                @click="openInvestigation(item.id)"
              >
                <span class="home-view__case-topic">{{ item.topic }}</span>
                <span class="home-view__case-meta">{{ item.platforms.join(' · ') }}</span>
              </button>
            </li>
          </ul>
        </section>
      </div>

      <!-- Recent Reports -->
      <section v-if="overview.recent_reports.length" class="home-view__panel" aria-label="最近报告">
        <div class="home-view__panel-head">
          <h2>最近报告</h2>
          <button type="button" class="home-view__more" @click="router.push('/reports')">
            报告中心
          </button>
        </div>
        <ul class="home-view__report-list">
          <li v-for="report in overview.recent_reports" :key="report.artifact_id">
            <button
              type="button"
              class="home-view__case"
              @click="openInvestigation(report.case_id)"
            >
              <span class="home-view__case-topic">{{ report.title }}</span>
              <span class="home-view__case-meta">
                {{ new Date(report.created_at).toLocaleDateString('zh-CN') }}
              </span>
            </button>
          </li>
        </ul>
      </section>
    </template>

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

.home-view__error {
  color: var(--red);
  font-size: 13px;
}

.home-view__hint {
  color: var(--text-muted);
  font-size: 13px;
}

.home-view__kpis {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
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

.home-view__columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}

.home-view__panel {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.home-view__panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.home-view__panel-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.home-view__more {
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
}

.home-view__search {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-soft);
}

.home-view__search input {
  border: none;
  outline: none;
  background: transparent;
  font-size: 12px;
  color: var(--text);
  min-width: 120px;
}

.home-view__signal-list,
.home-view__case-list,
.home-view__report-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.home-view__signal,
.home-view__case {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-muted);
  cursor: pointer;
}

.home-view__signal:hover,
.home-view__case:hover {
  border-color: var(--accent);
}

.home-view__severity {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
}

.home-view__severity[data-severity='critical'] {
  background: rgba(239, 68, 68, 0.12);
  color: var(--red);
}

.home-view__severity[data-severity='warning'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.home-view__signal-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.home-view__signal-title {
  font-size: 13px;
  color: var(--text);
}

.home-view__signal-meta,
.home-view__case-meta {
  font-size: 11px;
  color: var(--text-muted);
}

.home-view__case-topic {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.home-view__case {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
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
