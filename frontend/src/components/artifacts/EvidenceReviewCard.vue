<script setup lang="ts">
import { BadgeCheck, CircleAlert, ShieldAlert } from 'lucide-vue-next'

import type { EvidenceReviewData } from '@/types/api'

defineProps<{ data: EvidenceReviewData }>()

const verdictLabels: Record<string, string> = {
  supported: '证据支持',
  unsupported: '证据不足',
  overreach: '过度推断',
}
</script>

<template>
  <section class="panel artifact-panel">
    <div class="panel-heading">
      <div>
        <span class="eyebrow">EVIDENCE REVIEW</span>
        <h3>证据批判评审</h3>
      </div>
    </div>

    <ul class="finding-list">
      <li v-for="(verdict, index) in data.verdicts" :key="index" class="review-verdict">
        <div class="review-icon">
          <BadgeCheck v-if="verdict.verdict === 'supported'" :size="18" />
          <CircleAlert v-else-if="verdict.verdict === 'unsupported'" :size="18" />
          <ShieldAlert v-else :size="18" />
        </div>
        <div>
          <div class="fact-meta">
            <span>{{ verdictLabels[verdict.verdict] || verdict.verdict }}</span>
          </div>
          <h4>{{ verdict.target }}</h4>
          <p>{{ verdict.reason }}</p>
          <div v-if="verdict.evidence_ids.length" class="evidence-chips">
            <span v-for="eid in verdict.evidence_ids" :key="eid">{{ eid }}</span>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
