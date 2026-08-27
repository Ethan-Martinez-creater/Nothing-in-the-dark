<script setup lang="ts">
import { FolderKanban, MessageCircleQuestion, Navigation, Sparkles, FileText } from 'lucide-vue-next'
import { nextTick, onMounted, ref, watch } from 'vue'

import type { ChatItem } from '@/types/api'

import AgentBubble from './AgentBubble.vue'
import RunBubble from './RunBubble.vue'
import UserBubble from './UserBubble.vue'

const props = defineProps<{
  items: ChatItem[]
  caseId?: string
  /**
   * 显示欢迎引导卡（空状态）。由父组件计算：案例从未产生任何 run 时为
   * true——创建案例会自动生成一条主题 user turn，items 不会为空，
   * 不能直接用 items.length 判断「首次使用」。
   */
  guide?: boolean
}>()

const emit = defineEmits<{
  cancel: [runId: string]
  resume: [runId: string]
  loadTrace: [runId: string]
  retryArtifacts: [runId: string]
  askArtifact: [artifactId: string]
  quick: []
  openEvidence: []
  fillInput: [content: string]
  enterSteer: [runId: string]
}>()

// 空状态引导卡片：纯展示（未开启对话时的功能介绍），不可点击；
// 实际功能入口在输入区「快速完整分析」与工作台顶部按钮。
const guideCards = [
  {
    key: 'quick',
    icon: Sparkles,
    title: '快速完整分析',
    desc: '一键发起完整舆情分析：采集、主张抽取、传播复原与事实核查',
  },
  {
    key: 'evidence',
    icon: FileText,
    title: '查看案例证据',
    desc: '按主张分组浏览证据，支持/反驳/背景一目了然',
  },
  {
    key: 'steer',
    icon: Navigation,
    title: '指挥运行中任务',
    desc: '分析进行时，可向 Agent 发送指令影响后续决策',
  },
  {
    key: 'ask',
    icon: MessageCircleQuestion,
    title: '追问分析成果',
    desc: '对报告、核查卡等成果点「追问此成果」继续深挖',
  },
]

const examplePrompts = ['聚焦传播源头与传播阶段', '核查辟谣时间线', '生成完整分析报告']

const scroller = ref<HTMLDivElement | null>(null)

async function scrollToBottom(force = false) {
  await nextTick()
  const el = scroller.value
  if (!el) return
  // 用户上滚回看历史时不强制贴底（距底 > 120px 视为回看）；
  // jsdom 无真实布局尺寸，scrollHeight/scrollTop 均为 0，自然满足贴底。
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
  if (!force && !nearBottom) return
  try {
    el.scrollTo({ top: el.scrollHeight })
  } catch {
    /* no-op */
  }
}

onMounted(() => scrollToBottom(true))

// 只在新对话回合出现时强制贴底；运行中增量（deep 变化）由贴底守卫接管。
watch(
  () => props.items
    .map((item) => (item.type === 'run' ? item.run.id : item.type === 'turn' ? item.turn.id : 'orphan'))
    .join(','),
  () => scrollToBottom(false),
)

// 列表项稳定 key：turn/run 的持久 id（避免重建时组件状态错位导致闪动）。
function itemKey(item: ChatItem, index: number): string {
  if (item.type === 'run') return `run:${item.run.id}`
  if (item.type === 'turn') return `turn:${item.turn.id}`
  return `orphan:${index}`
}
</script>

<template>
  <div ref="scroller" class="chat-thread">
    <div v-if="guide" class="empty-state chat-empty">
      <div class="chat-welcome">
        <h3>欢迎使用案例分析工作台</h3>
        <p>输入分析指令开始对话，或从下面选择一项功能快速上手。</p>

        <div class="guide-grid">
          <div
            v-for="card in guideCards"
            :key="card.key"
            class="guide-card"
          >
            <component :is="card.icon" :size="18" />
            <strong>{{ card.title }}</strong>
            <span>{{ card.desc }}</span>
          </div>
        </div>

        <div class="prompt-chips">
          <span class="prompt-chips-label">试试这些示例：</span>
          <button
            v-for="prompt in examplePrompts"
            :key="prompt"
            type="button"
            class="prompt-chip"
            @click="emit('fillInput', prompt)"
          >
            {{ prompt }}
          </button>
        </div>
      </div>
    </div>

    <template v-for="(item, index) in items" :key="itemKey(item, index)">
      <UserBubble v-if="item.type === 'turn' && item.turn.role === 'user'" :turn="item.turn" />
      <AgentBubble v-else-if="item.type === 'turn'" :turn="item.turn" />

      <div v-else-if="item.type === 'run'" class="run-group">
        <!-- 用户指令（run.objective）以右侧蓝色气泡展示，与 ChatGPT 一致。
             仅顶层 run（协调器层）显示：专家子 run 的 objective 是系统委派
             提示词，不是用户发出的消息，不应出现在用户侧。 -->
        <UserBubble
          v-if="item.run.objective && !item.run.parent_run_id"
          :content="item.run.objective"
        />
        <RunBubble
          :run="item.run"
          :artifacts="item.artifacts"
          :trace="item.trace"
          :trace-loading="item.traceLoading"
          :artifacts-error="item.artifactsError || false"
          :live-events="item.liveEvents"
          :live-tool-calls="item.liveToolCalls"
          :live-model-calls="item.liveModelCalls"
          :final-content="item.finalContent"
          :case-id="caseId"
          @cancel="emit('cancel', $event)"
          @resume="emit('resume', $event)"
          @load-trace="emit('loadTrace', $event)"
          @retry-artifacts="emit('retryArtifacts', $event)"
          @ask-artifact="emit('askArtifact', $event)"
          @enter-steer="emit('enterSteer', $event)"
        />
      </div>

      <div v-else-if="item.type === 'orphan-artifacts'" class="orphan-block">
        <div class="orphan-head">
          <FolderKanban :size="14" />
          <span>未归属成果（Legacy 数据）</span>
        </div>
        <ArtifactCard
          v-for="artifact in item.artifacts"
          :key="artifact.id"
          :artifact="artifact"
          :case-id="caseId"
          @ask-artifact="emit('askArtifact', $event)"
        />
      </div>
    </template>
  </div>
</template>

<script lang="ts">
import ArtifactCard from '@/components/artifacts/ArtifactCard.vue'
</script>
