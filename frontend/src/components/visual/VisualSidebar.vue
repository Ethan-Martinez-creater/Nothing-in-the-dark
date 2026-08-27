<script setup lang="ts">
import { BarChart3, X } from 'lucide-vue-next'

import PlatformComparisonCard from '@/components/platform/PlatformComparisonCard.vue'

defineProps<{ open: boolean; caseId: string }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <!-- v-if="open"：未打开不渲染（父组件同样以 v-if 控制挂载），
       保证对话区右侧不会因常驻面板产生空白占位 -->
  <aside v-if="open" class="viz-sidebar" :class="{ open }">
    <div class="viz-header">
      <div>
        <span class="eyebrow">VISUALIZATION</span>
        <h3>可视化数据</h3>
      </div>
      <button type="button" class="icon-button" aria-label="关闭可视化面板" @click="emit('close')">
        <X :size="16" />
      </button>
    </div>

    <div class="viz-body">
      <div class="viz-section">
        <div class="viz-section-title">
          <BarChart3 :size="14" />
          <span>跨平台数据对齐</span>
        </div>
        <PlatformComparisonCard :case-id="caseId" />
      </div>
    </div>
  </aside>
</template>
