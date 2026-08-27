<script setup lang="ts">
import { ArrowRight, CalendarRange, Check, Database, Sparkles } from 'lucide-vue-next'
import { computed, reactive, ref } from 'vue'

const emit = defineEmits<{
  submit: [
    payload: {
      topic: string
      description: string
      platforms: string[]
      time_start?: string
      time_end?: string
    },
  ]
}>()

defineProps<{
  submitting?: boolean
  demoMode?: boolean
}>()

const form = reactive({
  topic: '',
  description: '',
  platforms: ['weibo', 'bilibili'] as string[],
  timeStart: '',
  timeEnd: '',
})
const error = ref('')
const platformOptions = [
  { id: 'weibo', label: '微博', icon: '微' },
  { id: 'bilibili', label: '哔哩哔哩', icon: 'B' },
  { id: 'tieba', label: '百度贴吧', icon: '贴' },
  { id: 'zhihu', label: '知乎', icon: '知' },
  { id: 'douyin', label: '抖音', icon: '抖' },
]

const canSubmit = computed(() => form.topic.trim().length >= 2 && form.platforms.length > 0)

function togglePlatform(platform: string) {
  form.platforms = form.platforms.includes(platform)
    ? form.platforms.filter((item) => item !== platform)
    : [...form.platforms, platform]
}

function submit() {
  if (!canSubmit.value) {
    error.value = '请输入至少两个字符的主题，并选择一个平台。'
    return
  }
  error.value = ''
  emit('submit', {
    topic: form.topic.trim(),
    description: form.description.trim(),
    platforms: form.platforms,
    time_start: form.timeStart || undefined,
    time_end: form.timeEnd || undefined,
  })
}
</script>

<template>
  <section class="composer-card">
    <div class="composer-heading">
      <div>
        <span class="eyebrow"><Sparkles :size="14" /> NEW ANALYSIS</span>
        <h2>今天要研究什么舆情？</h2>
        <p>先限定平台与时间范围，Harness 会生成可审计的分析计划。</p>
      </div>
      <div class="source-boundary">
        <Database :size="16" />
        <span>仅使用社交平台证据</span>
      </div>
    </div>

    <div class="form-grid">
      <label class="field field-wide">
        <span>研究主题</span>
        <textarea
          v-model="form.topic"
          rows="3"
          placeholder="例如：某公共事件在微博与哔哩哔哩的传播路径和主要争议"
        ></textarea>
      </label>

      <label class="field field-wide">
        <span>补充说明 <small>可选</small></span>
        <input v-model="form.description" placeholder="需要特别关注的人群、说法或传播节点" />
      </label>

      <div class="field">
        <span>社交平台</span>
        <div class="platform-options">
          <button
            v-for="platform in platformOptions"
            :key="platform.id"
            type="button"
            class="platform-option"
            :class="{ selected: form.platforms.includes(platform.id) }"
            @click="togglePlatform(platform.id)"
          >
            <span class="platform-icon">{{ platform.icon }}</span>
            {{ platform.label }}
            <Check v-if="form.platforms.includes(platform.id)" :size="15" />
          </button>
        </div>
      </div>

      <div class="field">
        <span><CalendarRange :size="15" /> 时间范围 <small>可选</small></span>
        <div class="date-range">
          <input v-model="form.timeStart" type="date" />
          <span>至</span>
          <input v-model="form.timeEnd" type="date" />
        </div>
      </div>
    </div>

    <div class="composer-actions">
      <div>
        <span class="error-text">{{ error }}</span>
        <small v-if="demoMode">演示模式会生成带 DEMO 标识的样本，不会调用真实平台账号。</small>
        <small v-else>真实模式将在案例页再次确认后调用已配置的平台采集器。</small>
      </div>
      <button class="primary-button" type="button" :disabled="submitting" @click="submit">
        {{ submitting ? '正在创建…' : '创建分析案例' }}
        <ArrowRight :size="17" />
      </button>
    </div>
  </section>
</template>
