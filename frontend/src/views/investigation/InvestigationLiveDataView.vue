<script setup lang="ts">
// Optimization V2 (M5.6)：Live Data 工作区。
// 回答"系统收集到了什么"：Platform Comparison + Media；Posts 列表在
// 出现统一 raw-post 列表 API 前先经采集定义/证据页提供（不伪造完整列表）。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import MediaPanel from '@/components/media/MediaPanel.vue'
import PlatformComparisonCard from '@/components/platform/PlatformComparisonCard.vue'
import { useInvestigationContext } from '@/composables/useInvestigationContext'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

type LiveDataTab = 'comparison' | 'media'
const tab = ref<LiveDataTab>('comparison')

const { setUiContext } = useInvestigationContext()

const tabLabels: Record<LiveDataTab, string> = {
  comparison: '平台对比',
  media: '媒体',
}

onMounted(() => {
  setUiContext({ workspace: 'live_data' })
})
</script>

<template>
  <div class="ilive">
    <div class="ilive__tabs">
      <button
        v-for="(label, key) in tabLabels"
        :key="key"
        type="button"
        class="ilive__tab"
        :class="{ 'ilive__tab--active': tab === key }"
        @click="tab = key as LiveDataTab"
      >
        {{ label }}
      </button>
      <span class="ilive__note">原始帖列表将随后续采集定义深化提供。</span>
    </div>

    <div class="ilive__body">
      <PlatformComparisonCard v-if="tab === 'comparison'" :case-id="caseId" />
      <MediaPanel v-else :case-id="caseId" :open="true" />
    </div>
  </div>
</template>

<style scoped>
.ilive {
  display: flex;
  flex-direction: column;
  min-height: 480px;
}

.ilive__tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.ilive__tab {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
}

.ilive__tab--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.ilive__note {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-soft);
}

.ilive__body {
  flex: 1;
  min-height: 0;
  padding: 12px 16px 24px;
}
</style>
