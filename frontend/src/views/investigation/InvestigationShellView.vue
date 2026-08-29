<script setup lang="ts">
// Optimization V2 (M2.3)：Investigation Shell。
// 只加载共享轻量数据（case record + capabilities），子页面按需懒加载；
// 禁止 Shell 首次加载 Evidence/Network/Timeline/Media 全量数据。
import { computed, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import InvestigationHeader from '@/components/investigation/InvestigationHeader.vue'
import InvestigationNav from '@/components/investigation/InvestigationNav.vue'
import CopilotDrawer from '@/components/copilot/CopilotDrawer.vue'
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
const copilotOpen = ref(true)

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
    <div class="ishell__main" :class="{ 'ishell__main--with-copilot': copilotOpen }">
      <div class="ishell__content">
        <InvestigationHeader :investigation="investigation">
          <template #actions>
            <button
              v-if="!copilotOpen"
              type="button"
              class="ishell__copilot-launcher"
              @click="copilotOpen = true"
            >
              Copilot
            </button>
          </template>
        </InvestigationHeader>
        <InvestigationNav :case-id="caseId" />
        <p v-if="loadError" class="ishell__error">{{ loadError }}</p>
        <div class="ishell__body">
          <RouterView v-if="investigation" :key="caseId" />
        </div>
      </div>
      <CopilotDrawer
        v-if="copilotOpen && investigation"
        :case-id="caseId"
        class="ishell__copilot"
        @close="copilotOpen = false"
      />
    </div>
  </div>
</template>

<style scoped>
.ishell {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.ishell__main {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

@media (min-width: 1100px) {
  .ishell__main {
    flex-direction: row;
  }

  .ishell__main--with-copilot .ishell__content {
    width: calc(100% - 420px);
  }

  .ishell__copilot {
    width: 420px;
    height: calc(100vh - 48px);
    position: sticky;
    top: 48px;
    flex-shrink: 0;
  }
}

.ishell__content {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.ishell__copilot-launcher {
  padding: 7px 14px;
  border: 1px solid var(--accent);
  border-radius: 10px;
  background: var(--surface);
  color: var(--accent);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
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
