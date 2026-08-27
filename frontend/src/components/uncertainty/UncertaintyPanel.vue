<script setup lang="ts">
import { LoaderCircle, Plus, Scale, X } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { AlternativeHypothesis, QualityAssessment } from '@/types/api'

const props = defineProps<{
  caseId: string
  open: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const loading = ref(true)
const error = ref('')
const assessments = ref<QualityAssessment[]>([])
const hypotheses = ref<AlternativeHypothesis[]>([])
const showCreate = ref(false)
const creating = ref(false)
const newStatement = ref('')
const newPrediction = ref('')
const actionError = ref('')

const LEVEL_LABELS: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
  insufficient: '不足',
}

const DIMENSION_LABELS: Record<string, string> = {
  coverage: '覆盖',
  sampling_bias: '采样偏差',
  measurement_uncertainty: '测量不确定',
  model_uncertainty: '模型不确定',
  evidence_strength: '证据强度',
  robustness: '稳健性',
  alternative_explanations: '替代解释',
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [assessmentList, hypothesisList] = await Promise.all([
      api.listQualityAssessments(props.caseId),
      api.listHypotheses(props.caseId),
    ])
    assessments.value = assessmentList
    hypotheses.value = hypothesisList
  } catch {
    error.value = '加载质量数据失败，请重试。'
  } finally {
    loading.value = false
  }
}

async function createHypothesis() {
  if (!newStatement.value.trim() || creating.value) return
  creating.value = true
  actionError.value = ''
  try {
    await api.createHypothesis(props.caseId, {
      statement: newStatement.value.trim(),
      prediction: newPrediction.value.trim(),
    })
    newStatement.value = ''
    newPrediction.value = ''
    showCreate.value = false
    await load()
  } catch {
    actionError.value = '创建替代假设失败。'
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <aside v-if="open" class="uncertainty-panel" aria-label="不确定性与偏差面板">
    <header class="panel-header">
      <div class="panel-title">
        <Scale :size="16" />
        <span>不确定性与偏差</span>
      </div>
      <button type="button" class="icon-button" aria-label="关闭" @click="emit('close')">
        <X :size="16" />
      </button>
    </header>

    <div class="panel-body">
      <div v-if="loading" class="state">
        <LoaderCircle :size="18" class="spin" />
        <span>加载中…</span>
      </div>
      <div v-else-if="error" class="state error">
        <span>{{ error }}</span>
        <button type="button" class="ghost-button" @click="load">重试</button>
      </div>
      <template v-else>
        <div v-if="actionError" class="action-error">{{ actionError }}</div>

        <section class="section">
          <h3>质量维度</h3>
          <ul v-if="assessments.length" class="assessment-list">
            <li v-for="assessment in assessments" :key="assessment.id" class="assessment-item">
              <span class="dimension">{{ DIMENSION_LABELS[assessment.dimension] || assessment.dimension }}</span>
              <span class="level-badge" :class="assessment.level">
                {{ LEVEL_LABELS[assessment.level] || assessment.level }}
              </span>
            </li>
          </ul>
          <div v-else class="state">
            <span>暂无质量评估。</span>
          </div>
        </section>

        <section class="section">
          <div class="section-head">
            <h3>替代解释</h3>
            <button type="button" class="ghost-button" @click="showCreate = !showCreate">
              <Plus :size="14" /> 新建
            </button>
          </div>
          <form v-if="showCreate" class="create-form" @submit.prevent="createHypothesis">
            <label>
              陈述
              <textarea v-model="newStatement" rows="2" placeholder="可证伪的替代解释" />
            </label>
            <label>
              可验证预测
              <input v-model="newPrediction" type="text" placeholder="若该假设成立，应观察到…" />
            </label>
            <button type="submit" class="primary-button" :disabled="creating">
              {{ creating ? '创建中…' : '创建' }}
            </button>
          </form>
          <ul v-if="hypotheses.length" class="hypothesis-list">
            <li v-for="hypothesis in hypotheses" :key="hypothesis.id" class="hypothesis-item">
              <p class="statement">{{ hypothesis.statement }}</p>
              <p v-if="hypothesis.prediction" class="prediction">{{ hypothesis.prediction }}</p>
              <div v-if="hypothesis.supporting_evidence.length || hypothesis.opposing_evidence.length" class="evidence">
                <span v-if="hypothesis.supporting_evidence.length">
                  支持 {{ hypothesis.supporting_evidence.length }}
                </span>
                <span v-if="hypothesis.opposing_evidence.length">
                  反对 {{ hypothesis.opposing_evidence.length }}
                </span>
              </div>
            </li>
          </ul>
          <div v-else class="state">
            <span>暂无可证伪的替代解释。数据不足时明确拒绝强结论。</span>
          </div>
        </section>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.uncertainty-panel {
  display: flex;
  flex-direction: column;
  width: 340px;
  border-left: 1px solid var(--color-border, #e2e8f0);
  background: var(--color-bg, #fff);
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border, #e2e8f0);
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.icon-button {
  display: inline-flex;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--color-muted, #64748b);
}
.panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
}
.state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 0;
  color: var(--color-muted, #64748b);
  text-align: center;
}
.state.error {
  color: var(--color-danger, #dc2626);
}
.action-error {
  margin-bottom: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 13px;
}
.section {
  margin-bottom: 16px;
}
.section h3 {
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 8px;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.section-head h3 {
  margin: 0;
}
.ghost-button,
.primary-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}
.ghost-button {
  border: 1px solid var(--color-border, #e2e8f0);
  background: transparent;
}
.primary-button {
  border: none;
  background: var(--color-primary, #2563eb);
  color: #fff;
}
.primary-button:disabled {
  opacity: 0.5;
}
.assessment-list,
.hypothesis-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.assessment-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--color-border, #e2e8f0);
}
.dimension {
  font-size: 13px;
}
.level-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: var(--color-muted, #64748b);
}
.level-badge.high {
  background: #dcfce7;
  color: #166534;
}
.level-badge.medium {
  background: #fef9c3;
  color: #854d0e;
}
.level-badge.low {
  background: #fee2e2;
  color: #991b1b;
}
.level-badge.insufficient {
  background: #fee2e2;
  color: #991b1b;
}
.create-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
  padding: 10px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
}
.create-form label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.create-form textarea,
.create-form input {
  padding: 6px 8px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 6px;
  font-size: 13px;
}
.hypothesis-item {
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 8px;
}
.statement {
  margin: 0 0 4px;
  font-size: 13px;
}
.prediction {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.evidence {
  display: flex;
  gap: 10px;
  font-size: 12px;
  color: var(--color-muted, #64748b);
}
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
