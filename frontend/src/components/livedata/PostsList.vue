<script setup lang="ts">
// C8.3: Live Data 的原始帖子列表（分页 + platform/关键词/时间过滤）。
// 数据来自 GET /cases/{id}/posts（真实 SourcePostRecord）；点击帖子进入
// Copilot context（workspace=live_data, selected_type=social_post）。
import { onMounted, ref, watch } from 'vue'

import { api } from '@/services/api'
import type { SocialPostDTO } from '@/types/api'

const props = defineProps<{ caseId: string; refreshTick?: number }>()
const emit = defineEmits<{
  selectPost: [post: SocialPostDTO]
}>()

const posts = ref<SocialPostDTO[]>([])
const loading = ref(false)
const error = ref('')
const hasMore = ref(false)

const platformFilter = ref('')
const keywordFilter = ref('')
const fromFilter = ref('')
const toFilter = ref('')
const PAGE_SIZE = 50

const PLATFORM_OPTIONS = ['weibo', 'bilibili', 'zhihu', 'tieba', 'douyin']

async function load(reset = false) {
  if (!props.caseId) return
  loading.value = true
  error.value = ''
  try {
    const page = await api.listCasePosts(props.caseId, {
      platform: platformFilter.value || undefined,
      q: keywordFilter.value || undefined,
      from: fromFilter.value || undefined,
      to: toFilter.value || undefined,
      limit: PAGE_SIZE,
      offset: reset ? 0 : posts.value.length,
    })
    posts.value = reset ? page.posts : [...posts.value, ...page.posts]
    hasMore.value = page.has_more
  } catch {
    error.value = '帖子列表加载失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  void load(true)
}

function loadMore() {
  void load(false)
}

function postTitle(post: SocialPostDTO): string {
  return post.title || post.content.slice(0, 60) || '(无标题)'
}

watch(
  () => props.caseId,
  () => {
    void load(true)
  },
)

// 渐进采集：后台 CollectionRun 有数据落库时（refreshTick 变化）刷新列表。
watch(
  () => props.refreshTick,
  (tick) => {
    if (typeof tick === 'number' && tick > 0) void load(true)
  },
)

onMounted(() => {
  void load(true)
})
</script>

<template>
  <section class="plist" aria-label="原始帖子列表">
    <div class="plist__filters">
      <select v-model="platformFilter" class="plist__select" @change="applyFilters">
        <option value="">全部平台</option>
        <option v-for="platform in PLATFORM_OPTIONS" :key="platform" :value="platform">
          {{ platform }}
        </option>
      </select>
      <input
        v-model="keywordFilter"
        class="plist__input"
        type="search"
        placeholder="关键词过滤"
        @keyup.enter="applyFilters"
      />
      <input v-model="fromFilter" class="plist__input" type="date" @change="applyFilters" />
      <input v-model="toFilter" class="plist__input" type="date" @change="applyFilters" />
      <button type="button" class="ghost-button" @click="applyFilters">应用</button>
    </div>

    <p v-if="error" class="plist__state plist__state--error">{{ error }}</p>
    <p v-else-if="loading && !posts.length" class="plist__state">帖子加载中…</p>
    <p v-else-if="!posts.length" class="plist__state">
      暂无帖子 —— 采集完成后这里会展示原始内容。
    </p>
    <ul v-else class="plist__list">
      <li
        v-for="post in posts"
        :key="post.id"
        class="plist__item"
        @click="emit('selectPost', post)"
      >
        <div class="plist__meta">
          <span class="plist__platform">{{ post.platform }}</span>
          <span v-if="post.author_name">{{ post.author_name }}</span>
          <time v-if="post.published_at">{{ post.published_at.slice(0, 16).replace('T', ' ') }}</time>
        </div>
        <p class="plist__title">{{ postTitle(post) }}</p>
        <a
          v-if="post.source_url"
          class="plist__link"
          :href="post.source_url"
          target="_blank"
          rel="noreferrer"
          @click.stop
        >打开原文</a>
      </li>
    </ul>
    <button
      v-if="hasMore && !loading"
      type="button"
      class="ghost-button plist__more"
      @click="loadMore"
    >
      加载更多
    </button>
  </section>
</template>

<style scoped>
.plist {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.plist__filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.plist__select,
.plist__input {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
}

.plist__state {
  margin: 20px 0;
  text-align: center;
  color: var(--text-soft);
  font-size: 13px;
}

.plist__state--error {
  color: var(--red);
}

.plist__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.plist__item {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  cursor: pointer;
}

.plist__item:hover {
  border-color: var(--accent);
}

.plist__meta {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 11px;
  color: var(--text-soft);
}

.plist__platform {
  padding: 1px 8px;
  border-radius: 999px;
  background: rgba(143, 155, 179, 0.16);
  color: var(--text-muted);
}

.plist__title {
  margin: 6px 0 4px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
}

.plist__link {
  font-size: 11px;
  color: var(--accent);
}

.plist__more {
  align-self: center;
}
</style>
