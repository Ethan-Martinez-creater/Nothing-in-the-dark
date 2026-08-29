<script setup lang="ts">
// Optimization V2 (M5.1/M5.3)：Network 全尺寸工作区。
// 三种模式共用 toolbar shell 与右侧详情（过渡期复用现有面板组件，
// open=true 全尺寸渲染；M8 移除 sidebar-only 入口）。选中对象进入
// Copilot context（workspace=network）。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AlignmentPanel from '@/components/alignment/AlignmentPanel.vue'
import IntegrityPanel from '@/components/integrity/IntegrityPanel.vue'
import VisualSidebar from '@/components/visual/VisualSidebar.vue'
import {
  useInvestigationContext,
} from '@/composables/useInvestigationContext'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

type NetworkMode = 'propagation' | 'alignment' | 'integrity'
const mode = ref<NetworkMode>('propagation')

const { setUiContext } = useInvestigationContext()

const modeLabels: Record<NetworkMode, string> = {
  propagation: '传播网络',
  alignment: '跨平台对齐',
  integrity: '协同行为',
}

// 进入 Network 工作区：设置 copilot 上下文（保留 filters/time_range）。
watch(
  mode,
  (value) => {
    setUiContext({ workspace: 'network', selected_type: `network_mode_${value}` })
  },
  { immediate: true },
)

onMounted(() => {
  setUiContext({ workspace: 'network' })
})
</script>

<template>
  <div class="inet">
    <div class="inet__toolbar">
      <div class="inet__modes">
        <button
          v-for="(label, key) in modeLabels"
          :key="key"
          type="button"
          class="inet__mode"
          :class="{ 'inet__mode--active': mode === key }"
          @click="mode = key as NetworkMode"
        >
          {{ label }}
        </button>
      </div>
      <p class="inet__note">
        候选源头与推断关系不代表已证实结论；置信度与证据请查看详情面板。
      </p>
    </div>

    <div class="inet__canvas">
      <VisualSidebar v-if="mode === 'propagation'" :open="true" :case-id="caseId" />
      <AlignmentPanel v-else-if="mode === 'alignment'" :case-id="caseId" :open="true" />
      <IntegrityPanel v-else :case-id="caseId" :open="true" />
    </div>
  </div>
</template>

<style scoped>
.inet {
  display: flex;
  flex-direction: column;
  min-height: 520px;
}

.inet__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  flex-wrap: wrap;
}

.inet__modes {
  display: flex;
  gap: 6px;
}

.inet__mode {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.inet__mode--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.inet__note {
  margin: 0;
  font-size: 11px;
  color: var(--text-soft);
}

.inet__canvas {
  flex: 1;
  min-height: 0;
}
</style>
