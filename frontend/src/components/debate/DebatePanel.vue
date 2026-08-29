<script setup lang="ts">
import { Gavel, Loader2, MessageSquarePlus, Send, X } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { Debate, DebateDetail, DebateMessage } from '@/types/api'

import MarkdownBody from '@/components/chat/MarkdownBody.vue'

// embedded：内嵌对话主区（滑块切换），不再使用右侧滑出边栏与关闭按钮。
const props = defineProps<{ caseId: string; embedded?: boolean }>()
const emit = defineEmits<{ close: [] }>()

const debates = ref<Debate[] | null>(null)
const active = ref<DebateDetail | null>(null)
const busy = ref(false)
const error = ref('')
const listRetry = ref(false)
const userContent = ref('')
const creating = ref(false)

const PLATFORM_NAMES: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

// 平台角色色点（与主对话 agent 消息一致的视觉锚点）。
const PLATFORM_COLORS: Record<string, string> = {
  weibo: '#e6672a',
  bilibili: '#00a1d6',
  tieba: '#2f6bff',
  zhihu: '#0066ff',
  douyin: '#111827',
}

const ROUND_LABELS: Record<number, string> = {
  1: '观点陈述',
  2: '互相反驳',
  3: '观点投票',
  4: '主持人总结',
}

const currentRoundLabel = computed(() =>
  active.value ? ROUND_LABELS[active.value.round] || `第 ${active.value.round} 轮` : '',
)

const completed = computed(() => active.value?.status === 'completed')

// 按轮次分组：轮与轮之间插入分隔线，轮内消息按 ChatGPT 式对话排版。
const threadGroups = computed(() => {
  if (!active.value) return []
  const groups: Array<{ round: number; label: string; messages: DebateMessage[] }> = []
  for (const message of active.value.messages) {
    const last = groups[groups.length - 1]
    if (!last || last.round !== message.round) {
      groups.push({
        round: message.round,
        label: ROUND_LABELS[message.round] || `第 ${message.round} 轮`,
        messages: [message],
      })
    } else {
      last.messages.push(message)
    }
  }
  return groups
})

function roleLabel(message: DebateMessage): string {
  if (message.role === 'moderator') return '主持人'
  return PLATFORM_NAMES[message.platform || ''] || message.platform || '参与者'
}

function roleColor(message: DebateMessage): string {
  if (message.role === 'moderator') return '#10b981'
  return PLATFORM_COLORS[message.platform || ''] || '#64748b'
}

// 平台无采集数据时的声明消息（后端不调 LLM 生成）：弱化展示，区别于真实发言。
function isNoDataMessage(message: DebateMessage): boolean {
  return message.role === 'platform_role' && message.content.startsWith('【数据缺失】')
}

async function loadDebates() {
  try {
    debates.value = await api.listDebates(props.caseId)
    const first = debates.value?.[0]
    if (!active.value && first) {
      active.value = await api.getDebate(first.id)
    }
    listRetry.value = false
    error.value = ''
  } catch {
    error.value = '辩论列表加载失败。'
    listRetry.value = true
  }
}

async function createDebate() {
  creating.value = true
  error.value = ''
  try {
    const debate = await api.createDebate(props.caseId)
    active.value = await api.getDebate(debate.id)
    await loadDebates()
  } catch {
    error.value = '发起辩论失败，请确认该调查已采集到平台数据。'
  } finally {
    creating.value = false
  }
}

async function advance() {
  if (!active.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    active.value = await api.advanceDebate(active.value.id)
  } catch {
    error.value = '本轮推进失败，请重试。'
  } finally {
    busy.value = false
  }
}

async function sendUserMessage() {
  const text = userContent.value.trim()
  if (!text || !active.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api.addDebateMessage(active.value.id, text)
    userContent.value = ''
    active.value = await api.getDebate(active.value.id)
  } catch {
    error.value = '发言发送失败。'
  } finally {
    busy.value = false
  }
}

onMounted(loadDebates)
</script>

