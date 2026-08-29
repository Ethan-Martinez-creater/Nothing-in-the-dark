<script setup lang="ts">
// C7: 传播图右侧详情面板。
// Edge：relation / confidence / algorithm version / feature scores /
// evidence IDs / 人工确认状态 + 确认/驳回（复用既有 confirmation API）。
// Node：roles / score / platform / label / excerpt / 发布时间 —— 只展示
// 真实持久化数据，candidate origin 不描述为事实。
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/services/api'
import type {
  PropagationGraphDTO,
  PropagationGraphEdgeDTO,
  PropagationGraphNodeDTO,
} from '@/types/api'
import type { PropagationSelection } from './PropagationGraph.vue'

const props = defineProps<{
  caseId: string
  graph: PropagationGraphDTO | null
  selection: PropagationSelection | null
}>()

const emit = defineEmits<{ (e: 'refresh'): void }>()

const router = useRouter()
const confirming = ref(false)
const confirmError = ref('')

const selectedEdge = computed<PropagationGraphEdgeDTO | null>(() => {
  if (props.selection?.type !== 'propagation_edge' || !props.graph) return null
  return props.graph.edges.find((edge) => edge.id === props.selection?.id) ?? null
})

const selectedNode = computed<PropagationGraphNodeDTO | null>(() => {
  if (props.selection?.type !== 'propagation_node' || !props.graph) return null
  return props.graph.nodes.find((node) => node.post_id === props.selection?.id) ?? null
})

const featureScoreEntries = computed(() => {
  return Object.entries(selectedEdge.value?.feature_scores ?? {})
})

async function confirmEdge(confirmed: boolean) {
  const edge = selectedEdge.value
  if (!edge || confirming.value) return
  confirming.value = true
  confirmError.value = ''
  try {
    await api.confirmPropagationEdge(props.caseId, edge.id, confirmed, '')
    emit('refresh')
  } catch {
    confirmError.value = '确认提交失败，请重试。'
  } finally {
    confirming.value = false
  }
}

function openEvidence() {
  void router.push(`/investigations/${props.caseId}/evidence`)
}

const ROLE_LABELS: Record<string, string> = {
  source: '源头候选',
  burst: '突发候选',
  hub: '枢纽候选',
  bridge: '桥接候选',
}

function roleLabel(role: string) {
  return ROLE_LABELS[role] ?? role
}
</script>

<template>
  <aside class="pdet" aria-label="传播详情">
    <div v-if="!selection" class="pdet__empty">
      在左侧图中选择节点或传播边查看详情。
    </div>

    <!-- Edge detail -->
    <template v-else-if="selectedEdge">
      <h3 class="pdet__title">传播边详情</h3>
      <p class="pdet__badge" :class="{ 'pdet__badge--ok': selectedEdge.human_confirmed }">
        {{ selectedEdge.human_confirmed ? '人工已确认' : '人工未确认（推断关系）' }}
      </p>
      <dl class="pdet__fields">
        <div><dt>关系</dt><dd>{{ selectedEdge.relation }}</dd></div>
        <div><dt>置信度</dt><dd>{{ Math.round(selectedEdge.confidence * 100) }}%</dd></div>
        <div><dt>算法版本</dt><dd>{{ selectedEdge.algorithm_version }}</dd></div>
        <div v-for="[key, value] in featureScoreEntries" :key="key">
          <dt>特征 · {{ key }}</dt><dd>{{ value }}</dd>
        </div>
      </dl>
      <div class="pdet__evidence">
        <span class="pdet__label">证据</span>
        <template v-if="selectedEdge.evidence_ids.length">
          <ul class="pdet__chips">
            <li v-for="evidenceId in selectedEdge.evidence_ids" :key="evidenceId">
              {{ evidenceId }}
            </li>
          </ul>
          <button type="button" class="ghost-button" @click="openEvidence">
            前往 Evidence 工作区
          </button>
        </template>
        <span v-else class="pdet__muted">该边未关联 Evidence</span>
      </div>
      <p v-if="confirmError" class="pdet__error">{{ confirmError }}</p>
      <div class="pdet__actions">
        <button
          type="button"
          class="ghost-button"
          :disabled="confirming"
          @click="confirmEdge(true)"
        >
          确认关系成立
        </button>
        <button
          type="button"
          class="ghost-button danger"
          :disabled="confirming"
          @click="confirmEdge(false)"
        >
          驳回该关系
        </button>
      </div>
    </template>

    <!-- Node detail -->
    <template v-else-if="selectedNode">
      <h3 class="pdet__title">节点详情</h3>
      <p class="pdet__badge">算法候选 · 非已证实结论</p>
      <dl class="pdet__fields">
        <div><dt>平台</dt><dd>{{ selectedNode.platform }}</dd></div>
        <div>
          <dt>候选角色</dt>
          <dd>{{ selectedNode.roles.map(roleLabel).join('、') }}</dd>
        </div>
        <div><dt>主角色得分</dt><dd>{{ selectedNode.score.toFixed(2) }}</dd></div>
        <div><dt>算法版本</dt><dd>{{ selectedNode.algorithm_version }}</dd></div>
        <div v-if="selectedNode.author_name">
          <dt>作者</dt><dd>{{ selectedNode.author_name }}</dd>
        </div>
        <div v-if="selectedNode.published_at">
          <dt>发布时间</dt><dd>{{ selectedNode.published_at }}</dd>
        </div>
      </dl>
      <div v-if="selectedNode.excerpt" class="pdet__evidence">
        <span class="pdet__label">内容摘录</span>
        <p class="pdet__excerpt">{{ selectedNode.excerpt }}</p>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.pdet {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-left: 1px solid var(--border);
  overflow-y: auto;
}

.pdet__empty {
  margin: auto;
  color: var(--text-soft);
  font-size: 12px;
}

.pdet__title {
  margin: 0;
  font-size: 14px;
}

.pdet__badge {
  margin: 0;
  padding: 3px 10px;
  align-self: flex-start;
  border-radius: 999px;
  font-size: 11px;
  background: rgba(143, 155, 179, 0.16);
  color: var(--text-muted);
}

.pdet__badge--ok {
  background: rgba(47, 158, 110, 0.18);
  color: #63c99a;
}

.pdet__fields {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pdet__fields div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.pdet__fields dt {
  color: var(--text-soft);
}

.pdet__fields dd {
  margin: 0;
  text-align: right;
  color: var(--text);
  word-break: break-all;
}

.pdet__label {
  font-size: 11px;
  color: var(--text-soft);
}

.pdet__chips {
  list-style: none;
  margin: 6px 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pdet__chips li {
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--surface-strong, rgba(255, 255, 255, 0.06));
  font-size: 11px;
  font-family: ui-monospace, monospace;
}

.pdet__excerpt {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.6;
}

.pdet__muted {
  font-size: 12px;
  color: var(--text-soft);
}

.pdet__error {
  margin: 0;
  color: var(--danger, #c0574f);
  font-size: 12px;
}

.pdet__actions {
  display: flex;
  gap: 8px;
}
</style>
