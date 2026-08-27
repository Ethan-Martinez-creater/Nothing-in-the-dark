from __future__ import annotations

import asyncio
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from app.application.repositories import ApplicationRepository
from app.domain.enums import EventType
from app.harness.agents import CoordinatorAgent
from app.harness.state import AnalysisState
from app.harness.tools import ToolRegistry


class CaseAnalysisGraph:
    """LEGACY fixture. Do not use on the production path.

    Production analysis is the model-driven Agent Loop (``/messages`` +
    GraphWorker). This fixed intake→plan→collect→… graph is kept so
    ``tests/test_runner_integration.py`` can still exercise the historical
    Task/Event/Artifact shape. It must not be recovered at process start.
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        tools: ToolRegistry,
    ) -> None:
        self._repository = repository
        self._tools = tools
        self._coordinator = CoordinatorAgent()
        self._graph = self._build().compile()

    def _build(self) -> StateGraph[AnalysisState]:
        graph = StateGraph(AnalysisState)
        graph.add_node("intake", self._intake)
        graph.add_node("plan", self._plan)
        graph.add_node("collect", self._collect)
        graph.add_node("opinion", self._opinion)
        graph.add_node("propagation", self._propagation)
        graph.add_node("verification", self._verification)
        graph.add_node("report", self._report)
        graph.add_edge(START, "intake")
        graph.add_edge("intake", "plan")
        graph.add_edge("plan", "collect")
        graph.add_edge("collect", "opinion")
        graph.add_edge("opinion", "propagation")
        graph.add_edge("propagation", "verification")
        graph.add_edge("verification", "report")
        graph.add_edge("report", END)
        return graph

    async def run(self, state: AnalysisState) -> AnalysisState:
        result = await self._graph.ainvoke(state)
        return cast(AnalysisState, result)

    async def _emit(
        self,
        state: AnalysisState,
        *,
        stage: str,
        message: str,
        progress: float,
        event_type: EventType = EventType.PROGRESS,
        payload: dict[str, object] | None = None,
    ) -> None:
        await self._repository.update_task(
            state["task_id"],
            current_stage=stage,
            progress=progress,
        )
        await self._repository.add_event(
            state["task_id"],
            event_type=event_type,
            stage=stage,
            message=message,
            progress=progress,
            payload=payload,
        )
        await asyncio.sleep(0.12)

    async def _intake(self, state: AnalysisState) -> dict[str, Any]:
        await self._emit(
            state,
            stage="intake",
            message="已确认主题、平台和分析边界",
            progress=0.08,
            payload={"agent": "coordinator_agent"},
        )
        return {}

    async def _plan(self, state: AnalysisState) -> dict[str, Any]:
        plan = self._coordinator.plan(
            topic=state["topic"],
            platforms=state["platforms"],
            options=state["options"],
        )
        await self._emit(
            state,
            stage="planning",
            message="Coordinator 已生成动态任务计划",
            progress=0.16,
            payload={"agent": "coordinator_agent", "plan": plan},
        )
        return {"plan": plan}

    async def _collect(self, state: AnalysisState) -> dict[str, Any]:
        result = await self._tools.invoke(
            "collect_social_posts",
            {
                "topic": state["topic"],
                "platforms": state["platforms"],
                "time_range": state["time_range"],
            },
        )
        posts = result["posts"]
        is_demo = any(bool(post.get("is_demo")) for post in posts)
        await self._emit(
            state,
            stage="collection",
            message=f"采集并归一化 {len(posts)} 条社交平台样本",
            progress=0.32,
            payload={
                "skill": "social_crawl",
                "tool": "collect_social_posts",
                "count": len(posts),
                "is_demo": is_demo,
            },
        )
        return {"posts": posts, "is_demo": is_demo}

    async def _opinion(self, state: AnalysisState) -> dict[str, Any]:
        result = await self._tools.invoke("analyze_opinion", {"posts": state["posts"]})
        await self._emit(
            state,
            stage="opinion_analysis",
            message="完成情感、话题和平台差异分析",
            progress=0.5,
            payload={
                "agent": "opinion_analysis_agent",
                "skill": "opinion_analysis",
                "tool": "analyze_opinion",
            },
        )
        return {"opinion": result}

    async def _propagation(self, state: AnalysisState) -> dict[str, Any]:
        result = await self._tools.invoke(
            "reconstruct_propagation",
            {"posts": state["posts"]},
        )
        await self._emit(
            state,
            stage="propagation",
            message=(
                f"生成 {len(result['nodes'])} 个节点和 "
                f"{len(result['edges'])} 条候选传播边"
            ),
            progress=0.68,
            payload={
                "agent": "propagation_agent",
                "skill": "propagation_reconstruction",
                "tool": "reconstruct_propagation",
            },
        )
        return {"propagation": result}

    async def _verification(self, state: AnalysisState) -> dict[str, Any]:
        if not bool(state["options"].get("include_fact_check", True)):
            result: dict[str, Any] = {
                "cards": [],
                "notice": "本次任务未启用事实核查。",
                "is_demo": state["is_demo"],
            }
        else:
            result = await self._tools.invoke(
                "verify_claims",
                {"posts": state["posts"], "topic": state["topic"]},
            )
        await self._emit(
            state,
            stage="verification",
            message=f"完成 {len(result['cards'])} 条主张的证据核查",
            progress=0.84,
            payload={
                "agent": "verification_agent",
                "skill": "fact_check",
                "tool": "verify_claims",
            },
        )
        return {"fact_check": result}

    async def _report(self, state: AnalysisState) -> dict[str, Any]:
        result = await self._tools.invoke(
            "build_report",
            {
                "topic": state["topic"],
                "opinion": state["opinion"],
                "propagation": state["propagation"],
                "fact_check": state["fact_check"],
            },
        )
        await self._emit(
            state,
            stage="reporting",
            message="Report Agent 已生成结构化简报",
            progress=0.96,
            payload={
                "agent": "report_agent",
                "skill": "report_generation",
                "tool": "build_report",
            },
        )
        return {"report": result}
