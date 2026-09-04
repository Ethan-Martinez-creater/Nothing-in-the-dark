<script setup lang="ts">
// V3 §45：全局情报工作台（Global Intelligence Workspace）。
// Connections：所有调查的跨调查关联（observed 实线 / candidate 虚线，
// 图组件负责线型；本视图提供 filter + 列表 + 详情三栏）。
// Entities：Workspace 级实体（身份组件聚合），点选加载 profile。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import IntelligenceConnectionsGraph from '@/components/intelligence/IntelligenceConnectionsGraph.vue'
import {
  crossApi,
  entityApi,
  type IntelligenceConnection,
  type WorkspaceEntityProfile,
  type WorkspaceEntitySummary,
} from '@/services/api/intelligence'

const router = useRouter()

type TabKey = 'connections' | 'entities'
const activeTab = ref<TabKey>('connections')

// ---- Connections tab ----
const connections = ref<IntelligenceConnection[]>([])
const connectionsLoading = ref(true)
const connectionsError = ref('')
const statusFilter = ref('')
const relationFilter = ref('')

const caseTitles = computed(() => {
  const titles: Record<string, string> = {}
  for (const link of connections.value) {
    if (link.left_title) titles[link.left_case_id] = link.left_title
    if (link.right_title) titles[link.right_case_id] = link.right_title
  }
  return titles
})

const filteredConnections = computed(() =>
  connections.value.filter(
    (link) =>
      (!statusFilter.value || link.status === statusFilter.value) &&
      (!relationFilter.value || link.relation_type === relationFilter.value),
  ),
)

const selectedLinkId = ref<string | null>(null)
const selectedLink = computed(
  () => connections.value.find((link) => link.id === selectedLinkId.value) ?? null,
)

const RELATION_LABELS: Record<string, string> = {
  shared_actor: '共享账号',
  shared_post: '共享帖子',
  shared_media: '共享媒体',
  shared_content: '共享内容',
}
const STATUS_LABELS: Record<string, string> = {
  observed: '已确认',
  candidate: '候选',
}

async function loadConnections() {
  connectionsLoading.value = true
  connectionsError.value = ''
  try {
    connections.value = await crossApi.connections({
      status: statusFilter.value || undefined,
      relation_type: relationFilter.value || undefined,
      limit: 200,
    })
  } catch {
    connectionsError.value = '加载跨调查关联失败，请检查后端服务后重试。'
  } finally {
    connectionsLoading.value = false
  }
}

function onGraphSelect(target: string) {
  const caseId = target.startsWith('case:') ? target.slice(5) : null
  if (caseId) {
    router.push(`/investigations/${caseId}/overview`)
    return
  }
  selectedLinkId.value = target
}

function openCase(caseId: string) {
  router.push(`/investigations/${caseId}/overview`)
}

// ---- Entities tab ----
const entities = ref<WorkspaceEntitySummary[]>([])
const entitiesTotal = ref(0)
const entitiesLoading = ref(true)
const entitiesError = ref('')
const entityQuery = ref('')
const platformFilter = ref('')
const minInvestigationFilter = ref(0)

const PLATFORM_OPTIONS = [
  { id: 'weibo', label: '微博' },
  { id: 'douyin', label: '抖音' },
  { id: 'bilibili', label: '哔哩哔哩' },
  { id: 'zhihu', label: '知乎' },
  { id: 'tieba', label: '百度贴吧' },
]

async function loadEntities() {
  entitiesLoading.value = true
  entitiesError.value = ''
  try {
    const page = await entityApi.list({
      query: entityQuery.value.trim() || undefined,
      platform: platformFilter.value || undefined,
      min_investigations: minInvestigationFilter.value || undefined,
      limit: 50,
    })
    entities.value = page.items
    entitiesTotal.value = page.total
  } catch {
    entitiesError.value = '加载实体失败，请检查后端服务后重试。'
  } finally {
    entitiesLoading.value = false
  }
}

const selectedEntityId = ref<string | null>(null)
const selectedEntity = ref<WorkspaceEntityProfile | null>(null)
const profileLoading = ref(false)
const profileError = ref('')

