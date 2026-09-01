<script setup lang="ts">
// Optimization V2 (M5.6 + C8.3)：Live Data 工作区。
// Tabs：Posts（原始帖子分页列表）/ Media / Platform Comparison。
// 选中帖子进入 Copilot context（workspace=live_data）。
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import MediaPanel from '@/components/media/MediaPanel.vue'
import PlatformComparisonCard from '@/components/platform/PlatformComparisonCard.vue'
import PostsList from '@/components/livedata/PostsList.vue'
import { collectionRunApi } from '@/services/api/collectionRuns'
import type { SocialPostDTO } from '@/types/api'
import { useInvestigationContext } from '@/composables/useInvestigationContext'

const route = useRoute()
const caseId = computed(() => String(route.params.caseId ?? ''))

type LiveDataTab = 'posts' | 'media' | 'comparison'
const tab = ref<LiveDataTab>('posts')

const { setUiContext } = useInvestigationContext()

// 渐进采集刷新：后台 CollectionRun 的 posts_collected 增长时刷新列表，
// 只有计数变化才 reload（文档 53 节），页面 hidden 时暂停。
const refreshTick = ref(0)
const pollTimer = ref<number | null>(null)
let lastTotal = 0

async function pollCollectionProgress() {
  if (document.hidden) return
  let active: Awaited<ReturnType<typeof collectionRunApi.list>> = []
  try {
    active = await collectionRunApi.list(caseId.value, { active: true })
  } catch {
    return
  }
  const total = active.reduce((sum, run) => sum + run.posts_collected, 0)
  if (total !== lastTotal) {
    lastTotal = total
    refreshTick.value += 1
  }
}

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
  void pollCollectionProgress()
  pollTimer.value = window.setInterval(pollCollectionProgress, 3000)
})

onUnmounted(() => {
  if (pollTimer.value !== null) {
    window.clearInterval(pollTimer.value)
    pollTimer.value = null
  }
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
      <PostsList
        v-if="tab === 'posts'"
        :case-id="caseId"
        :refresh-tick="refreshTick"
        @select-post="onSelectPost"
      />
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
