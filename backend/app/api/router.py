from fastapi import APIRouter

from app.api.routes import (
    a2a,
    alignment,
    approvals,
    artifacts,
    cases,
    collection_runs,
    collections,
    debates,
    evaluation,
    evidence,
    findings,
    goals,
    health,
    integrity,
    jobs,
    knowledge,
    media,
    memories,
    monitors,
    narratives,
    notifications,
    platform_comparison,
    posts,
    projects,
    propagation,
    provenance,
    reports,
    resilience,
    reviews,
    runs,
    security,
    semantics,
    signals,
    system,
    tasks,
    uncertainty,
    workspace,
)

api_router = APIRouter()
api_router.include_router(evidence.router, prefix="/cases", tags=["evidence"])
api_router.include_router(goals.router, prefix="/cases", tags=["goals"])
api_router.include_router(goals.goal_router, prefix="/goals", tags=["goals"])
api_router.include_router(
    evaluation.router, prefix="/system/evaluation", tags=["evaluation"]
)
api_router.include_router(
    platform_comparison.router, prefix="/cases", tags=["platform-comparison"]
)
api_router.include_router(debates.router, prefix="/cases", tags=["debates"])
api_router.include_router(monitors.router, prefix="/cases", tags=["monitoring"])
api_router.include_router(media.router, prefix="/cases", tags=["media"])
api_router.include_router(alignment.router, prefix="/cases", tags=["alignment"])
api_router.include_router(integrity.router, prefix="/cases", tags=["integrity"])
api_router.include_router(jobs.router, prefix="/cases", tags=["analysis-jobs"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(
    security.router, prefix="/system", tags=["content-security"]
)
api_router.include_router(
    resilience.router, prefix="/system/resilience", tags=["resilience"]
)
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])
api_router.include_router(knowledge.router, prefix="/cases", tags=["knowledge"])
api_router.include_router(
    propagation.router, prefix="/cases", tags=["propagation"]
)
api_router.include_router(posts.router, prefix="/cases", tags=["posts"])
api_router.include_router(memories.router, prefix="/memories", tags=["memories"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(runs.router, prefix="/runs", tags=["agent-runs"])
api_router.include_router(
    artifacts.router, prefix="/artifacts", tags=["artifacts"]
)
api_router.include_router(
    approvals.router, prefix="/approvals", tags=["approvals"]
)
api_router.include_router(a2a.router, prefix="/a2a", tags=["a2a"])
api_router.include_router(
    semantics.router, prefix="/cases", tags=["semantics"]
)
api_router.include_router(
    narratives.router, prefix="/cases", tags=["narratives"]
)
api_router.include_router(
    reviews.router, prefix="/cases", tags=["reviews"]
)
api_router.include_router(
    notifications.router, prefix="/cases", tags=["notifications"]
)
api_router.include_router(
    uncertainty.router, prefix="/cases", tags=["uncertainty"]
)
api_router.include_router(
    collections.router, prefix="/cases", tags=["collections"]
)
api_router.include_router(
    collection_runs.router, prefix="/cases", tags=["collection-runs"]
)
api_router.include_router(findings.router, prefix="/cases", tags=["findings"])
api_router.include_router(
    provenance.router, prefix="/cases", tags=["provenance"]
)
api_router.include_router(signals.router, prefix="/signals", tags=["signals"])
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(reports.case_router, prefix="/cases", tags=["reports"])
