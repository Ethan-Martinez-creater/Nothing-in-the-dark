<script setup lang="ts">
import { marked } from 'marked'
import { computed } from 'vue'

const props = defineProps<{ text: string }>()

// LLM 回答以 Markdown 渲染（标题/加粗/列表/代码块），观感对齐 ChatGPT。
marked.setOptions({ gfm: true, breaks: true })

const html = computed(() => marked.parse(props.text) as string)
</script>

<template>
  <div class="markdown-body" v-html="html" />
</template>
