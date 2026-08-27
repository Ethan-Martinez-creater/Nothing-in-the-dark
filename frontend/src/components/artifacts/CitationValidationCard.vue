<script setup lang="ts">
import { BadgeCheck, CircleHelp, XCircle } from 'lucide-vue-next'

import type { CitationValidationData } from '@/types/api'

defineProps<{ data: CitationValidationData }>()

const verdictLabels: Record<string, string> = {
  valid: '引用有效',
  invalid: '引用无效',
  not_found: '引用不存在',
}
</script>

<template>
  <section class="panel artifact-panel">
    <div class="panel-heading">
      <div>
        <span class="eyebrow">CITATION VALIDATION</span>
        <h3>引用校验</h3>
      </div>
    </div>

    <ul class="finding-list">
      <li
        v-for="(check, index) in data.checks"
        :key="index"
        class="citation-check"
        :class="check.verdict"
      >
        <div class="citation-icon">
          <BadgeCheck v-if="check.verdict === 'valid'" :size="18" />
          <XCircle v-else-if="check.verdict === 'invalid'" :size="18" />
          <CircleHelp v-else :size="18" />
        </div>
        <div>
          <div class="fact-meta">
            <span>{{ verdictLabels[check.verdict] || check.verdict }}</span>
          </div>
          <h4>{{ check.citation }}</h4>
          <p>{{ check.reason }}</p>
        </div>
      </li>
    </ul>
  </section>
</template>
