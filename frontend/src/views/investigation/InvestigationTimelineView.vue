<script setup lang="ts">
// Optimization V2 (M5.5)：Timeline 工作区。
// 过渡期复用 NarrativeTimelineView 全量内容（M8 按 Part VIII 矩阵
// 删除旧全局路由入口）；时间范围选中后进入 Copilot context（time_range）。
import { onMounted } from 'vue'

import NarrativeTimelineView from '@/views/NarrativeTimelineView.vue'
import { useInvestigationContext } from '@/composables/useInvestigationContext'

const { setUiContext } = useInvestigationContext()

onMounted(() => {
  setUiContext({ workspace: 'timeline' })
})
</script>

<template>
  <div class="itl">
    <NarrativeTimelineView />
  </div>
</template>

<style scoped>
.itl {
  display: flex;
  flex-direction: column;
  min-height: 520px;
}
</style>
