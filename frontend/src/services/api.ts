import axios from 'axios'

import type {
  ActivityEvent,
  AgentRun,
  AlertRule,
  AlignmentCandidate,
  AlternativeHypothesis,
  AnalysisJob,
  ApprovalInfo,
  ApprovalTrace,
  Artifact,
  ArtifactDiff,
  CaseRecord,
  ClaimEvidence,
  CoordinationCluster,
  CoordinationMember,
  IntegrityViews,
  CorrectionEvent,
  Debate,
  DebateDetail,
  DebateMessage,
  EvidenceSummary,
  MediaAsset,
  DeliveryAttempt,
  ExportJob,
  LexiconEntry,
  MediaAssetDetail,
  ModelCallTrace,
  Narrative,
  NarrativeDetail,
  NarrativeTimeline,
  NotificationEndpoint,
  NotificationEvent,
  ReviewItem,
  ReviewQueueItem,
  SemanticAnalysis,
  SemanticAnnotation,
  Subscription,
  MonitorAlert,
  MonitorDefinition,
  MonitorExecution,
  PlatformComparison,
  Project,
  PropagationEdgeState,
  PropagationGraphDTO,
  ProvenanceResponse,
  PostsPageDTO,
  PostsStatsDTO,
  QualityAssessment,
  RiskAssessment,
  RunEvent,
  RunTrace,
  SandboxHealth,
  SkillInfo,
  ToolSandboxCapabilities,
  SystemCapabilities,
  ToolCallTrace,
  TurnRecord,
  CircuitBreakerState,
  DeadLetterItem,
  DependencyHealth,
  IncidentRecord,
  KillSwitch,
  MemoryAccessEvent,
  MemoryConflict,
  MemoryMutationEntry,
  MemoryRecord,
  ResilienceHealthSummary,
  ContentSecurityPolicy,
  ContentSecurityAssessment,
  ContentSecuritySignal,
  ContentSecuritySummary,
  GuardrailDecision,
  GoalSummary,
  GoalDetail,
  CriterionInfo,
  CreatePlanRequest,
  PlanVersionInfo,
  PlanDetail,
  StepInfo,
  CompletionAssessmentInfo,
  TelemetryHealth,
  EvaluationDataset,
  EvaluationRunSummary,
  EvaluationRunDetail,
  ReleaseGateInfo,
  DriftResult,
  ApprovalInboxItem,
  ApprovalStats,
} from '@/types/api'

const baseURL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

export const http = axios.create({
  baseURL,
  timeout: 15_000,
})

