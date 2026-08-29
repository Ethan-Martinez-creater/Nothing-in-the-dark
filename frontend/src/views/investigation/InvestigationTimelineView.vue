<script setup lang="ts">
// Optimization V2 (M5.5 + C8.2)：Timeline 工作区。
// Volume / Platform / Narrative 三种 timeline；时间范围选择进入
// Copilot context（workspace=timeline, time_range）。
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'

import TimelineWorkspaceContent from '@/components/timeline/TimelineWorkspaceContent.vue'
import { useInvestigationContext } from '@/composables/useInvestigationContext'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const { setUiContext } = useInvestigationContext()

onMounted(() => {
  setUiContext({ workspace: 'timeline' })
})
</script>

<template>
  <div class="itl">
    <TimelineWorkspaceContent :case-id="caseId" :set-time-range="true" />
  </div>
</template>

<style scoped>
.itl {
  display: flex;
  flex-direction: column;
  min-height: 520px;
}
</style>
