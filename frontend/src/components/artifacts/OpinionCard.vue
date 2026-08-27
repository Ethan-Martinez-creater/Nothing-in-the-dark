<script setup lang="ts">
import { CircleAlert } from 'lucide-vue-next'

import type { OpinionData } from '@/types/api'

defineProps<{ data: OpinionData }>()

function statLines(statistics: Record<string, unknown>): Array<[string, string]> {
  return Object.entries(statistics).map(([key, value]) => [
    key,
    typeof value === 'object'
      ? JSON.stringify(value)
      : String(value),
  ])
}
</script>

<template>
  <section class="panel artifact-panel">
    <div class="panel-heading">
      <div>
        <span class="eyebrow">OPINION ANALYSIS</span>
        <h3>舆论观点分析</h3>
      </div>
    </div>

    <p v-if="data.explanation?.text" class="opinion-explanation">
      {{ data.explanation.text }}
    </p>
    <div v-if="data.explanation?.evidence_ids?.length" class="evidence-chips">
      <span v-for="eid in data.explanation.evidence_ids" :key="eid">{{ eid }}</span>
    </div>

    <ul class="finding-list">
      <li v-for="item in data.conclusions" :key="item.claim" class="opinion-conclusion">
        <div class="opinion-claim">
          <strong>{{ item.claim }}</strong>
          <em>置信度 {{ Math.round(item.confidence * 100) }}%</em>
        </div>
        <div v-if="item.evidence_ids.length" class="evidence-chips">
          <span v-for="eid in item.evidence_ids" :key="eid">{{ eid }}</span>
        </div>
      </li>
    </ul>

    <div v-if="statLines(data.statistics).length" class="stat-grid">
      <div v-for="[key, value] in statLines(data.statistics)" :key="key">
        <span>{{ key }}</span>
        <strong>{{ value }}</strong>
      </div>
    </div>

    <p v-if="data.limitations.length" class="panel-notice">
      <CircleAlert :size="14" />
      局限：{{ data.limitations.join('；') }}
    </p>
  </section>
</template>