export const api = {
  async getCapabilities(): Promise<SystemCapabilities> {
    const { data } = await http.get<SystemCapabilities>('/system/capabilities')
    return data
  },
  async listCases(): Promise<CaseRecord[]> {
    const { data } = await http.get<CaseRecord[]>('/cases')
    return data
  },
  async createCase(payload: {
    topic: string
    description: string
    platforms: string[]
    time_start?: string
    time_end?: string
    project_id?: string
  }): Promise<CaseRecord> {
    const { data } = await http.post<CaseRecord>('/cases', payload)
    return data
  },
  async listProjects(): Promise<Project[]> {
    const { data } = await http.get<Project[]>('/projects')
    return data
  },
  async createProject(title: string): Promise<Project> {
    const { data } = await http.post<Project>('/projects', { title })
    return data
  },
  async deleteProject(projectId: string): Promise<void> {
    await http.delete(`/projects/${projectId}`)
  },
  async getCase(caseId: string): Promise<CaseRecord> {
    const { data } = await http.get<CaseRecord>(`/cases/${caseId}`)
    return data
  },
  async renameCase(caseId: string, title: string): Promise<CaseRecord> {
    const { data } = await http.patch<CaseRecord>(`/cases/${caseId}`, { title })
    return data
  },
  async updateCase(
    caseId: string,
    payload: {
      title?: string
      topic?: string
      description?: string
      platforms?: string[]
      time_start?: string
      time_end?: string
    },
  ): Promise<CaseRecord> {
    const { data } = await http.patch<CaseRecord>(`/cases/${caseId}`, payload)
    return data
  },
  async deleteCase(caseId: string): Promise<void> {
    await http.delete(`/cases/${caseId}`)
  },
  async getPlatformComparison(caseId: string): Promise<PlatformComparison> {
    const { data } = await http.get<PlatformComparison>(
      `/cases/${caseId}/platform-comparison`,
    )
    return data
  },
  async getToolSandboxCapabilities(): Promise<ToolSandboxCapabilities> {
    const { data } = await http.get<ToolSandboxCapabilities>(
      '/system/tools/capabilities',
    )
    return data
  },
  async getSandboxHealth(): Promise<SandboxHealth> {
    const { data } = await http.get<SandboxHealth>('/system/sandbox/health')
    return data
  },
  async listSkills(): Promise<SkillInfo[]> {
    const { data } = await http.get<{ skills: SkillInfo[]; total: number }>(
      '/system/skills',
    )
    return data.skills
  },
  async createDebate(caseId: string, title?: string): Promise<Debate> {
    const { data } = await http.post<Debate>(`/cases/${caseId}/debates`, {
      title,
    })
    return data
  },
  async listDebates(caseId: string): Promise<Debate[]> {
    const { data } = await http.get<Debate[]>(`/cases/${caseId}/debates`)
    return data
  },
  async getDebate(debateId: string): Promise<DebateDetail> {
    const { data } = await http.get<DebateDetail>(`/cases/debates/${debateId}`)
    return data
  },
  async addDebateMessage(debateId: string, content: string): Promise<DebateMessage> {
    const { data } = await http.post<DebateMessage>(
      `/cases/debates/${debateId}/messages`,
      { content },
    )
    return data
  },
  async advanceDebate(debateId: string): Promise<DebateDetail> {
    const { data } = await http.post<DebateDetail>(
      `/cases/debates/${debateId}/advance`,
    )
    return data
  },
  async listTurns(caseId: string): Promise<TurnRecord[]> {
    const { data } = await http.get<TurnRecord[]>(`/cases/${caseId}/turns`)
    return data
  },
  async listCaseRuns(caseId: string): Promise<AgentRun[]> {
    const { data } = await http.get<AgentRun[]>(`/cases/${caseId}/runs`)
    return data
  },
  async listArtifacts(caseId: string): Promise<Artifact[]> {
    const { data } = await http.get<Artifact[]>(`/cases/${caseId}/artifacts`)
    return data
  },
  async getEvidenceSummary(caseId: string): Promise<EvidenceSummary> {
    const { data } = await http.get<EvidenceSummary>(`/cases/${caseId}/evidence-summary`)
    return data
  },
  async getArtifact(artifactId: string): Promise<Artifact> {
    const { data } = await http.get<Artifact>(`/artifacts/${artifactId}`)
    return data
  },
  async listArtifactVersions(artifactId: string): Promise<Artifact[]> {
    const { data } = await http.get<Artifact[]>(`/artifacts/${artifactId}/versions`)
    return data
  },
  async diffArtifacts(artifactId: string, against: string): Promise<ArtifactDiff> {
    const { data } = await http.get<ArtifactDiff>(`/artifacts/${artifactId}/diff`, {
      params: { against },
    })
    return data
  },
  async downloadReport(artifactId: string): Promise<void> {
    const response = await http.get<Blob>(`/artifacts/${artifactId}/download`, {
      responseType: 'blob',
    })
    const disposition = response.headers['content-disposition'] || ''
    const match = disposition.match(/filename="?([^";]+)"?/)
    const filename = match
      ? match[1]
      : `report-${artifactId.slice(0, 8)}.html`
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  },
  async sendMessage(
    caseId: string,
    content: string,
    approveCrawl = false,
    artifactId?: string,
    uiContext?: Record<string, unknown>,
  ): Promise<AgentRun> {
    const { data } = await http.post<AgentRun>(`/cases/${caseId}/messages`, {
      content,
      approve_crawl: approveCrawl,
      ...(artifactId ? { artifact_id: artifactId } : {}),
      ...(uiContext ? { ui_context: uiContext } : {}),
    })
    return data
  },
  async steerRun(runId: string, content: string): Promise<{ id: string }> {
    const { data } = await http.post<{ id: string }>(`/runs/${runId}/steering`, {
      content,
    })
    return data
  },
  async reviewClaim(
    caseId: string,
    claimId: string,
    confirmed: boolean,
    note = '',
  ): Promise<ClaimEvidence> {
    const { data } = await http.post<ClaimEvidence>(
      `/cases/${caseId}/claims/${claimId}/review`,
      { confirmed, note },
    )
    return data
  },
  async confirmPropagationEdge(
    caseId: string,
    edgeId: string,
    confirmed: boolean,
    note = '',
  ): Promise<Record<string, unknown>> {
    const { data } = await http.post<Record<string, unknown>>(
      `/cases/${caseId}/propagation-edges/${edgeId}/confirmation`,
      { confirmed, note },
    )
    return data
  },
  async listPropagationEdgeStates(
    caseId: string,
  ): Promise<PropagationEdgeState[]> {
    const { data } = await http.get<PropagationEdgeState[]>(
      `/cases/${caseId}/propagation-edges`,
    )
    return data
  },
  async getPropagationGraph(caseId: string): Promise<PropagationGraphDTO> {
    const { data } = await http.get<PropagationGraphDTO>(
      `/cases/${caseId}/propagation-graph`,
    )
    return data
  },
  async getEvidenceProvenance(
    caseId: string,
    evidenceId: string,
  ): Promise<ProvenanceResponse> {
    const { data } = await http.get<ProvenanceResponse>(
      `/cases/${caseId}/provenance/evidence/${evidenceId}`,
    )
    return data
  },
  async listCasePosts(
    caseId: string,
    params: {
      platform?: string
      q?: string
      from?: string
      to?: string
      limit?: number
      offset?: number
    } = {},
  ): Promise<PostsPageDTO> {
    const { data } = await http.get<PostsPageDTO>(`/cases/${caseId}/posts`, {
      params,
    })
    return data
  },
  async getPostStats(caseId: string): Promise<PostsStatsDTO> {
    const { data } = await http.get<PostsStatsDTO>(`/cases/${caseId}/posts:stats`)
    return data
  },
  async getRun(runId: string): Promise<AgentRun> {
    const { data } = await http.get<AgentRun>(`/runs/${runId}`)
    return data
  },
  async cancelRun(runId: string): Promise<AgentRun> {
    const { data } = await http.post<AgentRun>(`/runs/${runId}/cancel`)
    return data
  },
  async approveRun(
    runId: string,
    approvalId: string,
    decision: boolean,
    note?: string,
  ): Promise<AgentRun> {
    const { data } = await http.post<AgentRun>(`/runs/${runId}/approve`, {
      approval_id: approvalId,
      decision: decision ? 'approve' : 'reject',
      note: note || null,
    })
    return data
  },
  async resumeRun(runId: string): Promise<AgentRun> {
    const { data } = await http.post<AgentRun>(`/runs/${runId}/resume`)
    return data
  },
  async getRunTrace(runId: string): Promise<RunTrace> {
    const { data } = await http.get<RunTrace>(`/runs/${runId}/trace`)
    return data
  },
  async listRunEvents(runId: string, afterId = 0): Promise<RunEvent[]> {
    const { data } = await http.get<RunEvent[]>(`/runs/${runId}/events`, {
      params: { after_id: afterId },
    })
    return data
  },
  runEventStreamUrl(runId: string, cursor = 0): string {
    const normalized = baseURL.startsWith('http')
      ? baseURL
      : `${window.location.origin}${baseURL}`
    return `${normalized}/runs/${runId}/events/stream?cursor=${cursor}`
  },

  // ---- 持续监测与告警（01） ----

  async listMonitors(caseId: string): Promise<MonitorDefinition[]> {
    const { data } = await http.get<MonitorDefinition[]>(
      `/cases/${caseId}/monitors`,
    )
    return data
  },
  async createMonitor(
    caseId: string,
    payload: {
      name: string
      schedule_type?: 'interval' | 'cron'
      interval_seconds?: number | null
      cron?: string | null
      timezone?: string
      query_spec?: Record<string, unknown>
      platforms?: string[]
      account_watchlist?: Record<string, unknown>[]
      lookback_seconds?: number
      analysis_policy?: Record<string, unknown>
    },
  ): Promise<MonitorDefinition> {
    const { data } = await http.post<MonitorDefinition>(
      `/cases/${caseId}/monitors`,
      payload,
    )
    return data
  },
  async updateMonitor(
    caseId: string,
    monitorId: string,
    payload: { version: number } & Record<string, unknown>,
  ): Promise<MonitorDefinition> {
    const { data } = await http.patch<MonitorDefinition>(
      `/cases/${caseId}/monitors/${monitorId}`,
      payload,
    )
    return data
  },
  async deleteMonitor(caseId: string, monitorId: string): Promise<void> {
    await http.delete(`/cases/${caseId}/monitors/${monitorId}`)
  },
  async pauseMonitor(caseId: string, monitorId: string): Promise<MonitorDefinition> {
    const { data } = await http.post<MonitorDefinition>(
      `/cases/${caseId}/monitors/${monitorId}:pause`,
    )
    return data
  },
  async resumeMonitor(caseId: string, monitorId: string): Promise<MonitorDefinition> {
    const { data } = await http.post<MonitorDefinition>(
      `/cases/${caseId}/monitors/${monitorId}:resume`,
    )
    return data
  },
  async runMonitorNow(
    caseId: string,
    monitorId: string,
    idempotencyKey?: string,
  ): Promise<MonitorExecution> {
    const { data } = await http.post<MonitorExecution>(
      `/cases/${caseId}/monitors/${monitorId}:run-now`,
      { idempotency_key: idempotencyKey || null },
    )
    return data
  },
  async listMonitorExecutions(
    caseId: string,
    monitorId: string,
  ): Promise<MonitorExecution[]> {
    const { data } = await http.get<MonitorExecution[]>(
      `/cases/${caseId}/monitors/${monitorId}/executions`,
    )
    return data
  },
  async listMonitorRules(caseId: string, monitorId: string): Promise<AlertRule[]> {
    const { data } = await http.get<AlertRule[]>(
      `/cases/${caseId}/monitors/${monitorId}/rules`,
    )
    return data
  },
  async createMonitorRule(
    caseId: string,
    monitorId: string,
    payload: {
      rule_type: string
      parameters?: Record<string, unknown>
      severity?: string
      cooldown_seconds?: number
      enabled?: boolean
    },
  ): Promise<AlertRule> {
    const { data } = await http.post<AlertRule>(
      `/cases/${caseId}/monitors/${monitorId}/rules`,
      payload,
    )
    return data
  },
  async listAlerts(caseId: string, status?: string): Promise<MonitorAlert[]> {
    const { data } = await http.get<MonitorAlert[]>(`/cases/${caseId}/alerts`, {
      params: status ? { status } : {},
    })
    return data
  },
  async acknowledgeAlert(caseId: string, alertId: string): Promise<MonitorAlert> {
    const { data } = await http.post<MonitorAlert>(
      `/cases/${caseId}/alerts/${alertId}:acknowledge`,
      {},
    )
    return data
  },
  async resolveAlert(caseId: string, alertId: string): Promise<MonitorAlert> {
    const { data } = await http.post<MonitorAlert>(
      `/cases/${caseId}/alerts/${alertId}:resolve`,
      {},
    )
    return data
  },

  // ---- 多模态媒体流水线（04） ----

  async listMediaAssets(caseId: string): Promise<MediaAsset[]> {
    const { data } = await http.get<MediaAsset[]>(`/cases/${caseId}/media`)
    return data
  },
  async getMediaAsset(caseId: string, assetId: string): Promise<MediaAssetDetail> {
    const { data } = await http.get<MediaAssetDetail>(
      `/cases/${caseId}/media/${assetId}`,
    )
    return data
  },
  async backfillMedia(caseId: string): Promise<{ enqueued: number }> {
    const { data } = await http.post<{ enqueued: number }>(
      `/cases/${caseId}/media/backfill`,
      { limit: 100 },
    )
    return data
  },

  // ---- 跨平台对齐（06） ----

  async listAlignmentCandidates(
    caseId: string,
    decision?: string,
  ): Promise<AlignmentCandidate[]> {
    const { data } = await http.get<AlignmentCandidate[]>(
      `/cases/${caseId}/alignments/candidates`,
      { params: decision ? { decision } : {} },
    )
    return data
  },
  async analyzeAlignments(caseId: string): Promise<{ job_id: string; status: string }> {
    const { data } = await http.post<{ job_id: string; status: string }>(
      `/cases/${caseId}/alignments:analyze`,
    )
    return data
  },
  async reviewAlignmentCandidate(
    caseId: string,
    candidateId: string,
    action: 'confirm' | 'reject' | 'reopen',
  ): Promise<AlignmentCandidate> {
    const { data } = await http.post<AlignmentCandidate>(
      `/cases/${caseId}/alignments/${candidateId}:${action}`,
      {},
    )
    return data
  },

  // ---- 完整性风险（07） ----

  async listRiskAssessments(
    caseId: string,
    riskType?: string,
    band?: string,
  ): Promise<RiskAssessment[]> {
    const { data } = await http.get<RiskAssessment[]>(
      `/cases/${caseId}/integrity/assessments`,
      { params: { risk_type: riskType, band } },
    )
    return data
  },
  async analyzeIntegrity(caseId: string): Promise<{ job_id: string; status: string }> {
    const { data } = await http.post<{ job_id: string; status: string }>(
      `/cases/${caseId}/integrity:analyze`,
    )
    return data
  },
  async reviewRiskAssessment(
    caseId: string,
    assessmentId: string,
    status: 'reviewed_likely' | 'reviewed_unlikely' | 'inconclusive',
    note = '',
  ): Promise<RiskAssessment> {
    const { data } = await http.post<RiskAssessment>(
      `/cases/${caseId}/integrity/assessments/${assessmentId}:review`,
      { status, note },
    )
    return data
  },
  async listCoordinationClusters(caseId: string): Promise<CoordinationCluster[]> {
    const { data } = await http.get<CoordinationCluster[]>(
      `/cases/${caseId}/integrity/clusters`,
    )
    return data
  },
  async listCoordinationMembers(
    caseId: string,
    clusterId: string,
  ): Promise<CoordinationMember[]> {
    const { data } = await http.get<CoordinationMember[]>(
      `/cases/${caseId}/integrity/clusters/${clusterId}/members`,
    )
    return data
  },
  async getIntegrityViews(caseId: string): Promise<IntegrityViews> {
    const { data } = await http.get<IntegrityViews>(`/cases/${caseId}/integrity/views`)
    return data
  },
  async getAnalysisJob(caseId: string, jobId: string): Promise<AnalysisJob> {
    const { data } = await http.get<AnalysisJob>(`/cases/${caseId}/jobs/${jobId}`)
    return data
  },

  // ---- 不确定性与偏差（08） ----

  async listQualityAssessments(caseId: string): Promise<QualityAssessment[]> {
    const { data } = await http.get<{ assessments: QualityAssessment[]; conclusions: unknown[] }>(
      `/cases/${caseId}/quality/summary`,
    )
    return data.assessments ?? []
  },
  async listHypotheses(caseId: string): Promise<AlternativeHypothesis[]> {
    const { data } = await http.get<AlternativeHypothesis[]>(
      `/cases/${caseId}/hypotheses`,
    )
    return data
  },
  async createHypothesis(
    caseId: string,
    payload: { statement: string; prediction?: string; proposer?: string },
  ): Promise<AlternativeHypothesis> {
    const { data } = await http.post<AlternativeHypothesis>(
      `/cases/${caseId}/hypotheses`,
      payload,
    )
    return data
  },

  // ---- 09 审核工作台 ----

  async submitReviewItem(
    caseId: string,
    payload: {
      object_type: string
      object_id: string
      summary?: string
      priority?: number
      risk_level?: string
    },
  ): Promise<ReviewItem> {
    const { data } = await http.post<ReviewItem>(
      '/cases/' + caseId + '/reviews/items',
      payload,
    )
    return data
  },
  async listReviewQueue(
    caseId: string,
    status?: string,
    objectType?: string,
  ): Promise<{ total: number; items: ReviewQueueItem[] }> {
    const { data } = await http.get<{ total: number; items: ReviewQueueItem[] }>(
      '/cases/' + caseId + '/reviews/queue',
      { params: { status, object_type: objectType } },
    )
    return data
  },
  async reviewClaimItem(caseId: string, itemId: string): Promise<ReviewItem> {
    const { data } = await http.post<ReviewItem>(
      '/cases/' + caseId + '/reviews/' + itemId + ':claim',
    )
    return data
  },
  async reviewReleaseItem(caseId: string, itemId: string): Promise<ReviewItem> {
    const { data } = await http.post<ReviewItem>(
      '/cases/' + caseId + '/reviews/' + itemId + ':release',
    )
    return data
  },
  async reviewDecide(
    caseId: string,
    itemId: string,
    payload: {
      decision: string
      reason?: string
      structured_patch?: Record<string, unknown>
      expected_version?: number
    },
  ): Promise<ReviewItem> {
    const { data } = await http.post<ReviewItem>(
      '/cases/' + caseId + '/reviews/' + itemId + '/decisions',
      payload,
    )
    return data
  },
  async reviewReopen(caseId: string, itemId: string): Promise<ReviewItem> {
    const { data } = await http.post<ReviewItem>(
      '/cases/' + caseId + '/reviews/' + itemId + ':reopen',
    )
    return data
  },
  async listCaseActivity(caseId: string): Promise<{ events: ActivityEvent[] }> {
    const { data } = await http.get<{ events: ActivityEvent[] }>(
      '/cases/' + caseId + '/activity',
    )
    return data
  },

  // ---- 10 叙事 ----

  async analyzeNarratives(
    caseId: string,
  ): Promise<{ created: number; updated: number; total: number }> {
    const { data } = await http.post<{ created: number; updated: number; total: number }>(
      '/cases/' + caseId + '/narratives/analyze',
    )
    return data
  },
  async listNarratives(caseId: string): Promise<Narrative[]> {
    const { data } = await http.get<Narrative[]>('/cases/' + caseId + '/narratives')
    return data
  },
  async getNarrative(caseId: string, narrativeId: string): Promise<NarrativeDetail> {
    const { data } = await http.get<NarrativeDetail>(
      '/cases/' + caseId + '/narratives/' + narrativeId,
    )
    return data
  },
  async getNarrativeTimeline(
    caseId: string,
    narrativeId: string,
  ): Promise<NarrativeTimeline> {
    const { data } = await http.get<NarrativeTimeline>(
      '/cases/' + caseId + '/narratives/' + narrativeId + '/timeline',
    )
    return data
  },
  async listCorrections(caseId: string): Promise<CorrectionEvent[]> {
    const { data } = await http.get<CorrectionEvent[]>('/cases/' + caseId + '/corrections')
    return data
  },
  async addCorrection(
    caseId: string,
    payload: { content: string; correction_type?: string; target_narrative_id?: string },
  ): Promise<CorrectionEvent> {
    const { data } = await http.post<CorrectionEvent>(
      '/cases/' + caseId + '/corrections',
      payload,
    )
    return data
  },

  // ---- 11 语义分析 ----

  async listLexicon(
    caseId: string,
    domain?: string,
    platform?: string,
  ): Promise<LexiconEntry[]> {
    const { data } = await http.get<LexiconEntry[]>(
      '/cases/' + caseId + '/semantics/lexicon',
      { params: { domain, platform } },
    )
    return data
  },
  async addLexiconEntry(
    caseId: string,
    payload: {
      term: string
      meaning?: string
      domain?: string
      platform?: string
      review_state?: string
    },
  ): Promise<LexiconEntry> {
    const { data } = await http.post<LexiconEntry>(
      '/cases/' + caseId + '/semantics/lexicon',
      payload,
    )
    return data
  },
  async analyzeSemantics(
    caseId: string,
    payload: {
      text: string
      tasks?: string[]
      source_id?: string
      platform?: string
      domain?: string
    },
  ): Promise<SemanticAnalysis> {
    const { data } = await http.post<SemanticAnalysis>(
      '/cases/' + caseId + '/semantics/analyze',
      payload,
    )
    return data
  },
  async listSemanticAnnotations(
    caseId: string,
    sourceId?: string,
  ): Promise<SemanticAnnotation[]> {
    const { data } = await http.get<SemanticAnnotation[]>(
      '/cases/' + caseId + '/semantics/annotations',
      { params: { source_id: sourceId } },
    )
    return data
  },

  // ---- 13 订阅与通知 ----

  async createSubscription(
    caseId: string,
    payload: {
      name?: string
      event_filters?: string[]
      severity?: string
      channel?: string
    },
  ): Promise<Subscription> {
    const { data } = await http.post<Subscription>(
      '/cases/' + caseId + '/subscriptions',
      payload,
    )
    return data
  },
  async listSubscriptions(
    caseId: string,
  ): Promise<{ subscriptions: Subscription[] }> {
    const { data } = await http.get<{ subscriptions: Subscription[] }>(
      '/cases/' + caseId + '/subscriptions',
    )
    return data
  },
  async setSubscriptionEnabled(
    caseId: string,
    subId: string,
    enabled: boolean,
  ): Promise<Subscription> {
    const action = enabled ? 'resume' : 'pause'
    const { data } = await http.post<Subscription>(
      '/cases/' + caseId + '/subscriptions/' + subId + ':' + action,
    )
    return data
  },
  async createNotificationEndpoint(
    caseId: string,
    payload: { name?: string; url: string; secret_ref?: string },
  ): Promise<NotificationEndpoint> {
    const { data } = await http.post<NotificationEndpoint>(
      '/cases/' + caseId + '/notification-endpoints',
      payload,
    )
    return data
  },
  async verifyNotificationEndpoint(
    caseId: string,
    endpointId: string,
  ): Promise<{ id: string; verification_state: string }> {
    const { data } = await http.post<{ id: string; verification_state: string }>(
      '/cases/' + caseId + '/notification-endpoints/' + endpointId + ':verify',
    )
    return data
  },
  async listNotificationEndpoints(
    caseId: string,
  ): Promise<{ endpoints: NotificationEndpoint[] }> {
    const { data } = await http.get<{ endpoints: NotificationEndpoint[] }>(
      '/cases/' + caseId + '/notification-endpoints',
    )
    return data
  },
  async enqueueNotificationEvent(
    caseId: string,
    payload: {
      event_type: string
      severity?: string
      data?: Record<string, unknown>
    },
  ): Promise<{ event_id: string; status: string }> {
    const { data } = await http.post<{ event_id: string; status: string }>(
      '/cases/' + caseId + '/notification-events',
      payload,
    )
    return data
  },
  async listNotifications(
    caseId: string,
  ): Promise<{ events: NotificationEvent[] }> {
    const { data } = await http.get<{ events: NotificationEvent[] }>(
      '/cases/' + caseId + '/notifications',
    )
    return data
  },
  async listDeliveries(
    caseId: string,
  ): Promise<{ deliveries: DeliveryAttempt[] }> {
    const { data } = await http.get<{ deliveries: DeliveryAttempt[] }>(
      '/cases/' + caseId + '/deliveries',
    )
    return data
  },
  async createShareLink(
    caseId: string,
    payload: {
      target_type: string
      target_id: string
      expires_in_hours?: number
    },
  ): Promise<{ token: string; link_id: string }> {
    const { data } = await http.post<{ token: string; link_id: string }>(
      '/cases/' + caseId + '/share-links',
      payload,
    )
    return data
  },
  async createExportJob(
    caseId: string,
    payload: { scope?: string; format?: string; redaction_policy?: string },
  ): Promise<{ id: string; status: string }> {
    const { data } = await http.post<{ id: string; status: string }>(
      '/cases/' + caseId + '/export-jobs',
      payload,
    )
    return data
  },
  async listExportJobs(caseId: string): Promise<{ jobs: ExportJob[] }> {
    const { data } = await http.get<{ jobs: ExportJob[] }>(
      '/cases/' + caseId + '/export-jobs',
    )
    return data
  },

  // ---- M16 内容安全与注入防御 ----

  async getContentSecurityPolicy(): Promise<ContentSecurityPolicy> {
    const { data } = await http.get<ContentSecurityPolicy>(
      '/system/content-security/policy',
    )
    return data
  },
  async listContentSecurityAssessments(params?: {
    run_id?: string
    trust_level?: string
    disposition?: string
    limit?: number
  }): Promise<ContentSecurityAssessment[]> {
    const { data } = await http.get<ContentSecurityAssessment[]>(
      '/system/content-security/assessments',
      { params },
    )
    return data
  },
  async listGuardrailDecisions(params?: {
    run_id?: string
    stage?: string
    decision?: string
    limit?: number
  }): Promise<GuardrailDecision[]> {
    const { data } = await http.get<GuardrailDecision[]>(
      '/system/content-security/decisions',
      { params },
    )
    return data
  },
  async getContentSecuritySummary(): Promise<ContentSecuritySummary> {
    const { data } = await http.get<ContentSecuritySummary>(
      '/system/content-security/summary',
    )
    return data
  },
  async assessContent(payload: {
    text: string
    trust_level?: string
    source_type?: string
  }): Promise<{
    score: number
    signals: ContentSecuritySignal[]
    disposition: string
    reason: string
    context_preview: string
  }> {
    const { data } = await http.post(
      '/system/content-security/assess',
      payload,
    )
    return data
  },

  // ---- M17 显式目标、计划图与完成条件 ----

  async createGoal(
    caseId: string,
    payload: { objective: string; constraints?: string[]; priority?: string },
  ): Promise<{ goal: GoalSummary; criteria: CriterionInfo[]; complexity: string }> {
    const { data } = await http.post(
      '/cases/' + caseId + '/goals',
      payload,
    )
    return data
  },
  async listGoals(caseId: string): Promise<GoalSummary[]> {
    const { data } = await http.get<GoalSummary[]>(
      '/cases/' + caseId + '/goals',
    )
    return data
  },
  async getGoalDetail(goalId: string): Promise<GoalDetail> {
    const { data } = await http.get<GoalDetail>('/goals/' + goalId)
    return data
  },
  async transitionGoal(
    goalId: string,
    payload: { target: string; reason?: string },
  ): Promise<GoalSummary> {
    const { data } = await http.post<GoalSummary>(
      '/goals/' + goalId + '/transition',
      payload,
    )
    return data
  },
  async createPlan(
    goalId: string,
    payload: CreatePlanRequest,
  ): Promise<{
    plan_version: PlanVersionInfo
    steps: StepInfo[]
    step_id_by_key: Record<string, string>
  }> {
    const { data } = await http.post(
      '/goals/' + goalId + '/plans',
      payload,
    )
    return data
  },
  async getPlan(planVersionId: string): Promise<PlanDetail> {
    const { data } = await http.get<PlanDetail>(
      '/goals/plans/' + planVersionId,
    )
    return data
  },
  async declareStep(
    planVersionId: string,
    stepId: string,
    payload: { action: string; reason?: string },
  ): Promise<{ id: string; step_key: string; status: string }> {
    const { data } = await http.post(
      '/goals/plans/' + planVersionId + '/steps/' + stepId + '/declare',
      payload,
    )
    return data
  },
  async addStepEvidence(
    planVersionId: string,
    stepId: string,
    payload: { evidence_type: string; ref_id: string; ref_kind: string; payload?: object },
  ): Promise<{ id: string; step_id: string; evidence_type: string; ref_id: string }> {
    const { data } = await http.post(
      '/goals/plans/' + planVersionId + '/steps/' + stepId + '/evidence',
      payload,
    )
    return data
  },
  async assessGoal(
    goalId: string,
    payload: { plan_version_id: string },
  ): Promise<{ assessment: CompletionAssessmentInfo; gaps: string[] }> {
    const { data } = await http.post(
      '/goals/' + goalId + '/assess',
      payload,
    )
    return data
  },

  // ---- M19 可观测性与 SLO ----

  async getTelemetryHealth(): Promise<TelemetryHealth> {
    const { data } = await http.get<TelemetryHealth>(
      '/system/telemetry-health',
    )
    return data
  },

  // ---- M20 评测与门禁 ----

  async registerEvaluationDataset(payload: {
    manifest: {
      name: string
      version: string
      task: string
      source?: string
      license: string
      schema_version?: string
    },
    examples: Array<{ example_id: string; input?: unknown; gold?: unknown }>,
  }): Promise<{ manifest: EvaluationDataset; example_count: number; content_hash: string }> {
    const { data } = await http.post(
      '/system/evaluation/datasets',
      payload,
    )
    return data
  },
  async listEvaluationDatasets(limit?: number): Promise<EvaluationDataset[]> {
    const { data } = await http.get<EvaluationDataset[]>(
      '/system/evaluation/datasets',
      { params: { limit } },
    )
    return data
  },
  async runEvaluation(payload: {
    suite?: string
    candidate_version?: string
    baseline_version?: string
    dataset_manifest_id: string
    evaluator_names?: string[]
    commit?: string
  }): Promise<{ run: EvaluationRunSummary; aggregate: Record<string, number>; failed: unknown[] }> {
    const { data } = await http.post('/system/evaluation/runs', payload)
    return data
  },
  async listEvaluationRuns(suite?: string, limit?: number): Promise<EvaluationRunSummary[]> {
    const { data } = await http.get<EvaluationRunSummary[]>(
      '/system/evaluation/runs',
      { params: { suite, limit } },
    )
    return data
  },
  async getEvaluationRun(runId: string): Promise<EvaluationRunDetail> {
    const { data } = await http.get<EvaluationRunDetail>(
      '/system/evaluation/runs/' + runId,
    )
    return data
  },
  async evaluateGates(
    runId: string,
    payload?: { exempted_by?: string; exempt_reason?: string },
  ): Promise<{ gate_results: Array<{ gate_name: string; decision: string; reason: string }> }> {
    const { data } = await http.post(
      '/system/evaluation/runs/' + runId + ':gates',
      payload || {},
    )
    return data
  },
  async listReleaseGates(suite?: string): Promise<ReleaseGateInfo[]> {
    const { data } = await http.get<ReleaseGateInfo[]>(
      '/system/evaluation/gates',
      { params: { suite } },
    )
    return data
  },
  async createReleaseGate(payload: {
    name: string
    suite?: string
    thresholds?: Record<string, number>
    relative_regression_limits?: Record<string, number>
    mandatory?: boolean
  }): Promise<{ id: string; name: string; suite: string }> {
    const { data } = await http.post('/system/evaluation/gates', payload)
    return data
  },
  async checkDrift(payload: {
    baseline: Record<string, number>
    current: Record<string, number>
  }): Promise<DriftResult> {
    const { data } = await http.post<DriftResult>(
      '/system/evaluation/drift',
      payload,
    )
    return data
  },

  // ---- M21 广义人工介入 ----

  async listApprovals(params?: {
    case_id?: string
    run_id?: string
    status?: string
    approval_type?: string
    risk_level?: string
    limit?: number
  }): Promise<ApprovalInboxItem[]> {
    const { data } = await http.get<ApprovalInboxItem[]>(
      '/approvals',
      { params },
    )
    return data
  },
  async getApproval(approvalId: string): Promise<ApprovalInboxItem> {
    const { data } = await http.get<ApprovalInboxItem>(
      '/approvals/' + approvalId,
    )
    return data
  },
  async decideApproval(
    approvalId: string,
    payload: {
      decision: string
      note?: string
      edited_action?: object | null
      actor?: string
    },
  ): Promise<ApprovalInboxItem> {
    const { data } = await http.post<ApprovalInboxItem>(
      '/approvals/' + approvalId + ':decide',
      payload,
    )
    return data
  },
  async getApprovalStats(): Promise<ApprovalStats> {
    const { data } = await http.get<ApprovalStats>(
      '/approvals/stats/summary',
    )
    return data
  },
  async expireOverdueApprovals(): Promise<{ expired: number }> {
    const { data } = await http.post<{ expired: number }>(
      '/approvals/expire-overdue',
    )
    return data
  },
  // ---- M22 故障隔离、降级与事故处置 ----

  async getResilienceHealth(): Promise<ResilienceHealthSummary> {
    const { data } = await http.get<ResilienceHealthSummary>(
      '/system/resilience/health',
    )
    return data
  },
  async listCircuitBreakers(): Promise<CircuitBreakerState[]> {
    const { data } = await http.get<CircuitBreakerState[]>(
      '/system/resilience/circuits',
    )
    return data
  },
  async getQueueBackpressure(): Promise<Record<string, unknown>> {
    const { data } = await http.get<Record<string, unknown>>(
      '/system/resilience/queues',
    )
    return data
  },
  async listDeadLetters(params?: { status?: string; limit?: number }) {
    const { data } = await http.get<DeadLetterItem[]>(
      '/system/resilience/dead-letters',
      { params },
    )
    return data
  },
  async retryDeadLetter(
    deadLetterId: string,
    payload: { actor?: string; reason?: string; approval_id?: string | null },
  ): Promise<DeadLetterItem> {
    const { data } = await http.post<DeadLetterItem>(
      '/system/resilience/dead-letters/' + deadLetterId + ':retry',
      payload,
    )
    return data
  },
  async resolveDeadLetter(
    deadLetterId: string,
    payload: { actor?: string; reason?: string; approval_id?: string | null },
  ): Promise<DeadLetterItem> {
    const { data } = await http.post<DeadLetterItem>(
      '/system/resilience/dead-letters/' + deadLetterId + ':resolve',
      payload,
    )
    return data
  },
  async listKillSwitches(activeOnly = false): Promise<KillSwitch[]> {
    const { data } = await http.get<KillSwitch[]>(
      '/system/resilience/kill-switches',
      { params: { active_only: activeOnly } },
    )
    return data
  },
  async enableKillSwitch(payload: {
    scope: string
    target: string
    reason: string
    actor?: string
    approval_id?: string | null
  }): Promise<KillSwitch> {
    const { data } = await http.post<KillSwitch>(
      '/system/resilience/kill-switches',
      payload,
    )
    return data
  },
  async disableKillSwitch(
    killSwitchId: string,
    payload: { actor?: string; reason?: string; approval_id: string },
  ): Promise<KillSwitch> {
    const { data } = await http.post<KillSwitch>(
      '/system/resilience/kill-switches/' + killSwitchId + ':disable',
      payload,
    )
    return data
  },
  async listIncidents(params?: { status?: string; limit?: number }) {
    const { data } = await http.get<IncidentRecord[]>(
      '/system/resilience/incidents',
      { params },
    )
    return data
  },
  async createIncident(payload: {
    title: string
    severity?: string
    impact?: string
    metrics?: Record<string, unknown>
  }): Promise<IncidentRecord> {
    const { data } = await http.post<IncidentRecord>(
      '/system/resilience/incidents',
      payload,
    )
    return data
  },
  async getIncident(incidentId: string): Promise<IncidentRecord> {
    const { data } = await http.get<IncidentRecord>(
      '/system/resilience/incidents/' + incidentId,
    )
    return data
  },
  async closeIncident(
    incidentId: string,
    payload: { recovery?: Record<string, unknown>; retro?: Record<string, unknown> },
  ): Promise<IncidentRecord> {
    const { data } = await http.post<IncidentRecord>(
      '/system/resilience/incidents/' + incidentId + ':close',
      payload,
    )
    return data
  },

  // ---- M23 记忆安全与用户可控治理 ----

  async listMemories(params?: {
    scope?: string
    memory_type?: string
    status?: string
    source_type?: string
    include_inactive?: boolean
    limit?: number
  }): Promise<MemoryRecord[]> {
    const { data } = await http.get<MemoryRecord[]>('/memories', { params })
    return data
  },
  async getMemory(memoryId: string): Promise<MemoryRecord> {
    const { data } = await http.get<MemoryRecord>('/memories/' + memoryId)
    return data
  },
  async correctMemory(
    memoryId: string,
    payload: { content: string; actor?: string; reason?: string; importance?: number },
  ): Promise<MemoryRecord> {
    const { data } = await http.post<MemoryRecord>(
      '/memories/' + memoryId + ':correct',
      payload,
    )
    return data
  },
  async disableMemory(
    memoryId: string,
    payload: { actor?: string; reason?: string },
  ): Promise<MemoryRecord> {
    const { data } = await http.post<MemoryRecord>(
      '/memories/' + memoryId + ':disable',
      payload,
    )
    return data
  },
  async restoreMemory(
    memoryId: string,
    payload: { actor?: string; reason?: string },
  ): Promise<MemoryRecord> {
    const { data } = await http.post<MemoryRecord>(
      '/memories/' + memoryId + ':restore',
      payload,
    )
    return data
  },
  async deleteMemory(
    memoryId: string,
    payload: { actor?: string; reason?: string },
  ): Promise<MemoryRecord> {
    const { data } = await http.post<MemoryRecord>(
      '/memories/' + memoryId + ':delete',
      payload,
    )
    return data
  },
  async reviewMemory(
    memoryId: string,
    payload: { accept: boolean; actor?: string; reason?: string },
  ): Promise<MemoryRecord> {
    const { data } = await http.post<MemoryRecord>(
      '/memories/' + memoryId + ':review',
      payload,
    )
    return data
  },
  async getMemoryHistory(memoryId: string): Promise<MemoryMutationEntry[]> {
    const { data } = await http.get<MemoryMutationEntry[]>(
      '/memories/' + memoryId + '/history',
    )
    return data
  },
  async getMemoryAccesses(memoryId: string): Promise<MemoryAccessEvent[]> {
    const { data } = await http.get<MemoryAccessEvent[]>(
      '/memories/' + memoryId + '/accesses',
    )
    return data
  },
  async getMemoryConflicts(
    memoryId: string,
    unresolvedOnly = false,
  ): Promise<MemoryConflict[]> {
    const { data } = await http.get<MemoryConflict[]>(
      '/memories/' + memoryId + '/conflicts',
      { params: { unresolved_only: unresolvedOnly } },
    )
    return data
  },
  async reindexMemories(payload: {
    scope?: string
    status?: string
    memory_type?: string
    dry_run?: boolean
    embedding_version?: string
    limit?: number
  }): Promise<Record<string, unknown>> {
    const { data } = await http.post<Record<string, unknown>>(
      '/memories/reindex',
      payload,
    )
    return data
  },
  async runMemoryMaintenance(): Promise<Record<string, unknown>> {
    const { data } = await http.post<Record<string, unknown>>(
      '/memories/maintenance',
    )
    return data
  },
};
export type {
  ApprovalInfo,
  ApprovalTrace,
  ModelCallTrace,
  ToolCallTrace,
  CircuitBreakerState,
  DeadLetterItem,
  DependencyHealth,
  IncidentRecord,
  KillSwitch,
  MemoryAccessEvent,
  MemoryConflict,
  MemoryMutationEntry,
  MemoryRecord,
  ResilienceHealthSummary,
  ContentSecurityPolicy,
  ContentSecurityAssessment,
  ContentSecuritySignal,
  ContentSecuritySummary,
  GuardrailDecision,
  GoalSummary,
  GoalDetail,
  CriterionInfo,
  CreatePlanRequest,
  PlanVersionInfo,
  PlanDetail,
  StepInfo,
  CompletionAssessmentInfo,
  TelemetryHealth,
  EvaluationDataset,
  EvaluationRunSummary,
  EvaluationRunDetail,
  ReleaseGateInfo,
  DriftResult,
  ApprovalInboxItem,
  ApprovalStats,
}