async function selectEntity(entityId: string) {
  selectedEntityId.value = entityId
  selectedEntity.value = null
  profileLoading.value = true
  profileError.value = ''
  try {
    selectedEntity.value = await entityApi.profile(entityId)
  } catch {
    profileError.value = '加载实体详情失败。'
  } finally {
    profileLoading.value = false
  }
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('zh-CN')
}

function riskLabel(risk: Record<string, unknown>): string {
  const band = risk.band ?? risk.risk_level ?? risk.severity ?? ''
  return String(band)
}

function switchTab(tab: TabKey) {
  activeTab.value = tab
  if (tab === 'entities') void loadEntities()
}

function resetConnectionsFilters() {
  statusFilter.value = ''
  relationFilter.value = ''
  void loadConnections()
}

onMounted(() => {
  void loadConnections()
})
</script>

<template>
  <div class="intelview">
    <header class="intelview__header">
      <div>
        <h1 class="intelview__title">情报</h1>
        <p class="intelview__subtitle">跨调查关联与 Workspace 实体的集中视图</p>
      </div>
    </header>

    <div class="intelview__tabs" role="tablist" aria-label="情报视图">
      <button
        type="button"
        class="intelview__tab"
        :class="{ 'intelview__tab--active': activeTab === 'connections' }"
        role="tab"
        :aria-selected="activeTab === 'connections'"
        @click="switchTab('connections')"
      >
        关联
      </button>
      <button
        type="button"
        class="intelview__tab"
        :class="{ 'intelview__tab--active': activeTab === 'entities' }"
        role="tab"
        :aria-selected="activeTab === 'entities'"
        @click="switchTab('entities')"
      >
        实体
      </button>
    </div>

    <!-- Connections -->
    <section v-if="activeTab === 'connections'" class="intelview__section" aria-label="跨调查关联">
      <div class="intelview__filters">
        <select v-model="statusFilter" class="intelview__filter" @change="loadConnections">
          <option value="">全部状态</option>
          <option value="observed">已确认（observed）</option>
          <option value="candidate">候选（candidate）</option>
        </select>
        <select v-model="relationFilter" class="intelview__filter" @change="loadConnections">
          <option value="">全部类型</option>
          <option v-for="(label, key) in RELATION_LABELS" :key="key" :value="key">
            {{ label }}
          </option>
        </select>
        <button type="button" class="intelview__ghost" @click="resetConnectionsFilters">
          重置
        </button>
      </div>

      <p v-if="connectionsError" class="intelview__error">{{ connectionsError }}</p>

      <div class="intelview__workspace">
        <ul class="intelview__list" aria-label="关联列表">
          <li v-if="connectionsLoading" class="intelview__hint">正在加载…</li>
          <li v-else-if="filteredConnections.length === 0" class="intelview__hint">
            无匹配的跨调查关联。
          </li>
          <li v-for="link in filteredConnections" :key="link.id">
            <button
              type="button"
              class="intelview__item"
              :class="{ 'intelview__item--active': selectedLinkId === link.id }"
              @click="selectedLinkId = link.id"
            >
              <span class="intelview__item-status" :data-status="link.status">
                {{ STATUS_LABELS[link.status] ?? link.status }}
              </span>
              <span class="intelview__item-body">
                <span class="intelview__item-title">
                  {{ link.left_title ?? link.left_case_id }}
                  <em>↔</em>
                  {{ link.right_title ?? link.right_case_id }}
                </span>
                <span class="intelview__item-meta">
                  {{ RELATION_LABELS[link.relation_type] ?? link.relation_type }} ·
                  {{ link.evidence_count }} 条证据
                  <template v-if="link.score != null">· score {{ link.score.toFixed(2) }}</template>
                </span>
              </span>
            </button>
          </li>
        </ul>

        <div class="intelview__graph" aria-label="关联图">
          <IntelligenceConnectionsGraph
            :connections="filteredConnections"
            :case-titles="caseTitles"
            :loading="connectionsLoading"
            @select="onGraphSelect"
          />
        </div>

        <aside class="intelview__detail" aria-label="关联详情">
          <p v-if="!selectedLink" class="intelview__hint">从列表或图中选择一条关联。</p>
          <template v-else>
            <h3 class="intelview__detail-title">关联详情</h3>
            <dl class="intelview__dl">
              <dt>类型</dt>
              <dd>{{ RELATION_LABELS[selectedLink.relation_type] ?? selectedLink.relation_type }}</dd>
              <dt>状态</dt>
              <dd>{{ STATUS_LABELS[selectedLink.status] ?? selectedLink.status }}</dd>
              <dt>置信度</dt>
              <dd>{{ selectedLink.score != null ? selectedLink.score.toFixed(2) : '—' }}</dd>
              <dt>证据数</dt>
              <dd>{{ selectedLink.evidence_count }}</dd>
              <dt>算法版本</dt>
              <dd>{{ selectedLink.algorithm_version }}</dd>
              <dt>调查 A</dt>
              <dd>
                <button type="button" class="intelview__link" @click="openCase(selectedLink.left_case_id)">
                  {{ selectedLink.left_title ?? selectedLink.left_case_id }}
                </button>
              </dd>
              <dt>调查 B</dt>
              <dd>
                <button type="button" class="intelview__link" @click="openCase(selectedLink.right_case_id)">
                  {{ selectedLink.right_title ?? selectedLink.right_case_id }}
                </button>
              </dd>
            </dl>
          </template>
        </aside>
      </div>
    </section>

    <!-- Entities -->
    <section v-else class="intelview__section" aria-label="Workspace 实体">
      <div class="intelview__filters">
        <input
          v-model="entityQuery"
          type="search"
          class="intelview__search"
          placeholder="搜索实体名称"
          @keyup.enter="loadEntities"
        />
        <select v-model="platformFilter" class="intelview__filter" @change="loadEntities">
          <option value="">全部平台</option>
          <option v-for="platform in PLATFORM_OPTIONS" :key="platform.id" :value="platform.id">
            {{ platform.label }}
          </option>
        </select>
        <select v-model="minInvestigationFilter" class="intelview__filter" @change="loadEntities">
          <option :value="0">出现在任意调查</option>
          <option :value="2">出现 ≥ 2 个调查</option>
          <option :value="3">出现 ≥ 3 个调查</option>
        </select>
        <button type="button" class="intelview__ghost" @click="loadEntities">查询</button>
      </div>

      <p v-if="entitiesError" class="intelview__error">{{ entitiesError }}</p>

      <div class="intelview__workspace">
        <ul class="intelview__list" aria-label="实体列表">
          <li v-if="entitiesLoading" class="intelview__hint">正在加载…</li>
          <li v-else-if="entities.length === 0" class="intelview__hint">
            暂无实体 — 调查中出现账号后在此聚合。
          </li>
          <li v-for="item in entities" :key="item.entity_id">
            <button
              type="button"
              class="intelview__item"
              :class="{ 'intelview__item--active': selectedEntityId === item.entity_id }"
              @click="selectEntity(item.entity_id)"
            >
              <span class="intelview__item-body">
                <span class="intelview__item-title">{{ item.canonical_name }}</span>
                <span class="intelview__item-meta">
                  {{ item.platforms.join(' · ') }} · {{ item.investigation_count }} 个调查 ·
                  {{ item.post_count }} 帖 / {{ item.comment_count }} 评
                </span>
                <span v-if="item.risk_summary" class="intelview__item-risk">{{ item.risk_summary }}</span>
              </span>
            </button>
          </li>
          <li v-if="entities.length && !entitiesLoading" class="intelview__hint">
            共 {{ entitiesTotal }} 个实体（显示前 {{ entities.length }}）
          </li>
        </ul>

        <div class="intelview__entity-detail" aria-label="实体详情" aria-live="polite">
          <p v-if="profileLoading" class="intelview__hint">正在加载详情…</p>
          <p v-else-if="profileError" class="intelview__error">{{ profileError }}</p>
          <p v-else-if="!selectedEntity" class="intelview__hint">选择左侧实体查看详情。</p>
          <template v-else>
            <h3 class="intelview__detail-title">{{ selectedEntity.canonical_name }}</h3>
            <p class="intelview__detail-sub">
              {{ selectedEntity.platform_identities.map((p) => `${p.platform}:${p.native_id}`).join(' · ') }}
            </p>

            <dl class="intelview__dl">
              <dt>出现调查</dt>
              <dd>{{ selectedEntity.investigation_count }}</dd>
              <dt>内容量</dt>
              <dd>{{ selectedEntity.post_count }} 帖 / {{ selectedEntity.comment_count }} 评</dd>
              <dt>互动总量</dt>
              <dd>{{ selectedEntity.engagement_total }}</dd>
              <dt>首次/最近出现</dt>
              <dd>{{ formatTime(selectedEntity.first_seen_at) }} → {{ formatTime(selectedEntity.last_seen_at) }}</dd>
              <dt>别名</dt>
              <dd>{{ selectedEntity.aliases.length ? selectedEntity.aliases.join('、') : '—' }}</dd>
            </dl>

            <div v-if="selectedEntity.investigations.length" class="intelview__detail-block">
              <h4>相关调查</h4>
              <button
                v-for="caseId in selectedEntity.investigations"
                :key="caseId"
                type="button"
                class="intelview__link"
                @click="openCase(caseId)"
              >
                {{ caseId }}
              </button>
            </div>

            <div
              v-if="selectedEntity.risk_assessments.length || selectedEntity.unresolved_local_risk.length"
              class="intelview__detail-block"
            >
              <h4>风险标注</h4>
              <ul class="intelview__risk-list">
                <li v-for="(risk, index) in selectedEntity.risk_assessments" :key="index">
                  {{ riskLabel(risk) }}<span v-if="risk.score != null"> · score {{ Number(risk.score).toFixed(2) }}</span>
                </li>
                <li v-for="(risk, index) in selectedEntity.unresolved_local_risk" :key="`u-${index}`">
                  未解决 · {{ riskLabel(risk) }}
                </li>
              </ul>
            </div>
          </template>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.intelview {
  max-width: 1180px;
  margin: 0 auto;
  padding: 24px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.intelview__title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
}

