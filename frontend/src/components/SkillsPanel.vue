<script setup lang="ts">
import { Boxes, Wrench, X, Zap } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { SkillInfo } from '@/types/api'

const emit = defineEmits<{ close: [] }>()

const skills = ref<SkillInfo[] | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    skills.value = await api.listSkills()
  } catch {
    error.value = '技能清单加载失败。'
  }
})
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-card skills-modal">
      <div class="modal-head">
        <h3><Boxes :size="16" /> 已注册技能</h3>
        <button type="button" class="icon-button" aria-label="关闭" @click="emit('close')">
          <X :size="16" />
        </button>
      </div>

      <p v-if="error" class="modal-error">{{ error }}</p>
      <p v-else-if="!skills" class="modal-muted">技能清单加载中…</p>
      <div v-else-if="!skills.length" class="modal-muted">暂无已注册技能</div>

      <div v-else class="skills-list">
        <article v-for="skill in skills" :key="skill.name" class="skill-card">
          <div class="skill-head">
            <strong>{{ skill.name }}</strong>
            <span class="skill-version">v{{ skill.version }}</span>
            <span v-if="!skill.loadable" class="skill-badge">不可加载</span>
          </div>
          <p class="skill-desc">{{ skill.description }}</p>
          <div class="skill-row">
            <span class="skill-label"><Wrench :size="12" /> 工具</span>
            <code>{{ skill.tools.join('、') || '—' }}</code>
          </div>
          <div class="skill-row">
            <span class="skill-label"><Zap :size="12" /> 成本</span>
            <code>约 {{ skill.cost_tokens }} tokens</code>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>
