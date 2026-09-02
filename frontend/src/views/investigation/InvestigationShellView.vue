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

type LayoutMode = 'split' | 'content' | 'copilot'

const route = useRoute()

const caseId = computed(() => String(route.params.caseId ?? ''))
const investigation = ref<CaseRecord | null>(null)
const loadError = ref<string | null>(null)

/**
 * 工作区布局三态：
 * - split   ：左侧概览等模块与右侧 Copilot 分屏（默认）
 * - content ：全部显示概览等模块，隐藏 Copilot
 * - copilot ：全部显示 Copilot，隐藏概览等模块
 */
const layoutMode = ref<LayoutMode>('split')

const LAYOUT_OPTIONS: Array<{ key: LayoutMode; label: string }> = [
  { key: 'split', label: '分屏' },
  { key: 'content', label: '仅内容' },
  { key: 'copilot', label: '仅 Copilot' },
]

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
    <div class="ishell__main" :class="`ishell__main--${layoutMode}`">
      <div v-show="layoutMode !== 'copilot'" class="ishell__content">
        <InvestigationHeader :investigation="investigation">
          <template #actions>
            <div class="ishell__layout-switch" role="group" aria-label="工作区布局">
              <button
                v-for="option in LAYOUT_OPTIONS"
                :key="option.key"
                type="button"
                class="ishell__layout-btn"
                :class="{ 'ishell__layout-btn--active': layoutMode === option.key }"
                :aria-pressed="layoutMode === option.key"
                @click="layoutMode = option.key"
              >
                {{ option.label }}
              </button>
            </div>
          </template>
        </InvestigationHeader>
        <InvestigationNav :case-id="caseId" />
        <p v-if="loadError" class="ishell__error">{{ loadError }}</p>
        <div class="ishell__body">
          <RouterView v-if="investigation" :key="caseId" />
        </div>
      </div>
      <CopilotDrawer
        v-if="layoutMode !== 'content' && investigation"
        :case-id="caseId"
        class="ishell__copilot"
        @close="layoutMode = 'content'"
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

  /* 分屏：内容区让出右侧 Copilot 宽度 */
  .ishell__main--split .ishell__content {
    width: calc(100% - 420px);
  }

  /* 仅内容：内容区占满 */
  .ishell__main--content .ishell__content {
    width: 100%;
  }

  /* 仅 Copilot：Copilot 占满（内容区 v-show 隐藏） */
  .ishell__main--copilot .ishell__copilot {
    width: 100%;
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

.ishell__layout-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
}

.ishell__layout-btn {
  padding: 5px 12px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.ishell__layout-btn--active {
  background: var(--accent);
  color: #fff;
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
