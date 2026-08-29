<script setup lang="ts">
// Optimization V2 (M4.8)：Evidence full workspace。
// 复用 EvidenceSidebar 的 claim/evidence 探索逻辑（过渡：组件作为全尺寸
// 工作区渲染，M8 收尾时按 Part VIII 矩阵移除 sidebar-only 入口）。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import EvidenceSidebar from '@/components/evidence/EvidenceSidebar.vue'
import { api } from '@/services/api'
import type { EvidenceSummary } from '@/types/api'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const summary = ref<EvidenceSummary | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    summary.value = await api.getEvidenceSummary(caseId.value)
  } catch {
    error.value = '证据加载失败，请先运行分析采集数据。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="iev">
    <p v-if="error" class="iev__error">{{ error }}</p>
    <p v-else-if="loading" class="iev__hint">正在加载…</p>
    <div v-else-if="summary" class="iev__body">
      <EvidenceSidebar :open="true" :summary="summary" @close="load" />
    </div>
    <p v-else class="iev__hint">
      尚无证据 — 在 Copilot 中发送分析指令开始采集与核查。
    </p>
  </div>
</template>

<style scoped>
.iev {
  display: flex;
  flex-direction: column;
  min-height: 480px;
}

.iev__body {
  flex: 1;
  min-height: 0;
  border: 0;
  background: transparent;
}

.iev__error {
  margin: 20px;
  color: var(--red);
  font-size: 13px;
}

.iev__hint {
  margin: 20px;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
