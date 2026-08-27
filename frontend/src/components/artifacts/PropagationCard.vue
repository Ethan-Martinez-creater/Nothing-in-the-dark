<script setup lang="ts">
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '@/services/api'
import type { PropagationData } from '@/types/api'

const props = defineProps<{ data: PropagationData; caseId: string }>()

// edge_id -> 人工确认结果（true 确认 / false 反驳 / 未定义未操作）
const confirmations = ref<Record<string, boolean>>({})
const confirming = ref<Record<string, boolean>>({})
const confirmError = ref('')

async function confirmEdge(edgeId: string, confirmed: boolean) {
  if (!edgeId || confirming.value[edgeId]) return
  confirming.value = { ...confirming.value, [edgeId]: true }
  confirmError.value = ''
  try {
    await api.confirmPropagationEdge(props.caseId, edgeId, confirmed, '')
    confirmations.value = { ...confirmations.value, [edgeId]: confirmed }
  } catch {
    confirmError.value = '确认提交失败，请重试。'
  } finally {
    confirming.value = { ...confirming.value, [edgeId]: false }
  }
}

const chartElement = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const colors: Record<string, string> = {
  weibo: '#ff6a4d',
  bilibili: '#00a8d8',
  tieba: '#4d7cff',
  zhihu: '#2f9e6e',
  douyin: '#b06ae0',
}

const platforms = [...new Set(props.data.nodes.map((node) => node.platform))]

// 刷新后恢复已确认/驳回状态：后端持久化 human_confirmed，前端本地态
// 会因重载丢失（2026-08-08 冒烟 BUG-3）。
const persistedState = ref<Record<string, boolean>>({})

async function restoreConfirmations() {
  try {
    const states = await api.listPropagationEdgeStates(props.caseId)
    persistedState.value = Object.fromEntries(
      states.map((state) => [state.id, state.human_confirmed]),
    )
  } catch {
    // 后端不可用时保持空白状态，确认按钮仍可操作
  }
}
void restoreConfirmations()

function render() {
  if (!chartElement.value) return
  chart ||= init(chartElement.value)
  const option: EChartsCoreOption = {
    backgroundColor: 'transparent',
    tooltip: {
      confine: true,
      formatter: (value: unknown) => {
        const params = value as { dataType: string; data: Record<string, unknown> }
        if (params.dataType === 'edge') {
          const relation = params.data.relation === 'observed' ? '明确关系' : '推断关系'
          const reasons = (params.data.reasons as string[] | undefined) || []
          const reasonText = reasons.length
            ? `<br/>特征：${reasons.join('；')}`
            : ''
          return `${relation}<br/>置信度 ${Math.round(Number(params.data.confidence) * 100)}%${reasonText}`
        }
        return `${params.data.name}<br/>${params.data.platform}`
      },
    },
    legend: [
      {
        bottom: 0,
        textStyle: { color: '#8f9bb3' },
        data: platforms,
      },
    ],
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: {
          repulsion: 260,
          edgeLength: [90, 150],
          gravity: 0.08,
        },
        categories: platforms.map((platform) => ({ name: platform })),
        label: {
          show: true,
          position: 'bottom',
          color: '#d8deec',
          fontSize: 11,
        },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 8],
        lineStyle: {
          color: 'source',
          curveness: 0.12,
          opacity: 0.62,
        },
        data: props.data.nodes.map((node) => ({
          id: node.id,
          name: node.id,
          platform: node.platform,
          category: platforms.indexOf(node.platform),
          symbolSize: 30,
          itemStyle: {
            color: colors[node.platform] || '#7c8ba5',
            borderColor: 'rgba(255,255,255,.68)',
            borderWidth: 1,
          },
        })),
        links: props.data.edges.map((edge) => ({
          source: edge.source,
          target: edge.target,
          relation: edge.relation,
          confidence: edge.confidence,
          reasons: edge.reasons,
          lineStyle: {
            type: edge.relation === 'observed' ? 'solid' : 'dashed',
            width: edge.relation === 'observed' ? 2.5 : 1.5,
            opacity: edge.confidence,
          },
        })),
      },
    ],
  }
  chart.setOption(option)
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})

watch(() => props.data, render, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
})
</script>

<template>
  <section class="panel artifact-panel">
    <div class="panel-heading">
      <div>
        <span class="eyebrow">PROPAGATION GRAPH</span>
        <h3>跨平台传播链路</h3>
      </div>
      <div class="graph-legend">
        <span><i class="solid-line"></i>明确关系</span>
        <span><i class="dashed-line"></i>推断关系</span>
      </div>
    </div>
    <div ref="chartElement" class="graph-canvas"></div>
    <div v-if="data.origin_candidates.length" class="origin-callout">
      <span>源头候选</span>
      <div v-for="candidate in data.origin_candidates" :key="candidate.node_id" class="origin-candidate">
        <strong>{{ candidate.node_id }}</strong>
        <em>{{ Math.round(candidate.confidence * 100) }}%</em>
        <p>{{ candidate.reason }}</p>
      </div>
    </div>
    <div class="edge-confirm-section">
      <span class="eyebrow">EDGE HUMAN CONFIRMATION</span>
      <p v-if="confirmError" class="edge-confirm-error">{{ confirmError }}</p>
      <ul v-if="data.edges.length" class="edge-confirm-list">
        <li v-for="edge in data.edges" :key="`${edge.source}-${edge.target}`" class="edge-confirm-item">
          <div class="edge-confirm-meta">
            <span class="edge-relation" :class="`relation-${edge.relation}`">
              {{ edge.relation === 'observed' ? '明确' : '推断' }}
            </span>
            <strong>{{ edge.source }} → {{ edge.target }}</strong>
            <em>{{ Math.round(edge.confidence * 100) }}%</em>
          </div>
          <p v-if="edge.reasons.length" class="edge-reasons">{{ edge.reasons.join('；') }}</p>
          <div v-if="edge.edge_id" class="edge-confirm-actions">
            <template v-if="confirmations[edge.edge_id] !== undefined || persistedState[edge.edge_id] !== undefined">
              <span class="edge-confirm-done">
                {{ (confirmations[edge.edge_id] ?? persistedState[edge.edge_id]) ? '✓ 已确认' : '✗ 已驳回' }}
              </span>
            </template>
            <template v-else>
              <button
                type="button"
                class="ghost-button edge-confirm-yes"
                :disabled="!!confirming[edge.edge_id]"
                @click="confirmEdge(edge.edge_id!, true)"
              >
                确认
              </button>
              <button
                type="button"
                class="ghost-button danger"
                :disabled="!!confirming[edge.edge_id]"
                @click="confirmEdge(edge.edge_id!, false)"
              >
                驳回
              </button>
            </template>
          </div>
          <span v-else class="edge-no-id">旧数据：无边 ID，无法人工确认</span>
        </li>
      </ul>
      <p v-else class="edge-confirm-empty">无传播边</p>
    </div>
  </section>
</template>
