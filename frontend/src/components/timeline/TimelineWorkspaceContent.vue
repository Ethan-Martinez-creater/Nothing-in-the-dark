<script setup lang="ts">
// C8.2: Timeline workspace 内容组件（新旧 route 临时复用）。
// Volume Timeline / Platform Timeline 数据来自 GET /cases/{id}/posts:stats
// （现有持久化数据上的轻量只读聚合，无新持久化表）；Narrative Timeline
// 复用既有 NarrativeTimelineView。时间范围选择进入 Copilot context
// （workspace=timeline, time_range）并过滤 volume/platform 图。
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import NarrativeTimelineView from '@/views/NarrativeTimelineView.vue'
import { api } from '@/services/api'
import type { PostsStatsDTO } from '@/types/api'
import { useInvestigationContext } from '@/composables/useInvestigationContext'

use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ caseId: string; setTimeRange?: boolean }>()

// 旧全局路由（无 Investigation Shell）没有 context provider：静默降级
let contextApi: { setUiContext: (context: Record<string, unknown>) => void } | null = null
try {
  contextApi = useInvestigationContext()
} catch {
  contextApi = null
}

type TimelineTab = 'volume' | 'platform' | 'narrative'
const tab = ref<TimelineTab>('volume')
const tabLabels: Record<TimelineTab, string> = {
  volume: 'Volume Timeline',
  platform: 'Platform Timeline',
  narrative: 'Narrative Timeline',
}

const stats = ref<PostsStatsDTO | null>(null)
const loading = ref(false)
const error = ref('')

// 时间范围过滤（进入 Copilot context）
const rangeFrom = ref('')
const rangeTo = ref('')

async function load() {
  if (!props.caseId) return
  loading.value = true
  error.value = ''
  try {
    stats.value = await api.getPostStats(props.caseId)
  } catch {
    error.value = '时间聚合数据加载失败。'
  } finally {
    loading.value = false
  }
}

const filteredVolume = computed(() => {
  const all = stats.value?.volume_by_day ?? []
  return all.filter((item) => {
    if (rangeFrom.value && item.day < rangeFrom.value) return false
    if (rangeTo.value && item.day > rangeTo.value) return false
    return true
  })
})

const filteredPlatform = computed(() => {
  const all = stats.value?.platform_by_day ?? []
  return all.filter((item) => {
    if (rangeFrom.value && item.day < rangeFrom.value) return false
    if (rangeTo.value && item.day > rangeTo.value) return false
    return true
  })
})

const platformNames = computed(() => [
  ...new Set(filteredPlatform.value.map((item) => item.platform)),
])

const volumeElement = ref<HTMLDivElement | null>(null)
const platformElement = ref<HTMLDivElement | null>(null)
let volumeChart: ECharts | null = null
let platformChart: ECharts | null = null

function renderVolume() {
  if (!volumeElement.value) return
  volumeChart ||= init(volumeElement.value)
  const days = filteredVolume.value.map((item) => item.day)
  const counts = filteredVolume.value.map((item) => item.count)
  const option: EChartsCoreOption = {
    backgroundColor: 'transparent',
    tooltip: { confine: true },
    grid: { left: 40, right: 16, top: 24, bottom: 28 },
    xAxis: { type: 'category', data: days },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'bar',
        name: '帖子量',
        data: counts,
        itemStyle: { color: '#4d7cff' },
      },
    ],
  }
  volumeChart.setOption(option, true)
}

function renderPlatform() {
  if (!platformElement.value) return
  platformChart ||= init(platformElement.value)
  const days = [
    ...new Set(filteredPlatform.value.map((item) => item.day)),
  ].sort()
  const series = platformNames.value.map((platform) => ({
    name: platform,
    type: 'line',
    stack: 'total',
    areaStyle: { opacity: 0.2 },
    data: days.map((day) => {
      const hit = filteredPlatform.value.find(
        (item) => item.platform === platform && item.day === day,
      )
      return hit?.count ?? 0
    }),
  }))
  const option: EChartsCoreOption = {
    backgroundColor: 'transparent',
    tooltip: { confine: true, trigger: 'axis' },
    legend: { bottom: 0, textStyle: { color: '#8f9bb3' } },
    grid: { left: 40, right: 16, top: 24, bottom: 40 },
    xAxis: { type: 'category', data: days },
    yAxis: { type: 'value' },
    series,
  }
  platformChart.setOption(option, true)
}

function emitTimeRange() {
  if (!props.setTimeRange) return
  if (!rangeFrom.value && !rangeTo.value) return
  contextApi?.setUiContext({
    workspace: 'timeline',
    time_range: {
      start: rangeFrom.value || undefined,
      end: rangeTo.value || undefined,
    },
  })
}

function onRangeChange() {
  emitTimeRange()
  renderVolume()
  renderPlatform()
}

function resize() {
  volumeChart?.resize()
  platformChart?.resize()
}

onMounted(() => {
  void load()
  window.addEventListener('resize', resize)
})

watch(stats, () => {
  renderVolume()
  renderPlatform()
})
watch(tab, (value) => {
  if (value === 'volume') renderVolume()
  if (value === 'platform') renderPlatform()
})
watch(filteredVolume, renderVolume)
watch(filteredPlatform, renderPlatform)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  volumeChart?.dispose()
  platformChart?.dispose()
  volumeChart = null
  platformChart = null
})
</script>

<template>
  <div class="twc">
    <div class="twc__toolbar">
      <div class="twc__tabs">
        <button
          v-for="(label, key) in tabLabels"
          :key="key"
          type="button"
          class="twc__tab"
          :class="{ 'twc__tab--active': tab === key }"
          @click="tab = key as TimelineTab"
        >
          {{ label }}
        </button>
      </div>
      <div class="twc__range">
        <input v-model="rangeFrom" type="date" class="twc__date" @change="onRangeChange" />
        <span>→</span>
        <input v-model="rangeTo" type="date" class="twc__date" @change="onRangeChange" />
      </div>
    </div>

    <div v-if="tab === 'narrative'" class="twc__narrative">
      <NarrativeTimelineView />
    </div>
    <template v-else>
      <p v-if="error" class="twc__state twc__state--error">{{ error }}</p>
      <p v-else-if="loading" class="twc__state">聚合数据加载中…</p>
      <p
        v-else-if="tab === 'volume' && !filteredVolume.length"
        class="twc__state"
      >
        该时间范围内暂无帖子数据。
      </p>
      <p
        v-else-if="tab === 'platform' && !filteredPlatform.length"
        class="twc__state"
      >
        该时间范围内暂无平台数据。
      </p>
      <div v-show="tab === 'volume'" ref="volumeElement" class="twc__chart"></div>
      <div v-show="tab === 'platform'" ref="platformElement" class="twc__chart"></div>
    </template>
  </div>
</template>

<style scoped>
.twc {
  display: flex;
  flex-direction: column;
  min-height: 480px;
}

.twc__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}

.twc__tabs {
  display: flex;
  gap: 6px;
}

.twc__tab {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.twc__tab--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.twc__range {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-soft);
}

.twc__date {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
}

.twc__narrative {
  flex: 1;
  min-height: 0;
}

.twc__chart {
  flex: 1;
  min-height: 380px;
}

.twc__state {
  margin: 24px;
  text-align: center;
  color: var(--text-soft);
  font-size: 13px;
}

.twc__state--error {
  color: var(--red);
}
</style>
