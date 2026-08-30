<script setup lang="ts">
// C7: 传播网络工作区的真实图组件（纯渲染，数据由工作区加载）。
// 数据来自 GET /cases/{id}/propagation-graph（PropagationNodeRecord +
// PropagationEdgeRecord + SourcePostRecord join），节点按 post 去重。
// 视觉语义：human_confirmed=true → 确认实线；false → 驳回；未确认 →
// 推断虚线；透明度映射 confidence。节点角色颜色区分 source/burst/hub/bridge。
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { PropagationGraphDTO } from '@/types/api'

export interface PropagationSelection {
  type: 'propagation_node' | 'propagation_edge'
  id: string
}

const props = defineProps<{
  graph: PropagationGraphDTO | null
  loading: boolean
  error: string
}>()
const emit = defineEmits<{ (e: 'select', selection: PropagationSelection): void }>()

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const ROLE_COLORS: Record<string, string> = {
  source: '#ff6a4d',
  burst: '#ffb02e',
  hub: '#00a8d8',
  bridge: '#2f9e6e',
}

const chartElement = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function render() {
  if (!chartElement.value || !props.graph) return
  chart ||= init(chartElement.value)
  const data = props.graph
  const roles = [...new Set(data.nodes.map((node) => node.role))]
  const option: EChartsCoreOption = {
    backgroundColor: 'transparent',
    tooltip: {
      confine: true,
      formatter: (value: unknown) => {
        const params = value as { dataType: string; data: Record<string, unknown> }
        if (params.dataType === 'edge') {
          const confidence = Math.round(Number(params.data.confidence) * 100)
          return `置信度 ${confidence}%<br/>${String(params.data.algorithm_version ?? '')}`
        }
        return `${String(params.data.label ?? '')}<br/>${String(params.data.platform ?? '')}`
      },
    },
    legend: [
      {
        bottom: 0,
        textStyle: { color: '#8f9bb3' },
        data: roles,
      },
    ],
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion: 260, edgeLength: [90, 150], gravity: 0.08 },
        categories: roles.map((role) => ({ name: role })),
        label: {
          show: true,
          position: 'bottom',
          color: '#d8deec',
          fontSize: 11,
          formatter: (value: unknown) => {
            const params = value as { data: { label: string } }
            return params.data.label
          },
        },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 8],
        lineStyle: { curveness: 0.12 },
        data: data.nodes.map((node) => ({
          id: node.post_id,
          name: node.post_id,
          label: node.label,
          platform: node.platform,
          role: node.role,
          score: node.score,
          category: roles.indexOf(node.role),
          symbolSize: 18 + Math.round(node.score * 22),
          itemStyle: {
            color: ROLE_COLORS[node.role] || '#7c8ba5',
            borderColor: 'rgba(255,255,255,.68)',
            borderWidth: 1,
          },
        })),
        links: data.edges.map((edge) => ({
          id: edge.id,
          source: edge.source_post_id,
          target: edge.target_post_id,
          confidence: edge.confidence,
          algorithm_version: edge.algorithm_version,
          lineStyle: {
            // FC1 三态：unreviewed 灰色虚线（算法推断/未复核）；
            // confirmed 绿色实线（人工确认）；rejected 红色实线（人工驳回）。
            type: edge.human_review_state === 'unreviewed' ? 'dashed' : 'solid',
            color:
              edge.human_review_state === 'confirmed'
                ? '#2f9e6e'
                : edge.human_review_state === 'rejected'
                  ? '#c0574f'
                  : '#8f9bb3',
            width: edge.human_review_state === 'confirmed' ? 3 : 1.5,
            opacity: 0.35 + edge.confidence * 0.55,
          },
        })),
      },
    ],
  }
  chart.setOption(option)

  chart.off('click')
  chart.on('click', (params: unknown) => {
    const event = params as { dataType?: string; data?: { id?: string } }
    if (event.dataType === 'node' && event.data?.id) {
      emit('select', { type: 'propagation_node', id: event.data.id })
    } else if (event.dataType === 'edge' && event.data?.id) {
      emit('select', { type: 'propagation_edge', id: event.data.id })
    }
  })
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  // immediate watch 在 setup 期间触发时 DOM 还未挂载，onMounted 补一次渲染
  if (props.graph) render()
  window.addEventListener('resize', resize)
})

watch(
  () => props.graph,
  (value) => {
    if (value) render()
  },
  { deep: true, immediate: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <section class="pgraph" aria-label="传播网络图">
    <div v-if="loading" class="pgraph__state">传播图加载中…</div>
    <div v-else-if="error" class="pgraph__state pgraph__state--error">{{ error }}</div>
    <div v-else-if="!graph || (!graph.nodes.length && !graph.edges.length)" class="pgraph__state">
      暂无传播图数据：传播分析完成后，候选源头与传播关系会显示在这里。
    </div>
    <template v-else>
      <p class="pgraph__note">
        图中节点与关系均为算法候选/推断，不代表已证实结论；确认状态以人工复核为准。
      </p>
      <div ref="chartElement" class="pgraph__canvas"></div>
    </template>
  </section>
</template>

<style scoped>
.pgraph {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 420px;
}

.pgraph__state {
  margin: auto;
  color: var(--text-soft);
  font-size: 13px;
}

.pgraph__state--error {
  color: var(--danger, #c0574f);
}

.pgraph__note {
  margin: 0;
  padding: 8px 12px 0;
  font-size: 11px;
  color: var(--text-soft);
}

.pgraph__canvas {
  flex: 1;
  min-height: 380px;
}
</style>
