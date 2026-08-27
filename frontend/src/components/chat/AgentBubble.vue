<script setup lang="ts">
import type { TurnRecord } from '@/types/api'

import MarkdownBody from './MarkdownBody.vue'

defineProps<{ turn: TurnRecord }>()
</script>

<template>
  <div class="chat-bubble agent-bubble">
    <!-- 历史 assistant 轮次与 run 最终回答同款排版（Markdown + 加粗大一号） -->
    <MarkdownBody :text="turn.content" class="run-answer" />
  </div>
</template>
