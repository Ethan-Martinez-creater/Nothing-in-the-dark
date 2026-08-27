<script setup lang="ts">
import {
  BarChart3,
  Bot,
  Database,
  FileText,
  Gavel,
  Layers,
  MessageSquareText,
  Network,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-vue-next'
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import CaseComposer from '@/components/CaseComposer.vue'
import { api } from '@/services/api'

const router = useRouter()
const newChatOpen = ref(false)
const creating = ref(false)
const createError = ref('')

// 核心能力模块
const capabilities = [
  {
    icon: Network,
    title: '多平台采集',
    desc: '微博 / 哔哩哔哩 / 百度贴吧 / 知乎 / 抖音。LLM 按平台特点优化检索词与入口，采集指定主题事件数据，支持真实爬取与演示模式。',
  },
  {
    icon: MessageSquareText,
    title: '基于证据的多轮对话',
    desc: 'Agent 基于采集数据回答问题，多轮追问持续深入；检索、记忆、专家委派全程可审计，每条结论都能回到证据。',
  },
  {
    icon: BarChart3,
    title: '跨平台数据对齐',
    desc: '同一事件在各平台的参与度、情感、时间线、话题词对比，可视化看清传播异同与舆情走向。',
  },
  {
    icon: Gavel,
    title: '多角色辩论验证',
    desc: '各平台视角的 Agent 以本平台数据为背景辩论：陈述 → 反驳 → 投票 → 主持人总结，逼近事实结论。',
  },
  {
    icon: Bot,
    title: 'Harness Agent 调度',
    desc: 'Coordinator 协调多专家 Agent（意见、传播、核验、证据批判、报告、引用校验），任务动态派发、父子 Run 追踪。',
  },
  {
    icon: ShieldCheck,
    title: '可审计与人工介入',
    desc: '模型调用、工具调用、运行轨迹全量审计；审批（human-in-the-loop）与运行中指令随时介入分析过程。',
  },
]

// 分析工作流：从创建会话到输出报告
const workflow = [
  {
    icon: Search,
    title: '新建会话',
    desc: '设定主题事件、选择目标平台与分析范围，创建专属案例分析会话。',
  },
  {
    icon: Database,
    title: '数据采集入库',
    desc: '多平台并行采集并做清洗、去重与元数据标注，构建事件数据底座。',
  },
  {
    icon: Layers,
    title: '证据化分析',
    desc: '主张抽取、证据链构建、传播网络复原与事实核查，专家 Agent 协作产出成果。',
  },
  {
    icon: FileText,
    title: '报告与验证',
    desc: '跨平台对比可视化、多角色辩论验证，最终沉淀为可追溯的核查报告。',
  },
]

const platforms = ['微博', '哔哩哔哩', '百度贴吧', '知乎', '抖音']

// 技术底座
const stack = [
  'FastAPI',
  'LangGraph',
  'PostgreSQL',
  'Vue 3',
  'Multi-Agent',
  'RAG 检索',
  'SSE 实时流',
  'MCP / A2A',
]

async function createCase(payload: {
  topic: string
  description: string
  platforms: string[]
  time_start?: string
  time_end?: string
}) {
  creating.value = true
  createError.value = ''
  try {
    const record = await api.createCase(payload)
    newChatOpen.value = false
    void router.push(`/cases/${record.id}`)
  } catch {
    createError.value = '创建会话失败，请检查后端服务后重试。'
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="page welcome-page">
    <section class="welcome-hero">
      <span class="eyebrow">COIFESP · SOCIAL INTELLIGENCE HARNESS</span>
      <h1>让每条结论，都能回到证据。</h1>
      <p>
        面向舆情研究的多智能体 Harness 工作台：从微博、哔哩哔哩、百度贴吧、知乎、
        抖音等社交平台采集指定主题事件数据，基于证据开展多轮对话、跨平台对比与
        多角色辩论验证。
      </p>
      <button type="button" class="primary-button welcome-start" @click="newChatOpen = true">
        <Sparkles :size="16" />
        新建会话开始分析
      </button>
    </section>

    <section class="welcome-about">
      <h2>本系统是什么</h2>
      <p>
        COIFESP 是一个把「社交数据采集 — 证据化分析 — 结论验证」串成一条可审计链路的
        Harness Agent 系统。它不只是单轮问答：创建会话后，Coordinator 会调度采集、
        主张抽取、传播复原、事实核查等专家 Agent 分步推进；每一步的模型调用、工具
        调用与中间结果都会内联展示在对话中，你可以随时向运行中的任务发指令、审批
        敏感操作或追问某份成果，直到输出一份每条论断都有据可查的分析报告。
      </p>
    </section>

    <section class="welcome-section">
      <h2>核心能力</h2>
      <div class="welcome-features">
        <article v-for="feature in capabilities" :key="feature.title" class="welcome-feature">
          <feature.icon :size="20" />
          <h3>{{ feature.title }}</h3>
          <p>{{ feature.desc }}</p>
        </article>
      </div>
    </section>

    <section class="welcome-section">
      <h2>分析工作流</h2>
      <div class="welcome-steps">
        <article v-for="(step, index) in workflow" :key="step.title" class="welcome-step">
          <span class="welcome-step-index">{{ index + 1 }}</span>
          <step.icon :size="18" />
          <h3>{{ step.title }}</h3>
          <p>{{ step.desc }}</p>
        </article>
      </div>
    </section>

    <section class="welcome-section">
      <h2>技术底座</h2>
      <div class="welcome-chips">
        <span v-for="item in stack" :key="item" class="welcome-chip">{{ item }}</span>
      </div>
      <p class="welcome-footnote">支持平台：{{ platforms.join(' · ') }}</p>
      <p class="welcome-footnote">检索增强 · 记忆 · 审批（Human-in-the-Loop）· 全链路审计</p>
    </section>

    <div v-if="newChatOpen" class="modal-overlay" @click.self="newChatOpen = false">
      <div class="modal-card">
        <div class="modal-head">
          <h3>新建会话</h3>
          <button type="button" class="icon-button" aria-label="关闭" @click="newChatOpen = false">
            ✕
          </button>
        </div>
        <CaseComposer :submitting="creating" :demo-mode="true" @submit="createCase" />
        <p v-if="createError" class="modal-error">{{ createError }}</p>
      </div>
    </div>
  </div>
</template>
