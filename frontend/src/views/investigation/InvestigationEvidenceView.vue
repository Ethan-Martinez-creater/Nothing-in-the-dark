<script setup lang="ts">
// Optimization V2 (M4.8 + C8.1 + C9.1)：Evidence 全尺寸工作区。
// 子 tab：Claims（左 claim 列表 + 右详情）/ Semantics（语义标注面板）。
// selection 进入 Copilot context（workspace=evidence）。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import EvidenceClaimList from '@/components/evidence/EvidenceClaimList.vue'
import EvidenceDetailPanel from '@/components/evidence/EvidenceDetailPanel.vue'
import SemanticAnnotationsPanel from '@/components/semantics/SemanticAnnotationsPanel.vue'
import { api } from '@/services/api'
import type { ClaimEvidence, EvidenceItem, EvidenceSummary } from '@/types/api'
import { useInvestigationContext } from '@/composables/useInvestigationContext'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const summary = ref<EvidenceSummary | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

// C9.1: workspace 子 tab —— Claims 为证据主工作流，Semantics 为 M5.7 迁入
type EvidenceTab = 'claims' | 'semantics'
const workspaceTab = ref<EvidenceTab>('claims')

type ClaimFilter = 'all' | 'pending' | 'verified' | 'rejected'
const filter = ref<ClaimFilter>('all')
const filterLabels: Record<ClaimFilter, string> = {
  all: '全部',
  pending: '待核查',
  verified: '已核实',
  rejected: '已剔除',
}

const selectedClaim = ref<ClaimEvidence | null>(null)
const selectedItem = ref<EvidenceItem | null>(null)

const { setUiContext } = useInvestigationContext()

const claimCount = computed(() => summary.value?.claims.length ?? 0)
const unassignedCount = computed(() => summary.value?.unassigned.length ?? 0)

const filteredClaims = computed(() => {
  const claims = summary.value?.claims ?? []
  if (filter.value === 'all') return claims
  return claims.filter((claim) => claim.status === filter.value)
})

async function load() {
  loading.value = true
  error.value = null
  try {
    summary.value = await api.getEvidenceSummary(caseId.value)
  } catch {
    error.value = '证据加载失败，请先运行分析采集数据。'
  } finally {
    loading.value = false
  }
}

function onSelectClaim(claim: ClaimEvidence) {
  selectedClaim.value = claim
  selectedItem.value = null
  setUiContext({
    workspace: 'evidence',
    selected_type: 'claim',
    selected_id: claim.id,
  })
}

function onSelectEvidence(payload: { claim: ClaimEvidence; item: EvidenceItem }) {
  selectedClaim.value = payload.claim
  selectedItem.value = payload.item
  setUiContext({
    workspace: 'evidence',
    selected_type: 'evidence',
    selected_id: payload.item.id,
  })
}

watch(caseId, () => {
  selectedClaim.value = null
  selectedItem.value = null
  void load()
})

onMounted(load)
</script>

<template>
  <div class="iev">
    <div class="iev__tabs">
      <button
        type="button"
        class="iev__tab"
        :class="{ 'iev__tab--active': workspaceTab === 'claims' }"
        @click="workspaceTab = 'claims'"
      >
        Claims
      </button>
      <button
        type="button"
        class="iev__tab"
        :class="{ 'iev__tab--active': workspaceTab === 'semantics' }"
        @click="workspaceTab = 'semantics'"
      >
        Semantics
      </button>
    </div>

    <template v-if="workspaceTab === 'claims'">
      <p v-if="error" class="iev__error">{{ error }}</p>
      <p v-else-if="loading" class="iev__hint">正在加载…</p>
      <div v-else-if="summary && (claimCount || unassignedCount)" class="iev__workspace">
        <div class="iev__list">
          <div class="iev__filters">
            <button
              v-for="(label, key) in filterLabels"
              :key="key"
              type="button"
              class="iev__filter"
              :class="{ 'iev__filter--active': filter === key }"
              @click="filter = key as ClaimFilter"
            >
              {{ label }}
            </button>
            <span class="iev__meta">主张 {{ claimCount }} · 未分组证据 {{ unassignedCount }}</span>
          </div>
          <EvidenceClaimList
            :claims="filteredClaims"
            :case-id="caseId"
            @select-claim="onSelectClaim"
            @select-evidence="onSelectEvidence"
            @reviewed="load"
          />
        </div>
        <div class="iev__detail">
          <EvidenceDetailPanel
            :case-id="caseId"
            :claim="selectedClaim"
            :item="selectedItem"
            @reviewed="load"
          />
        </div>
      </div>
      <div v-else class="iev__empty-guide">
        <p>尚无证据 — 在 Copilot 中发送分析指令开始采集与核查。</p>
      </div>
    </template>

    <div v-else class="iev__semantics">
      <SemanticAnnotationsPanel :case-id="caseId" />
    </div>
  </div>
</template>

<style scoped>
.iev {
  display: flex;
  flex-direction: column;
  min-height: 480px;
}

.iev__tabs {
  display: flex;
  gap: 6px;
  padding: 10px 16px 0;
}

.iev__tab {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-bottom: none;
  border-radius: 10px 10px 0 0;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.iev__tab--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.iev__semantics {
  padding: 12px 16px 24px;
}

.iev__workspace {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(260px, 1fr);
  flex: 1;
  min-height: 0;
}

.iev__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 16px;
  overflow-y: auto;
}

.iev__filters {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.iev__filter {
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.iev__filter--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.iev__meta {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-soft);
}

.iev__detail {
  min-height: 0;
}

.iev__error {
  margin: 20px;
  color: var(--red);
  font-size: 13px;
}

.iev__hint {
  margin: 20px;
  color: var(--text-muted);
  font-size: 13px;
}

.iev__empty-guide {
  margin: 20px;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 960px) {
  .iev__workspace {
    grid-template-columns: 1fr;
  }
}
</style>
