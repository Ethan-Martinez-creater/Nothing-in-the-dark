<script setup lang="ts">
import { FileText, Sparkles, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import { api } from '@/services/api'
import type { ClaimEvidence, EvidenceItem, EvidenceSummary } from '@/types/api'

const props = defineProps<{
  open: boolean
  summary: EvidenceSummary | null
}>()

const emit = defineEmits<{
  close: []
  runAnalysis: []
}>()

const claimCount = computed(() => props.summary?.claims.length ?? 0)
const evidenceCount = computed(() => {
  const summary = props.summary
  if (!summary) return 0
  return summary.claims.reduce((total, claim) => total + claim.evidence.length, 0)
    + summary.unassigned.length
})

const VERDICT_LABELS: Record<string, string> = {
  supported: '已核实',
  refuted: '已反驳',
  insufficient: '证据不足',
}

const STANCE_LABELS: Record<string, string> = {
  support: '支持',
  oppose: '反驳',
  context: '背景',
}

const PLATFORM_LABELS: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

const reviewing = ref<Record<string, boolean>>({})
const localStatus = ref<Record<string, string>>({})
const reviewError = ref('')

function verdictLabel(claim: ClaimEvidence): string {
  const status = localStatus.value[claim.id] || claim.status
  if (status === 'human_confirmed') return '人工确认'
  if (status === 'human_rejected') return '人工驳回'
  if (status === 'verified' && claim.verdict) {
    return VERDICT_LABELS[claim.verdict] || claim.verdict
  }
  if (status === 'verified') return '已核实'
  if (status === 'rejected') return '已剔除'
  return '待核查'
}

async function reviewClaim(claim: ClaimEvidence, confirmed: boolean) {
  if (!props.summary || reviewing.value[claim.id]) return
  reviewing.value = { ...reviewing.value, [claim.id]: true }
  reviewError.value = ''
  try {
    const updated = await api.reviewClaim(props.summary.case_id, claim.id, confirmed)
    localStatus.value = { ...localStatus.value, [claim.id]: updated.status }
  } catch {
    reviewError.value = '人工复核提交失败，请重试。'
  } finally {
    reviewing.value = { ...reviewing.value, [claim.id]: false }
  }
}

function stanceLabel(stance: string): string {
  return STANCE_LABELS[stance] || stance
}

// 采集帖子（source_type=social_post）显示 平台 · 作者；其余显示 类型 · id。
function sourceLabel(item: EvidenceItem): string {
  if (item.source_type === 'social_post') {
    const meta = item.metadata_json as Record<string, unknown>
    const platform = PLATFORM_LABELS[String(meta.platform || '')] || String(meta.platform || '')
    const author = String(meta.author || '').slice(0, 14)
    return platform && author ? `${platform} · ${author}` : platform || item.source_id.slice(0, 10)
  }
  return `${item.source_type} · ${item.source_id.slice(0, 10)}`
}
</script>

<template>
  <!-- v-if="open"：未打开不渲染（父组件同样以 v-if 控制挂载），
       保证对话区右侧不会因常驻面板产生空白占位 -->
  <aside v-if="open" class="evidence-sidebar" :class="{ open }">
    <div class="evidence-header">
      <div>
        <span class="eyebrow">EVIDENCE</span>
        <h3>案例证据</h3>
      </div>
      <button type="button" class="icon-button" aria-label="关闭证据面板" @click="emit('close')">
        <X :size="16" />
      </button>
    </div>

    <template v-if="summary">
      <div class="evidence-summary">
        <div>
          <span>主张</span>
          <strong>{{ claimCount }}</strong>
        </div>
        <div>
          <span>证据</span>
          <strong>{{ evidenceCount }}</strong>
        </div>
      </div>

      <div v-if="summary.claims.length" class="evidence-section">
        <span class="eyebrow">CLAIMS ({{ claimCount }})</span>
        <ul class="evidence-claim-list">
          <li v-for="claim in summary.claims" :key="claim.id" class="evidence-claim">
            <div class="evidence-claim-head">
              <span class="verdict-chip" :class="`verdict-${claim.status}`">
                {{ verdictLabel(claim) }}
              </span>
              <em v-if="claim.status === 'verified'" class="claim-confidence">
                {{ Math.round(claim.confidence * 100) }}%
              </em>
            </div>
            <p class="claim-text">{{ claim.text }}</p>
            <div class="review-actions">
              <button
                type="button"
                class="ghost-button"
                :disabled="reviewing[claim.id]"
                @click="reviewClaim(claim, true)"
              >
                确认
              </button>
              <button
                type="button"
                class="ghost-button"
                :disabled="reviewing[claim.id]"
                @click="reviewClaim(claim, false)"
              >
                驳回
              </button>
            </div>
            <ul v-if="claim.evidence.length" class="evidence-item-list">
              <li v-for="item in claim.evidence" :key="item.id" class="evidence-item">
                <span class="stance-chip" :class="`stance-${item.stance}`">
                  {{ stanceLabel(item.stance) }}
                </span>
                <div class="evidence-item-body">
                  <span class="evidence-source">{{ sourceLabel(item) }}</span>
                  <p>{{ item.excerpt }}</p>
                  <em class="evidence-relevance">相关度 {{ item.relevance.toFixed(2) }}</em>
                </div>
              </li>
            </ul>
            <p v-else class="claim-no-evidence">暂无证据绑定</p>
          </li>
        </ul>
      </div>

      <p v-if="reviewError" class="panel-notice">{{ reviewError }}</p>

      <div v-if="summary.unassigned.length" class="evidence-section">
        <span class="eyebrow">COLLECTED ({{ summary.unassigned.length }})</span>
        <ul class="evidence-item-list">
          <li v-for="item in summary.unassigned" :key="item.id" class="evidence-item">
            <span class="stance-chip" :class="`stance-${item.stance}`">
              {{ stanceLabel(item.stance) }}
            </span>
            <div class="evidence-item-body">
              <span class="evidence-source">{{ sourceLabel(item) }}</span>
              <p>{{ item.excerpt }}</p>
              <em class="evidence-relevance">相关度 {{ item.relevance.toFixed(2) }}</em>
            </div>
          </li>
        </ul>
      </div>

      <div v-if="!summary.claims.length && !summary.unassigned.length" class="evidence-empty-guide">
        <FileText :size="18" />
        <p>暂无证据数据。</p>
        <p class="evidence-empty-hint">
          证据随分析产生：发起「快速完整分析」采集平台数据并执行事实核查后，
          这里会按主张分组展示支持 / 反驳 / 背景证据。
        </p>
        <button type="button" class="primary-button" @click="emit('runAnalysis')">
          <Sparkles :size="14" />
          发起含事实核查的分析
        </button>
      </div>
    </template>

    <p v-else class="evidence-empty">证据汇总加载中…</p>
  </aside>
</template>
