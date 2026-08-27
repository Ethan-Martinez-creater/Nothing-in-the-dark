<script setup lang="ts">
import { Download, GitCompareArrows, History } from 'lucide-vue-next'
import { ref } from 'vue'

import { api } from '@/services/api'
import type { Artifact, ArtifactDiff, ReportData } from '@/types/api'

const props = defineProps<{ data: ReportData; artifactId: string }>()

const downloading = ref(false)
const downloadError = ref('')
const versionsOpen = ref(false)
const versions = ref<Artifact[] | null>(null)
const versionsError = ref('')
const diff = ref<ArtifactDiff | null>(null)
const diffError = ref('')
const comparingTo = ref<string | null>(null)
const comparedVersionLabel = ref('')

async function download() {
  if (downloading.value) return
  downloading.value = true
  downloadError.value = ''
  try {
    await api.downloadReport(props.artifactId)
  } catch {
    downloadError.value = '下载失败，请重试。'
  } finally {
    downloading.value = false
  }
}

async function toggleVersions() {
  versionsOpen.value = !versionsOpen.value
  if (versionsOpen.value && versions.value === null) {
    versionsError.value = ''
    try {
      versions.value = await api.listArtifactVersions(props.artifactId)
    } catch {
      versionsError.value = '版本历史加载失败。'
    }
  }
}

async function compare(version: Artifact) {
  comparingTo.value = version.id
  comparedVersionLabel.value = `v${version.version}`
  diff.value = null
  diffError.value = ''
  try {
    diff.value = await api.diffArtifacts(props.artifactId, version.id)
  } catch {
    diffError.value = '版本差异比较失败。'
  } finally {
    comparingTo.value = null
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="panel artifact-panel">
    <div class="panel-heading">
      <div>
        <span class="eyebrow">REPORT ARTIFACT</span>
        <h3>{{ data.title }}</h3>
      </div>
      <div class="report-actions">
        <button
          type="button"
          class="ghost-button"
          :disabled="downloading"
          @click="download"
        >
          <Download :size="13" />
          {{ downloading ? '下载中…' : '下载 HTML' }}
        </button>
        <button type="button" class="ghost-button" @click="toggleVersions">
          <History :size="13" />
          {{ versionsOpen ? '收起版本' : '版本历史' }}
        </button>
      </div>
    </div>
    <p v-if="downloadError" class="artifact-error-inline">{{ downloadError }}</p>

    <p class="report-lead">{{ data.executive_summary }}</p>
    <div class="report-sections">
      <article v-for="section in data.sections" :key="section.title">
        <span>{{ section.title }}</span>
        <p>{{ section.content }}</p>
      </article>
    </div>
    <div v-if="data.citation_links.length" class="citation-links">
      <span class="eyebrow">CITATION LINKS</span>
      <div v-for="link in data.citation_links" :key="link.conclusion" class="citation-link">
        <p>{{ link.conclusion }}</p>
        <div class="evidence-chips">
          <span v-for="eid in link.evidence_ids" :key="eid">{{ eid }}</span>
        </div>
      </div>
    </div>
    <p class="panel-notice">{{ data.disclaimer }}</p>

    <div v-if="versionsOpen" class="report-versions">
      <span class="eyebrow">VERSION HISTORY</span>
      <p v-if="versionsError" class="artifact-error-inline">{{ versionsError }}</p>
      <ul v-else-if="versions" class="version-list">
        <li
          v-for="version in versions"
          :key="version.id"
          :class="{ current: version.id === artifactId }"
        >
          <button
            type="button"
            class="version-compare"
            :disabled="comparingTo !== null"
            @click="compare(version)"
          >
            <GitCompareArrows :size="13" />
          </button>
          <div class="version-meta">
            <span>v{{ version.version }} · {{ formatTime(version.created_at) }}</span>
            <em v-if="version.id === artifactId">当前版本</em>
          </div>
        </li>
      </ul>
      <p v-else class="trace-empty">正在加载版本历史…</p>

      <div v-if="diff" class="diff-summary">
        <span class="eyebrow">DIFF VS {{ comparedVersionLabel || '上一版本' }}</span>
        <div class="diff-row">
          <span class="diff-tag" :class="{ changed: diff.title_changed }">
            {{ diff.title_changed ? '标题已变更' : '标题未变' }}
          </span>
          <span class="diff-tag" :class="{ changed: diff.summary_changed }">
            {{ diff.summary_changed ? '摘要已变更' : '摘要未变' }}
          </span>
        </div>
        <div class="diff-groups">
          <div v-if="diff.sections_added.length" class="diff-group">
            <span class="diff-label">新增章节</span>
            <ul><li v-for="title in diff.sections_added" :key="title">{{ title }}</li></ul>
          </div>
          <div v-if="diff.sections_removed.length" class="diff-group">
            <span class="diff-label">移除章节</span>
            <ul><li v-for="title in diff.sections_removed" :key="title">{{ title }}</li></ul>
          </div>
          <div v-if="diff.sections_changed.length" class="diff-group">
            <span class="diff-label">内容变更章节</span>
            <ul><li v-for="title in diff.sections_changed" :key="title">{{ title }}</li></ul>
          </div>
          <p v-if="!diff.sections_added.length && !diff.sections_removed.length && !diff.sections_changed.length" class="diff-clean">
            章节结构一致 · 共 {{ diff.citation_link_count }} 条引用
          </p>
        </div>
      </div>
      <p v-if="diffError" class="artifact-error-inline">{{ diffError }}</p>
    </div>
  </section>
</template>
