<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { PlatformComparison } from '@/types/api'

// 纯内容组件（无模态外壳）：供右侧可视化边栏 / 模态嵌入。
const props = defineProps<{ caseId: string }>()

const data = ref<PlatformComparison | null>(null)
const error = ref('')
const chartEl = ref<HTMLDivElement | null>(null)

const PLATFORM_NAMES: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

const SENTIMENT_LABELS: Record<string, string> = {
  positive: '正面',
  neutral: '中性',
  negative: '负面',
}

const SENTIMENT_COLORS = ['#10b981', '#94a3b8', '#ef4444']

async function renderChart() {
  if (!data.value || !chartEl.value) return
  const echarts = await import('echarts/core')
  const { init } = echarts
  const { BarChart } = await import('echarts/charts')
  const { GridComponent, LegendComponent, TooltipComponent } = await import('echarts/components')
  const { CanvasRenderer } = await import('echarts/renderers')
  echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
  const chart = init(chartEl.value)

  const platforms = data.value.platforms.map((p) => PLATFORM_NAMES[p] || p)
  const participation = data.value.participation.map((item) => ({
    platform: PLATFORM_NAMES[item.platform] || item.platform,
    posts: item.posts,
    engagement: item.total_engagement,
  }))

  const sentimentSeries = ['positive', 'neutral', 'negative'].map((key, index) => ({
    name: SENTIMENT_LABELS[key],
    type: 'bar',
    stack: 'sentiment',
    data: data.value!.sentiment.map((entry) => entry.distribution[key] || 0),
    itemStyle: { color: SENTIMENT_COLORS[index] },
  }))

  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0 },
    grid: [
      { left: 8, right: 8, top: 42, height: 118, containLabel: true },
      { left: 8, right: 8, top: 218, height: 118, containLabel: true },
    ],
    xAxis: [
      { type: 'category', data: platforms, axisLabel: { color: '#64748b' } },
      { type: 'category', data: platforms, axisLabel: { color: '#64748b' } },
    ],
    yAxis: [
      { type: 'value', name: '互动量', axisLabel: { color: '#64748b' } },
      { type: 'value', name: '占比 %', axisLabel: { color: '#64748b' }, max: 100 },
    ],
    series: [
      {
        name: '互动量',
        type: 'bar',
        data: participation.map((item) => item.engagement),
        itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] },
      },
      ...sentimentSeries,
    ],
  })
}

async function load() {
  error.value = ''
  try {
    data.value = await api.getPlatformComparison(props.caseId)
    // chartEl 在 v-else 分支内：data 赋值后需等 DOM 挂载 ref 才可用，
    // 否则 renderChart 读到 null 直接跳过（图表永远不渲染）。
    await nextTick()
    await renderChart()
  } catch {
    error.value = '平台对比数据加载失败。'
  }
}

onMounted(load)
</script>

<template>
  <div>
    <p v-if="error" class="modal-error">
      {{ error }}
      <button type="button" class="ghost-button" @click="load">重试</button>
    </p>
    <p v-else-if="!data" class="modal-muted">对比数据加载中…</p>

    <template v-else>
      <div ref="chartEl" class="comparison-chart"></div>

      <div v-if="data.insights.length" class="comparison-insights">
        <span class="eyebrow">对比洞察</span>
        <ul>
          <li v-for="(insight, index) in data.insights" :key="index">{{ insight }}</li>
        </ul>
      </div>

      <div v-if="data.common_terms.length" class="comparison-terms">
        <span class="eyebrow">跨平台共现话题词</span>
        <p>
          <span v-for="term in data.common_terms" :key="term.term" class="common-term-chip">
            {{ term.term }}
          </span>
        </p>
      </div>

      <div class="comparison-terms">
        <span class="eyebrow">各平台高频话题词</span>
        <div v-for="entry in data.topic_terms" :key="entry.platform" class="platform-terms-row">
          <strong>{{ PLATFORM_NAMES[entry.platform] || entry.platform }}</strong>
          <span v-for="term in entry.terms" :key="term" class="term-chip">{{ term }}</span>
        </div>
      </div>
    </template>
  </div>
</template>
