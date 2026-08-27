<script setup lang="ts">
import { ShieldAlert } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import type { ApprovalInfo, RunStatus } from '@/types/api'

const props = defineProps<{
  approval: ApprovalInfo
  runId: string
  runStatus: RunStatus
}>()

const emit = defineEmits<{
  decide: [approvalId: string, decision: boolean, note: string]
}>()

const note = ref('')
const decided = ref<'approved' | 'rejected' | null>(null)
const busy = ref(false)

// action 本地化：后端直出英文工具名，用户应看到中文操作含义。
const actionLabels: Record<string, string> = {
  collect_social_posts: '采集社交平台数据',
  crawl_real_platform: '真实平台采集',
  budget_exceeded: '突破模型费用预算',
  high_cost_tool: '高成本工具调用',
  write_database: '写入数据库',
  external_read: '外部数据读取',
  search_web: '联网搜索',
}

const actionText = computed(
  () => actionLabels[props.approval.action] || props.approval.action,
)

async function decide(decision: boolean) {
  if (busy.value) return
  busy.value = true
  try {
    await emit('decide', props.approval.id, decision, note.value.trim())
    decided.value = decision ? 'approved' : 'rejected'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="approval-card" :class="{ 'is-decided': decided }">
    <div class="approval-head">
      <ShieldAlert :size="16" />
      <span>需要审批：{{ actionText }}</span>
    </div>
    <p class="approval-reason">{{ approval.reason }}</p>
    <div v-if="!decided" class="approval-controls">
      <input
        v-model="note"
        class="approval-note"
        placeholder="审批备注（可选）"
        :disabled="busy || runStatus !== 'waiting_approval'"
      />
      <button
        type="button"
        class="approval-approve"
        :disabled="busy || runStatus !== 'waiting_approval'"
        @click="decide(true)"
      >
        批准
      </button>
      <button
        type="button"
        class="approval-reject"
        :disabled="busy || runStatus !== 'waiting_approval'"
        @click="decide(false)"
      >
        拒绝
      </button>
    </div>
    <p v-else class="approval-done">
      {{ decided === 'approved' ? '已批准，Agent 将从原 Tool Call 继续执行' : '已拒绝，Agent 将走替代路径' }}
    </p>
  </div>
</template>
