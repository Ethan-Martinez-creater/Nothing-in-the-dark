<script setup lang="ts">
// Optimization V2 (M2.3)：Investigation Shell。
// 只加载共享轻量数据（case record + capabilities），子页面按需懒加载；
// 禁止 Shell 首次加载 Evidence/Network/Timeline/Media 全量数据。
import { computed, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import InvestigationHeader from '@/components/investigation/InvestigationHeader.vue'
import InvestigationNav from '@/components/investigation/InvestigationNav.vue'
import {
  provideInvestigationContext,
  type InvestigationWorkspace,
} from '@/composables/useInvestigationContext'
import { api } from '@/services/api'
import type { CaseRecord } from '@/types/api'

const route = useRoute()

const caseId = computed(() => String(route.params.caseId ?? ''))
const investigation = ref<CaseRecord | null>(null)
const loadError = ref<string | null>(null)

const WORKSPACE_BY_SUFFIX: Record<string, InvestigationWorkspace> = {
  overview: 'overview',
  'live-data': 'live_data',
  evidence: 'evidence',
  network: 'network',
  timeline: 'timeline',
  findings: 'findings',
  report: 'report',
  activity: 'activity',
}

const { uiContext, setUiContext } = provideInvestigationContext()

async function loadCase() {
  loadError.value = null
  try {
    investigation.value = await api.getCase(caseId.value)
  } catch {
    loadError.value = '调查加载失败，可能已被删除。'
  }
}

// 子路由切换时同步 workspace context（切换会保留用户已设置的 filters 等）。
watch(
  () => route.path,
  (path) => {
    const suffix = path.split('/').pop() ?? 'overview'
    const workspace = WORKSPACE_BY_SUFFIX[suffix]
    if (workspace && uiContext.value.workspace !== workspace) {
      setUiContext({ workspace })
    }
  },
  { immediate: true },
)

onMounted(loadCase)
</script>

<template>
  <div class="ishell">
    <InvestigationHeader :investigation="investigation">
      <template #actions>
        <!-- M2.4：Copilot launcher -->
      </template>
    </InvestigationHeader>
    <InvestigationNav :case-id="caseId" />
    <p v-if="loadError" class="ishell__error">{{ loadError }}</p>
    <div class="ishell__body">
      <RouterView v-if="investigation" :key="caseId" />
    </div>
  </div>
</template>

<style scoped>
.ishell {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.ishell__error {
  margin: 16px;
  color: var(--red);
  font-size: 13px;
}

.ishell__body {
  flex: 1;
  min-width: 0;
  min-height: 0;
}
</style>
