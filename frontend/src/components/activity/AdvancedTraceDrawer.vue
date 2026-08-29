<script setup lang="ts">
// Optimization V2 (M2.5)：高级技术轨迹 Drawer。
// 复用 /runs/{id}/trace，展示 model/tool/approval/cost/raw events；不新建后端 API。
import { onMounted, ref } from 'vue'

import { X } from 'lucide-vue-next'

import { api } from '@/services/api'
import type { RunTrace } from '@/types/api'

const props = defineProps<{ runId: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const trace = ref<RunTrace | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    trace.value = await api.getRunTrace(props.runId)
  } catch {
    error.value = '轨迹加载失败。'
  } finally {
    loading.value = false
  }
})

function formatCost(value: number): string {
  return `¥${value.toFixed(4)}`
}
</script>

<template>
  <div class="trace">
    <header class="trace__header">
      <strong>技术轨迹</strong>
      <span class="trace__run">{{ runId.slice(0, 8).toUpperCase() }}</span>
      <button type="button" class="trace__close" aria-label="关闭" @click="emit('close')">
        <X :size="16" />
      </button>
    </header>

    <p v-if="loading" class="trace__hint">正在加载…</p>
    <p v-else-if="error" class="trace__error">{{ error }}</p>

    <div v-else-if="trace" class="trace__body">
      <div class="trace__summary">
        <span>状态：{{ trace.run.status }}</span>
        <span>模型调用：{{ trace.model_calls.length }}</span>
        <span>工具调用：{{ trace.tool_calls.length }}</span>
        <span>总成本：{{ formatCost(trace.total_cost ?? 0) }}</span>
      </div>

      <section class="trace__section">
        <h3>模型调用</h3>
        <ul>
          <li v-for="call in trace.model_calls" :key="call.id">
            <span>{{ call.model }}（{{ call.route }}）</span>
            <span>{{ call.status }} · in {{ call.input_tokens }} / out {{ call.output_tokens }}</span>
            <span>{{ formatCost(call.estimated_cost) }}</span>
          </li>
        </ul>
      </section>

      <section class="trace__section">
        <h3>工具调用</h3>
        <ul>
          <li v-for="call in trace.tool_calls" :key="call.id">
            <span>{{ call.tool_name }}</span>
            <span>{{ call.status }} · {{ call.duration_ms }}ms</span>
            <span v-if="call.retry_count > 0">重试 {{ call.retry_count }}</span>
            <span>{{ formatCost(call.estimated_cost) }}</span>
          </li>
        </ul>
      </section>

      <section class="trace__section">
        <h3>审批记录</h3>
        <ul>
          <li v-for="approval in trace.approvals" :key="approval.id">
            <span>{{ approval.action }}</span>
            <span>{{ approval.status }}</span>
            <span class="trace__reason">{{ approval.reason }}</span>
          </li>
        </ul>
      </section>

      <section class="trace__section">
        <h3>原始事件</h3>
        <ul class="trace__events">
          <li v-for="event in trace.events" :key="event.id">
            <code>{{ event.event_type }}</code>
            <span class="trace__reason">{{ event.tool ?? event.agent }}</span>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.trace {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  background: var(--surface);
  border-left: 1px solid var(--border);
}

.trace__header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
}

.trace__run {
  font-family: monospace;
  font-size: 12px;
  color: var(--text-soft);
}

.trace__close {
  margin-left: auto;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
}

.trace__hint,
.trace__error {
  padding: 16px;
  font-size: 13px;
  color: var(--text-muted);
}

.trace__error {
  color: var(--red);
}

.trace__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.trace__summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--text-muted);
}

.trace__section h3 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.trace__section ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.trace__section li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--surface-muted);
  font-size: 12px;
  color: var(--text-muted);
}

.trace__section li > span:first-child {
  color: var(--text);
  font-weight: 500;
}

.trace__reason {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace__events code {
  font-size: 11px;
}
</style>
