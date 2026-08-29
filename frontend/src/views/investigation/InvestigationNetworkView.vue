<script setup lang="ts">
// Optimization V2 (M5.1/M5.3 + C7)：Network 全尺寸工作区。
// Propagation 模式为真实图工作区（PropagationGraph + DetailPanel），
// 不再挂载 VisualSidebar/PlatformComparisonCard。选中节点/边进入
// Copilot context（workspace=network, selected_type=propagation_node/edge）。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AlignmentPanel from '@/components/alignment/AlignmentPanel.vue'
import IntegrityPanel from '@/components/integrity/IntegrityPanel.vue'
import PropagationDetailPanel from '@/components/network/PropagationDetailPanel.vue'
import PropagationGraph, {
  type PropagationSelection,
} from '@/components/network/PropagationGraph.vue'
import { api } from '@/services/api'
import type { PropagationGraphDTO } from '@/types/api'
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

const graph = ref<PropagationGraphDTO | null>(null)
const graphLoading = ref(false)
const graphError = ref('')
const selection = ref<PropagationSelection | null>(null)

async function loadGraph() {
  if (!caseId.value) return
  graphLoading.value = true
  graphError.value = ''
  try {
    graph.value = await api.getPropagationGraph(caseId.value)
  } catch {
    graphError.value = '传播图加载失败，请稍后重试。'
  } finally {
    graphLoading.value = false
  }
}

function onSelect(next: PropagationSelection) {
  selection.value = next
  // 选中对象进入 Copilot context（workspace=network）
  setUiContext({
    workspace: 'network',
    selected_type: next.type,
    selected_id: next.id,
  })
}

function refreshGraph() {
  void loadGraph()
}

// 进入 Network 工作区：设置 copilot 上下文（保留 filters/time_range）。
watch(
  mode,
  (value) => {
    if (value !== 'propagation') {
      selection.value = null
    }
    setUiContext({ workspace: 'network', selected_type: `network_mode_${value}` })
  },
  { immediate: true },
)

onMounted(() => {
  setUiContext({ workspace: 'network' })
  void loadGraph()
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

    <div v-if="mode === 'propagation'" class="inet__propagation">
      <div class="inet__canvas">
        <PropagationGraph
          :graph="graph"
          :loading="graphLoading"
          :error="graphError"
          @select="onSelect"
        />
      </div>
      <div class="inet__detail">
        <PropagationDetailPanel
          :case-id="caseId"
          :graph="graph"
          :selection="selection"
          @refresh="refreshGraph"
        />
      </div>
    </div>
    <div v-else class="inet__canvas">
      <AlignmentPanel v-if="mode === 'alignment'" :case-id="caseId" :open="true" />
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

.inet__propagation {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  flex: 1;
  min-height: 0;
}

.inet__canvas {
  flex: 1;
  min-height: 0;
}

@media (max-width: 960px) {
  .inet__propagation {
    grid-template-columns: 1fr;
  }
}
</style>
