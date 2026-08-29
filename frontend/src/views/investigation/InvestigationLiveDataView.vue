<script setup lang="ts">
// Optimization V2 (M5.6 + C8.3)：Live Data 工作区。
// Tabs：Posts（原始帖子分页列表）/ Media / Platform Comparison。
// 选中帖子进入 Copilot context（workspace=live_data）。
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import MediaPanel from '@/components/media/MediaPanel.vue'
import PlatformComparisonCard from '@/components/platform/PlatformComparisonCard.vue'
import PostsList from '@/components/livedata/PostsList.vue'
import type { SocialPostDTO } from '@/types/api'
import { useInvestigationContext } from '@/composables/useInvestigationContext'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

type LiveDataTab = 'posts' | 'media' | 'comparison'
const tab = ref<LiveDataTab>('posts')

const { setUiContext } = useInvestigationContext()

const tabLabels: Record<LiveDataTab, string> = {
  posts: 'Posts',
  media: '媒体',
  comparison: '平台对比',
}

function onSelectPost(post: SocialPostDTO) {
  setUiContext({
    workspace: 'live_data',
    selected_type: 'social_post',
    selected_id: post.id,
  })
}

onMounted(() => {
  setUiContext({ workspace: 'live_data' })
})
</script>

<template>
  <div class="ilive">
    <div class="ilive__tabs">
      <button
        v-for="(label, key) in tabLabels"
        :key="key"
        type="button"
        class="ilive__tab"
        :class="{ 'ilive__tab--active': tab === key }"
        @click="tab = key as LiveDataTab"
      >
        {{ label }}
      </button>
    </div>

    <div class="ilive__body">
      <PostsList v-if="tab === 'posts'" :case-id="caseId" @select-post="onSelectPost" />
      <MediaPanel v-else-if="tab === 'media'" :case-id="caseId" :open="true" />
      <PlatformComparisonCard v-else :case-id="caseId" />
    </div>
  </div>
</template>

<style scoped>
.ilive {
  display: flex;
  flex-direction: column;
  min-height: 480px;
}

.ilive__tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.ilive__tab {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
}

.ilive__tab--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.ilive__body {
  flex: 1;
  min-height: 0;
  padding: 12px 16px 24px;
}
</style>
