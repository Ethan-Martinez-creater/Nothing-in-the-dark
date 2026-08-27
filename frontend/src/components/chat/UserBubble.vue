<script setup lang="ts">
import type { TurnRecord } from '@/types/api'

// 同时支持 turn 记录与纯文本内容：run 回合的用户指令（run.objective）
// 也以右侧蓝色气泡展示，与 ChatGPT 一致。
defineProps<{ turn?: TurnRecord; content?: string }>()
</script>

<template>
  <div class="chat-bubble user-bubble">
    <p>{{ content ?? turn?.content ?? '' }}</p>
  </div>
</template>
