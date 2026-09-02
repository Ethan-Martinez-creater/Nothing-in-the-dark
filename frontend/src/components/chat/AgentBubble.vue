<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'

import type { TurnRecord } from '@/types/api'

import MarkdownBody from './MarkdownBody.vue'

const props = defineProps<{ turn: TurnRecord }>()

// 默认展开；点击标题栏折叠/展开长回答，避免一条超长输出占据整个视口。
const collapsed = ref(false)

const summary = computed(() => {
  const text = (props.turn.content || '').replace(/\s+/g, ' ').trim()
  if (!text) return '（空）'
  return text.length > 90 ? `${text.slice(0, 90)}…` : text
})
</script>

<template>
  <div class="chat-bubble agent-bubble">
    <button
      type="button"
      class="agent-bubble__toggle"
      :aria-expanded="!collapsed"
      @click="collapsed = !collapsed"
    >
      <ChevronRight v-if="collapsed" :size="14" class="agent-bubble__chevron" />
      <ChevronDown v-else :size="14" class="agent-bubble__chevron" />
      <span class="agent-bubble__label">Agent</span>
      <span v-if="collapsed" class="agent-bubble__summary">{{ summary }}</span>
    </button>
    <div v-show="!collapsed" class="agent-bubble__body">
      <MarkdownBody :text="turn.content" class="run-answer" />
    </div>
  </div>
</template>

<style scoped>
.agent-bubble {
  padding: 8px 10px;
}

.agent-bubble__toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 2px 0 6px;
  border: 0;
  background: transparent;
  color: var(--text-muted, #64748b);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}

.agent-bubble__chevron {
  flex-shrink: 0;
}

.agent-bubble__label {
  flex-shrink: 0;
}

.agent-bubble__summary {
  overflow: hidden;
  min-width: 0;
  color: var(--text-soft, #94a3b8);
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-bubble__body {
  padding-top: 2px;
}
</style>
