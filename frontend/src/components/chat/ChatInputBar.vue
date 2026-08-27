<script setup lang="ts">
import { MessageCircleQuestion, Navigation, Send, Sparkles, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import type { AgentRun, ApprovalInfo } from '@/types/api'

import ApprovalCard from './ApprovalCard.vue'

// 审批队列首卡：全部待审批项按产生顺序依次展示，决定一个即消失并浮现下一个。
export interface ApprovalTarget {
  runId: string
  run: AgentRun
  approval: ApprovalInfo
  queueCount: number
}

const props = withDefaults(
  defineProps<{
    sending: boolean
    realCrawl: boolean
    llmConfigured?: boolean
    steerTarget?: AgentRun | null
    askTarget?: { artifactId: string } | null
    approvalTarget?: ApprovalTarget | null
  }>(),
  { steerTarget: null, askTarget: null, approvalTarget: null, llmConfigured: true },
)

const emit = defineEmits<{
  send: [content: string, approveCrawl: boolean, artifactId?: string]
  quick: []
  steer: [runId: string, content: string]
  cancelSteer: []
  cancelAsk: []
  decide: [runId: string, approvalId: string, decision: boolean, note: string]
}>()

const content = ref('')
const approveCrawl = ref(false)

const steering = computed(() => props.steerTarget !== null)
const asking = computed(() => props.askTarget !== null)

function submit() {
  const text = content.value.trim()
  if (!text || props.sending || props.llmConfigured === false) return
  if (steering.value && props.steerTarget) {
    emit('steer', props.steerTarget.id, text)
  } else {
    emit('send', text, approveCrawl.value, props.askTarget?.artifactId)
  }
  content.value = ''
}

function quickAnalyze() {
  if (props.llmConfigured === false) return
  emit('quick')
}

// 空状态引导的示例 prompt 填入输入框（CaseWorkspaceView 通过 ref 调用）。
function fill(text: string) {
  content.value = text
}

defineExpose({ fill })
</script>

<template>
  <div class="chat-input-bar">
    <!-- 审批队列：一次只显示队首一个，决定后自动切换到下一个 -->
    <div v-if="approvalTarget" class="approval-queue">
      <ApprovalCard
        :key="approvalTarget.approval.id"
        :approval="approvalTarget.approval"
        :run-id="approvalTarget.runId"
        :run-status="approvalTarget.run.status"
        @decide="(approvalId, decision, note) => approvalTarget && emit('decide', approvalTarget.runId, approvalId, decision, note)"
      />
      <span v-if="approvalTarget.queueCount > 1" class="approval-queue-count">
        队列中还有 {{ approvalTarget.queueCount - 1 }} 个待审批
      </span>
    </div>

    <div v-if="steering" class="steer-banner">
      <Navigation :size="14" />
      <span>
        运行指令模式：正在向运行中任务发送指令
        <em>{{ steerTarget?.objective.slice(0, 24) }}</em>
      </span>
      <button type="button" class="icon-button" title="退出指令模式" @click="emit('cancelSteer')">
        <X :size="14" />
      </button>
    </div>
    <div v-else-if="asking" class="steer-banner ask-banner">
      <MessageCircleQuestion :size="14" />
      <span>追问模式：新消息将绑定该成果，交由 Agent 解释</span>
      <button type="button" class="icon-button" title="退出追问模式" @click="emit('cancelAsk')">
        <X :size="14" />
      </button>
    </div>
    <div v-if="llmConfigured === false" class="llm-missing-banner">
      未配置大模型。在 <code>backend/.env</code> 填写
      <code>LLM_API_KEY</code> 与 <code>LLM_FAST_MODEL</code> 后重启后端，
      即可在本会话继续发消息，不会回退到固定模板结论。
    </div>
    <button
      type="button"
      class="quick-button"
      :disabled="sending || steering || llmConfigured === false"
      :title="steering ? '运行指令模式下不可发起新分析' : sending ? '分析执行中，请稍候' : '一键发起完整舆情分析（采集 + 主张抽取 + 传播复原 + 事实核查）'"
      @click="quickAnalyze"
    >
      <Sparkles :size="15" />
      快速完整分析
    </button>
    <div class="input-row">
      <textarea
        v-model="content"
        class="chat-textarea"
        rows="2"
        :placeholder="steering ? '输入对运行中任务的指令，将在下一轮模型上下文生效' : '输入分析指令或追问，例如：请分析该案例的传播源头'"
        :disabled="sending || llmConfigured === false"
        @keydown.enter.exact.prevent="submit"
      />
      <button type="button" class="primary-button send-button" :disabled="sending || llmConfigured === false || !content.trim()" @click="submit">
        <Send :size="16" />
        {{ sending ? '执行中…' : steering ? '发指令' : '发送' }}
      </button>
    </div>
    <label v-if="realCrawl && !steering" class="approve-toggle" title="勾选后将允许 Agent 调用真实平台采集（会打开登录浏览器并消耗真实数据）；演示模式无需勾选">
      <input v-model="approveCrawl" type="checkbox" :disabled="sending" />
      <span>批准真实平台采集（将打开登录浏览器，消耗真实数据）</span>
    </label>
  </div>
</template>
