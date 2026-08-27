<script setup lang="ts">
import { AlertTriangle, BadgeCheck, CircleHelp, ShieldAlert, XCircle } from 'lucide-vue-next'
import { ref } from 'vue'

import { api } from '@/services/api'
import type { FactCheckData } from '@/types/api'

const props = defineProps<{ data: FactCheckData; caseId?: string }>()

const reviewing = ref<Record<string, boolean>>({})
const reviewState = ref<Record<string, 'human_confirmed' | 'human_rejected'>>({})
const reviewError = ref('')

const verdictLabels: Record<string, string> = {
  supported: '有证据支持',
  refuted: '有证据反驳',
  insufficient: '证据不足',
  misleading: '存在误导',
}

const consistencyLabels: Record<string, string> = {
  pass: '通过',
  fail: '不通过',
  unknown: '未知',
}

function verdictClass(verdict: string): string {
  if (verdict === 'supported') return 'supported'
  if (verdict === 'misleading') return 'misleading'
  if (verdict === 'refuted') return 'refuted'
  return 'insufficient'
}

async function review(claimId: string, confirmed: boolean) {
  if (!props.caseId || !claimId || reviewing.value[claimId]) return
  reviewing.value = { ...reviewing.value, [claimId]: true }
  reviewError.value = ''
  try {
    await api.reviewClaim(props.caseId, claimId, confirmed)
    reviewState.value = {
      ...reviewState.value,
      [claimId]: confirmed ? 'human_confirmed' : 'human_rejected',
    }
  } catch {
    reviewError.value = '人工复核提交失败，请重试。'
  } finally {
    reviewing.value = { ...reviewing.value, [claimId]: false }
  }
}
</script>

<template>
  <section class="panel artifact-panel">
    <div class="panel-heading">
      <div>
        <span class="eyebrow">CLAIM VERIFICATION</span>
        <h3>事实核查</h3>
      </div>
      <span class="evidence-boundary">SOCIAL EVIDENCE ONLY</span>
    </div>

    <div class="fact-list">
      <article
        v-for="(card, index) in data.cards"
        :key="card.id || index"
        class="fact-card"
        :class="verdictClass(card.verdict)"
      >
        <div class="fact-icon">
          <BadgeCheck v-if="card.verdict === 'supported'" :size="19" />
          <XCircle v-else-if="card.verdict === 'refuted'" :size="19" />
          <ShieldAlert v-else-if="card.verdict === 'misleading'" :size="19" />
          <CircleHelp v-else :size="19" />
        </div>
        <div>
          <div class="fact-meta">
            <span>{{ verdictLabels[card.verdict] || card.verdict }}</span>
            <em>置信度 {{ Math.round(card.confidence * 100) }}%</em>
          </div>
          <h4>{{ card.claim }}</h4>
          <p>{{ card.reason }}</p>
          <div class="consistency-row">
            <span v-if="card.temporal_consistency">
              时间 {{ consistencyLabels[card.temporal_consistency] }}
            </span>
            <span v-if="card.subject_consistency">
              主体 {{ consistencyLabels[card.subject_consistency] }}
            </span>
            <span v-if="card.context_consistency">
              语境 {{ consistencyLabels[card.context_consistency] }}
            </span>
          </div>
          <div class="evidence-count">
            <span>支持证据 {{ card.supporting_evidence.length }}</span>
            <span>反驳证据 {{ card.contradicting_evidence.length }}</span>
          </div>
          <div v-if="caseId && card.id" class="review-actions">
            <button
              type="button"
              class="ghost-button"
              :disabled="reviewing[card.id]"
              @click="review(card.id, true)"
            >
              确认
            </button>
            <button
              type="button"
              class="ghost-button"
              :disabled="reviewing[card.id]"
              @click="review(card.id, false)"
            >
              驳回
            </button>
            <em v-if="reviewState[card.id]">
              {{ reviewState[card.id] === 'human_confirmed' ? '已人工确认' : '已人工驳回' }}
            </em>
          </div>
        </div>
      </article>
    </div>
    <p v-if="reviewError" class="panel-notice">{{ reviewError }}</p>
    <p v-if="data.limitations.length" class="panel-notice">
      <AlertTriangle :size="14" />
      局限：{{ data.limitations.join('；') }}
    </p>
  </section>
</template>