.intelview__subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--text-muted);
}

.intelview__tabs {
  display: flex;
  gap: 6px;
  border-bottom: 1px solid var(--border);
}

.intelview__tab {
  padding: 8px 18px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}

.intelview__tab--active {
  color: var(--accent-strong);
  border-bottom-color: var(--accent);
}

.intelview__filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.intelview__filter,
.intelview__search {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 7px 10px;
  font-size: 13px;
  color: var(--text);
  font-family: inherit;
}

.intelview__search {
  min-width: 220px;
}

.intelview__ghost {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text);
}

.intelview__ghost:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.intelview__error {
  color: var(--red);
  font-size: 13px;
  margin: 0;
}

.intelview__workspace {
  display: grid;
  grid-template-columns: 300px 1fr 260px;
  gap: 16px;
  align-items: start;
}

.intelview__entity-detail {
  grid-column: 2 / -1;
}

.intelview__list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 560px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  padding: 10px;
}

.intelview__item {
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-muted);
  cursor: pointer;
}

.intelview__item:hover,
.intelview__item--active {
  border-color: var(--accent);
}

.intelview__item--active {
  background: rgba(37, 99, 235, 0.08);
}

.intelview__item-status {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
}

.intelview__item-status[data-status='observed'] {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.intelview__item-status[data-status='candidate'] {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.intelview__item-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.intelview__item-title {
  font-size: 13px;
  color: var(--text);
  font-weight: 500;
}

.intelview__item-title em {
  font-style: normal;
  color: var(--text-soft);
}

.intelview__item-meta {
  font-size: 11px;
  color: var(--text-muted);
}

.intelview__item-risk {
  font-size: 11px;
  color: #b45309;
}

.intelview__graph {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  padding: 10px;
  min-height: 400px;
}

.intelview__detail,
.intelview__entity-detail {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.intelview__detail-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.intelview__detail-sub {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}

.intelview__dl {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 6px 10px;
  margin: 0;
  font-size: 13px;
}

.intelview__dl dt {
  color: var(--text-muted);
}

.intelview__dl dd {
  margin: 0;
  color: var(--text);
  overflow-wrap: anywhere;
}

.intelview__link {
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 13px;
  cursor: pointer;
  padding: 0;
  text-align: left;
}

.intelview__detail-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}

.intelview__detail-block h4 {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-soft);
}

.intelview__risk-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #b45309;
}

.intelview__hint {
  margin: 0;
  padding: 12px 0;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 1080px) {
  .intelview__workspace {
    grid-template-columns: 1fr;
  }
}
</style>