<script setup lang="ts">
// C8.1: Evidence workspace 的 claim 列表内容组件（自 EvidenceSidebar 抽出）。
// 每 claim：verdict 状态、confidence、全文、人工确认/驳回、
// support/oppose/context 证据分组（复用 EvidenceItem.stance）。
// selection 通过 emit 上抛（workspace 层负责 Copilot context）。
import { computed, ref } from 'vue'

import { api } from '@/services/api'
import type { ClaimEvidence, EvidenceItem } from '@/types/api'

const props = defineProps<{ claims: ClaimEvidence[]; caseId: string }>()

const emit = defineEmits<{
  selectClaim: [claim: ClaimEvidence]
  selectEvidence: [payload: { claim: ClaimEvidence; item: EvidenceItem }]
  reviewed: []
}>()

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

function stanceLabel(stance: string): string {
  return STANCE_LABELS[stance] || stance
}

async function reviewClaim(claim: ClaimEvidence, confirmed: boolean) {
  if (reviewing.value[claim.id]) return
  reviewing.value = { ...reviewing.value, [claim.id]: true }
  reviewError.value = ''
  try {
    const updated = await api.reviewClaim(props.caseId, claim.id, confirmed)
    localStatus.value = { ...localStatus.value, [claim.id]: updated.status }
    emit('reviewed')
  } catch {
    reviewError.value = '人工复核提交失败，请重试。'
  } finally {
    reviewing.value = { ...reviewing.value, [claim.id]: false }
  }
}

const hasAny = computed(() => props.claims.length > 0)
</script>

<template>
  <div class="ecl">
    <p v-if="reviewError" class="ecl__error">{{ reviewError }}</p>
    <ul v-if="hasAny" class="ecl__list">
      <li
        v-for="claim in claims"
        :key="claim.id"
        class="ecl__claim"
        @click="emit('selectClaim', claim)"
      >
        <div class="ecl__head">
          <span class="verdict-chip" :class="`verdict-${localStatus[claim.id] || claim.status}`">
            {{ verdictLabel(claim) }}
          </span>
          <em v-if="(localStatus[claim.id] || claim.status) === 'verified'" class="ecl__confidence">
            {{ Math.round(claim.confidence * 100) }}%
          </em>
        </div>
        <p class="ecl__text">{{ claim.text }}</p>
        <div class="ecl__actions" @click.stop>
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
        <ul v-if="claim.evidence.length" class="ecl__evidence">
          <li
            v-for="item in claim.evidence"
            :key="item.id"
            class="ecl__item"
            @click.stop="emit('selectEvidence', { claim, item })"
          >
            <span class="stance-chip" :class="`stance-${item.stance}`">
              {{ stanceLabel(item.stance) }}
            </span>
            <div class="ecl__item-body">
              <p>{{ item.excerpt }}</p>
              <em class="ecl__relevance">相关度 {{ item.relevance.toFixed(2) }}</em>
            </div>
          </li>
        </ul>
        <p v-else class="ecl__none">暂无证据绑定</p>
      </li>
    </ul>
    <p v-else class="ecl__empty">当前筛选下没有主张。</p>
  </div>
</template>

<style scoped>
.ecl {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ecl__error {
  margin: 0;
  color: var(--red);
  font-size: 12px;
}

.ecl__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ecl__claim {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  cursor: pointer;
}

.ecl__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ecl__confidence {
  font-style: normal;
  font-size: 12px;
  color: var(--text-muted);
}

.ecl__text {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.ecl__actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.ecl__evidence {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ecl__item {
  display: flex;
  gap: 8px;
  padding: 8px;
  border-radius: 8px;
  background: var(--surface-strong, rgba(255, 255, 255, 0.04));
  cursor: pointer;
}

.ecl__item-body p {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
}

.ecl__relevance {
  font-size: 11px;
  color: var(--text-soft);
}

.ecl__none {
  margin: 0;
  font-size: 12px;
  color: var(--text-soft);
}

.ecl__empty {
  margin: 0;
  color: var(--text-soft);
  font-size: 12px;
}
</style>
