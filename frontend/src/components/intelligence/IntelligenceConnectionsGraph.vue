<script setup lang="ts">
// V3 §45：全局 Connections case-to-case 关系图（ECharts graph）。
// 视觉语义（§45）：observed → 实线；candidate → 虚线。节点为 Case，
// 边为 Cross-Investigation Link（每对 + 关系类型 + 版本一条聚合）。
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { IntelligenceConnection } from '@/services/api/intelligence'

const props = defineProps<{
  connections: IntelligenceConnection[]
  caseTitles: Record<string, string>
  loading: boolean
}>()

const emit = defineEmits<{ (e: 'select', connectionId: string): void }>()

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const RELATION_COLORS: Record<string, string> = {
  shared_actor: '#00a8d8',
  shared_post: '#2f9e6e',
  shared_media: '#ffb02e',
  shared_content: '#a06bd8',
}

const chartElement = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function caseLabel(caseId: string): string {
  return props.caseTitles[caseId] ?? caseId.slice(0, 8)
}

function render() {
  if (!chartElement.value || props.connections.length === 0) return
  chart ||= init(chartElement.value)

  const caseIds = [
    ...new Set(props.connections.flatMap((link) => [link.left_case_id, link.right_case_id])),
  ]
  const relationTypes = [
    ...new Set(props.connections.map((link) => link.relation_type)),
  ]

  const option: EChartsCoreOption = {
    backgroundColor: 'transparent',
    tooltip: {
      confine: true,
      formatter: (value: unknown) => {
        const params = value as { dataType: string; data: Record<string, unknown> }
        if (params.dataType === 'edge') {
          const score = params.data.score
          return `${String(params.data.relation_type)}<br/>status: ${String(params.data.status)}${
            score == null ? '' : `<br/>score: ${Number(score).toFixed(2)}`
          }`
        }
        return String(params.data.label ?? '')
      },
    },
    legend: [
      {
        bottom: 0,
        textStyle: { color: '#8f9bb3' },
        data: relationTypes,
      },
    ],
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion: 300, edgeLength: [110, 190], gravity: 0.1 },
        categories: relationTypes.map((relation) => ({ name: relation })),
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
        edgeSymbol: ['none', 'none'],
        data: caseIds.map((caseId) => ({
          id: caseId,
          name: caseId,
          label: caseLabel(caseId),
          symbolSize: 26,
          itemStyle: {
            color: '#1f2a3d',
            borderColor: 'rgba(255,255,255,.68)',
            borderWidth: 1,
          },
        })),
        links: props.connections.map((link) => ({
          id: link.id,
          source: link.left_case_id,
          target: link.right_case_id,
          relation_type: link.relation_type,
          status: link.status,
          score: link.score,
          category: relationTypes.indexOf(link.relation_type),
          lineStyle: {
            // §45：observed 实线（已确认证据）；candidate 虚线（待复核推断）。
            type: link.status === 'observed' ? 'solid' : 'dashed',
            color: RELATION_COLORS[link.relation_type] || '#8f9bb3',
            width: link.status === 'observed' ? 2.5 : 1.5,
            opacity: 0.75,
          },
        })),
      },
    ],
  }
  chart.setOption(option)

  chart.off('click')
  chart.on('click', (params: unknown) => {
    const event = params as { dataType?: string; data?: { id?: string } }
    if (event.dataType === 'edge' && event.data?.id) {
      emit('select', event.data.id)
    } else if (event.dataType === 'node' && event.data?.id) {
      emit('select', `case:${event.data.id}`)
    }
  })
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})

watch(
  () => [props.connections, props.caseTitles],
  () => render(),
  { deep: true },
)
</script>

<template>
  <div v-show="!loading && connections.length" ref="chartElement" class="iconn-graph" aria-label="Case 关系图"></div>
  <p v-if="loading" class="iconn-graph__hint">正在加载关系…</p>
  <p v-else-if="connections.length === 0" class="iconn-graph__hint">
    尚无跨调查关联 — 多个调查产生共享证据后自动出现。
  </p>
</template>

<style scoped>
.iconn-graph {
  width: 100%;
  height: 100%;
  min-height: 380px;
}

.iconn-graph__hint {
  margin: 0;
  padding: 24px 0;
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
}
</style>
