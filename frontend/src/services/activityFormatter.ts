// Optimization V2 (M2.5)：Run 事件 → 语义化活动映射（纯函数）。
// 未知事件不崩溃：默认列表显示通用文本，raw 内容留给 Advanced Trace。
import type { RunEvent } from '@/types/api'

export interface SemanticActivity {
  id: string
  category: 'agent' | 'collection' | 'analysis' | 'approval' | 'review' | 'system'
  title: string
  detail?: string
  status: 'pending' | 'running' | 'success' | 'warning' | 'error'
  createdAt: string
  runId?: string
  refType?: string
  refId?: string
}

const AGENT_LABELS: Record<string, string> = {
  coordinator: '协调专家',
  opinion: '观点分析专家',
  propagation: '传播分析专家',
  verification: '事实核查专家',
  evidence_critic: '证据评审专家',
  report: '报告专家',
  citation_validator: '引用校验专家',
}

function agentLabel(agent: string | null | undefined): string {
  if (!agent) return 'Agent'
  return AGENT_LABELS[agent] ?? agent
}

function toolLabel(tool: string | null | undefined): string {
  switch (tool) {
    case 'collect_social_posts':
      return '正在采集社交平台数据'
    case 'search_evidence':
      return '正在检索证据'
    case 'search_social_evidence':
      return '正在检索社交证据'
    case 'write_case_memory':
      return '正在写入案例记忆'
    case 'get_case_summary':
      return '正在读取案例摘要'
    case 'get_artifact':
      return '正在读取分析成果'
    case 'get_propagation_graph':
      return '正在读取传播网络'
    default:
      return tool ? `正在执行 ${tool}` : '正在执行工具调用'
  }
}

export function formatRunEvent(event: RunEvent): SemanticActivity {
  const payload = (event.payload ?? {}) as Record<string, unknown>
  const agent = typeof payload.agent === 'string' ? payload.agent : event.agent
  const base: SemanticActivity = {
    id: `evt-${event.id}`,
    category: 'system',
    title: event.event_type,
    status: 'running',
    createdAt: event.created_at,
    runId: event.run_id,
  }

  switch (event.event_type) {
    case 'agent_queued':
      return {
        ...base,
        category: 'agent',
        title: '分析任务已排队',
        status: 'pending',
      }
    case 'agent_start':
      return {
        ...base,
        category: 'agent',
        title: `${agentLabel(agent)}开始工作`,
        status: 'running',
      }
    case 'expert_dispatched': {
      const expert = typeof payload.expert === 'string' ? payload.expert : agent
      return {
        ...base,
        category: 'analysis',
        title: `已委派${agentLabel(expert)}`,
        status: 'running',
        refType: 'expert',
        refId: expert ?? undefined,
      }
    }
    case 'expert_completed': {
      const expert = typeof payload.expert === 'string' ? payload.expert : agent
      return {
        ...base,
        category: 'analysis',
        title: `${agentLabel(expert)}已完成`,
        status: 'success',
        refType: 'expert',
        refId: expert ?? undefined,
      }
    }
    case 'expert_failed': {
      const expert = typeof payload.expert === 'string' ? payload.expert : agent
      return {
        ...base,
        category: 'analysis',
        title: `${agentLabel(expert)}执行失败`,
        detail: typeof payload.error === 'string' ? payload.error : undefined,
        status: 'error',
      }
    }
    case 'tool_execution_start':
      return {
        ...base,
        category: event.tool === 'collect_social_posts' ? 'collection' : 'analysis',
        title: toolLabel(event.tool),
        detail: event.skill ?? undefined,
        status: 'running',
        refType: 'tool',
        refId: event.tool_call_id ?? undefined,
      }
    case 'tool_execution_end':
      return {
        ...base,
        category: event.tool === 'collect_social_posts' ? 'collection' : 'analysis',
        title: `${toolLabel(event.tool)}${event.status === 'success' || event.status === 'completed' ? '完成' : `结束（${event.status}）`}`,
        status:
          event.status === 'success' || event.status === 'completed'
            ? 'success'
            : event.status === 'cancelled'
              ? 'warning'
              : 'error',
        refType: 'tool',
        refId: event.tool_call_id ?? undefined,
      }
    case 'approval_required':
    case 'approval_pending':
      return {
        ...base,
        category: 'approval',
        title:
          event.tool === 'collect_social_posts'
            ? '需要批准真实平台采集'
            : '等待你的批准',
        detail: typeof payload.reason === 'string' ? payload.reason : undefined,
        status: 'warning',
        refType: 'approval',
        refId: typeof payload.approval_id === 'string' ? payload.approval_id : undefined,
      }
    case 'steering_received':
      return {
        ...base,
        category: 'agent',
        title: '收到运行指令，将在下一步生效',
        status: 'pending',
      }
    case 'steering_applied':
      return {
        ...base,
        category: 'agent',
        title: '运行指令已生效',
        status: 'success',
      }
    case 'agent_end':
      return {
        ...base,
        category: 'agent',
        title: event.status === 'completed' ? '分析完成' : `分析结束（${event.status}）`,
        status:
          event.status === 'completed'
            ? 'success'
            : event.status === 'cancelled'
              ? 'warning'
              : 'error',
      }
    case 'agent_error':
      return {
        ...base,
        category: 'agent',
        title: '执行出错',
        detail: typeof payload.error === 'string' ? payload.error : undefined,
        status: 'error',
      }
    default:
      // 未知事件：默认列表给中性语义文本，raw 留给 Advanced Trace。
      return {
        ...base,
        category: 'system',
        title: '系统事件',
        detail: event.event_type,
        status: 'running',
      }
  }
}