<template>
  <div class="debate-panel" :class="embedded ? 'debate-inline' : 'workspace-panel open'">
    <div class="modal-head">
      <h3><Gavel :size="16" /> 多角色辩论</h3>
      <button v-if="!embedded" type="button" class="icon-button" aria-label="关闭" @click="emit('close')">
        <X :size="16" />
      </button>
    </div>

      <p v-if="error" class="modal-error">
        {{ error }}
        <button
          v-if="listRetry"
          type="button"
          class="ghost-button"
          @click="loadDebates"
        >
          重试
        </button>
      </p>

      <div v-if="!active" class="debate-empty">
        <p>以各平台采集数据为背景知识，让多个平台视角的 Agent 辩论，逼近更接近事实的结论。</p>
        <p class="debate-flow">四轮流程：观点陈述 → 互相反驳 → 观点投票 → 主持人总结，每轮之间你都可以插话。</p>
        <button type="button" class="primary-button" :disabled="creating" @click="createDebate">
          <Loader2 v-if="creating" class="spin" :size="14" />
          发起辩论
        </button>
      </div>

      <template v-else>
        <div class="debate-head">
          <strong>{{ active.title }}</strong>
          <span class="debate-round-badge">
            第 {{ active.round }} 轮 · {{ currentRoundLabel }}
          </span>
          <span v-if="completed" class="debate-done-badge">已结束</span>
        </div>

        <div class="debate-thread">
          <template v-for="group in threadGroups" :key="group.round">
            <div class="debate-round-divider">
              <span>第 {{ group.round }} 轮 · {{ group.label }}</span>
            </div>
            <template v-for="message in group.messages" :key="message.id">
              <!-- 用户插话：右侧蓝色气泡，与主对话一致 -->
              <article v-if="message.role === 'user'" class="debate-message role-user">
                <p>{{ message.content }}</p>
              </article>
              <!-- 平台/主持人发言：左侧 agent 消息（色点 + 角色 + Markdown 正文） -->
              <article
                v-else
                class="debate-message role-agent"
                :class="{ 'role-moderator': message.role === 'moderator', 'msg-no-data': isNoDataMessage(message) }"
              >
                <div class="debate-message-head">
                  <span class="debate-role-dot" :style="{ background: roleColor(message) }" />
                  <span class="debate-role">{{ roleLabel(message) }}</span>
                  <span v-if="isNoDataMessage(message)" class="no-data-tag">缺席</span>
                </div>
                <MarkdownBody :text="message.content" class="debate-content" />
              </article>
            </template>
          </template>

          <div v-if="active.votes.length" class="debate-votes">
            <span class="eyebrow">第三轮投票结果</span>
            <div
              v-for="vote in active.votes"
              :key="vote.id"
              class="debate-vote"
            >
              <strong>{{ PLATFORM_NAMES[vote.platform] || vote.platform }}</strong>
              投给
              <em>{{ PLATFORM_NAMES[vote.choice] || vote.choice }}</em>
              <span v-if="vote.reason">：{{ vote.reason }}</span>
            </div>
          </div>

          <p v-if="busy" class="debate-busy"><Loader2 class="spin" :size="13" /> 正在生成本轮发言…</p>
        </div>

        <div v-if="!completed" class="debate-actions">
          <div class="input-row">
            <textarea
              v-model="userContent"
              class="chat-textarea"
              rows="2"
              placeholder="发表你的观点（将在下一轮注入辩论上下文）"
              :disabled="busy"
              @keydown.enter.exact.prevent="sendUserMessage"
            />
            <button
              type="button"
              class="primary-button send-button"
              :disabled="busy || !userContent.trim()"
              @click="sendUserMessage"
            >
              <Send :size="15" />
              发言
            </button>
          </div>
          <button
            type="button"
            class="primary-button debate-advance"
            :disabled="busy"
            @click="advance"
          >
            <Loader2 v-if="busy" class="spin" :size="14" />
            {{ active.round === 4 ? '生成主持人总结' : `推进第 ${active.round + 1} 轮` }}
          </button>
        </div>
        <p v-else class="debate-finished">
          <MessageSquarePlus :size="14" />
          辩论已完成。主持人结论即上方「主持人」发言。
          <button
            type="button"
            class="ghost-button debate-restart"
            :disabled="creating"
            title="基于最新采集数据与平台画像再开一场新辩论"
            @click="createDebate"
          >
            <Loader2 v-if="creating" class="spin" :size="12" />
            再开一场
          </button>
        </p>
      </template>
  </div>
</template>
