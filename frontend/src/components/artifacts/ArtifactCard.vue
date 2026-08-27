<script setup lang="ts">
import { MessageCircleQuestion } from 'lucide-vue-next'

import type {
  Artifact,
  CitationValidationData,
  EvidenceReviewData,
  FactCheckData,
  OpinionData,
  PropagationData,
  ReportData,
} from '@/types/api'

import CitationValidationCard from './CitationValidationCard.vue'
import EvidenceReviewCard from './EvidenceReviewCard.vue'
import FactCheckCard from './FactCheckCard.vue'
import OpinionCard from './OpinionCard.vue'
import PropagationCard from './PropagationCard.vue'
import ReportCard from './ReportCard.vue'

defineProps<{ artifact: Artifact; caseId?: string }>()

const emit = defineEmits<{
  askArtifact: [artifactId: string]
}>()
</script>

<template>
  <div class="artifact-card">
    <OpinionCard v-if="artifact.kind === 'opinion_analysis'" :data="artifact.data as unknown as OpinionData" />
    <PropagationCard
      v-else-if="artifact.kind === 'propagation_reconstruction'"
      :data="artifact.data as unknown as PropagationData"
      :case-id="caseId || ''"
    />
    <FactCheckCard
      v-else-if="artifact.kind === 'fact_check'"
      :data="artifact.data as unknown as FactCheckData"
      :case-id="caseId || ''"
    />
    <EvidenceReviewCard
      v-else-if="artifact.kind === 'evidence_review'"
      :data="artifact.data as unknown as EvidenceReviewData"
    />
    <ReportCard
      v-else-if="artifact.kind === 'report'"
      :data="artifact.data as unknown as ReportData"
      :artifact-id="artifact.id"
    />
    <CitationValidationCard
      v-else-if="artifact.kind === 'citation_validation'"
      :data="artifact.data as unknown as CitationValidationData"
    />
    <pre v-else class="artifact-raw">{{ JSON.stringify(artifact.data, null, 2) }}</pre>
    <button
      type="button"
      class="ghost-button ask-artifact-button"
      title="针对此成果发起追问"
      @click="emit('askArtifact', artifact.id)"
    >
      <MessageCircleQuestion :size="14" />
      追问此成果
    </button>
  </div>
</template>
