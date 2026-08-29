<script setup lang="ts">
// Optimization V2 (M3.9)：Investigation Overview。
// Scope（Active Collection Definition）+ 当前状态；Copilot 由 Shell 右侧提供。
// M5/M6 逐步补充 Investigation Plan / Latest Outputs / Monitoring 卡。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import CollectionDefinitionCard from '@/components/collection/CollectionDefinitionCard.vue'
import { api } from '@/services/api'
import type { AgentRun, Artifact, CaseRecord } from '@/types/api'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

const investigation = ref<CaseRecord | null>(null)
const runs = ref<AgentRun[]>([])
const artifacts = ref<Artifact[]>([])
const loading = ref(true)

const activeRuns = computed(() =>
  runs.value.filter((run) => ['pending', 'running', 'waiting_approval'].includes(run.status)),
)

onMounted(async () => {
  try {
    const [record, runList, artifactList] = await Promise.all([
      api.getCase(caseId.value),
      api.listCaseRuns(caseId.value),
      api.listArtifacts(caseId.value),
    ])
    investigation.value = record
    runs.value = runList
    artifacts.value = artifactList
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="ioverview">
    <p v-if="loading" class="ioverview__hint">正在加载…</p>

    <template v-else>
      <section class="ioverview__status" aria-label="当前状态">
        <div class="ioverview__stat">
          <span class="ioverview__stat-value">{{ activeRuns.length }}</span>
          <span class="ioverview__stat-label">进行中的分析</span>
        </div>
        <div class="ioverview__stat">
          <span class="ioverview__stat-value">{{ runs.length }}</span>
          <span class="ioverview__stat-label">历史分析</span>
        </div>
        <div class="ioverview__stat">
          <span class="ioverview__stat-value">{{ artifacts.length }}</span>
          <span class="ioverview__stat-label">分析成果</span>
        </div>
      </section>

      <CollectionDefinitionCard
        :case-id="caseId"
        :case-platforms="investigation?.platforms ?? []"
      />
    </template>
  </div>
</template>

<style scoped>
.ioverview {
  max-width: 880px;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.ioverview__hint {
  color: var(--text-muted);
  font-size: 13px;
}

.ioverview__status {
  display: flex;
  gap: 12px;
}

.ioverview__stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.ioverview__stat-value {
  font-size: 22px;
  font-weight: 700;
}

.ioverview__stat-label {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
