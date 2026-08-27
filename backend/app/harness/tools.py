from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import ApplicationError
from app.harness.cancel import run_cancel_event

ToolHandler = Callable[[BaseModel], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    permissions: tuple[str, ...] = ()
    side_effect: str = "none"
    idempotent: bool = True
    timeout_seconds: int = 30
    max_retries: int = 0
    estimated_cost: float = 0
    requires_approval: bool = False
    enabled: bool = True
    execution_mode: str = "parallel"
    # M6: output contract validation (validated after a successful handler run)
    output_model: type[BaseModel] | None = None
    # M6: result cache TTL in seconds; only active for idempotent,
    # side-effect-free tools (0 = no caching)
    cache_ttl_seconds: int = 0
    # M6: per-tool concurrency cap (0 = unlimited)
    max_concurrency: int = 0
    # M8c: mark RAG retrieval tools so tool_execution_end events carry
    # structured hit counts and retrieval modes for the frontend panel
    rag_output: bool = False
    # M15: 工具沙箱能力清单（执行类 / 文件 / 网络 / 秘密 / 资源 / 风险）。
    execution_class: str = "trusted_in_process"
    filesystem: dict[str, object] = dataclasses_field(default_factory=dict)
    network: dict[str, object] = dataclasses_field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    resources: dict[str, object] = dataclasses_field(default_factory=dict)
    risk_level: str = "low"
    # M15 强制沙箱：restricted_process / container 工具声明外部处理器
    # （"module:function"），由 SandboxedToolExecutor 在独立子进程中执行；
    # 未装配沙箱执行器时 fail closed，绝不降级裸跑。
    external_handler: str | None = None


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Structured tool result: output plus execution metadata for auditing."""

    output: dict[str, Any]
    cached: bool = False
    duration_ms: int = 0
    retry_history: tuple[dict[str, object], ...] = ()


def _cancel_error(name: str) -> ApplicationError:
    return ApplicationError(
        f"Tool '{name}' was cancelled",
        code="tool_cancelled",
    )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        # name-normalized-arguments -> (monotonic expiry, output)
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # M15: 工具策略引擎与密钥提供（不可绕过；模型无法覆盖）。
        self._policy_engine: Any = None
        self._secrets: Any = None
        # M15 强制沙箱：外部工具子进程执行器与出口代理（可选装配，未装配
        # 时 restricted_process 工具拒绝执行）。
        self._sandbox_executor: Any = None
        self._egress_proxy: Any = None
        # M16: 内容安全护栏（工具输入前置 / 工具输出后置）。
        self._security: Any = None

    def set_policy(
        self,
        engine: Any,
        secrets: Any | None = None,
    ) -> None:
        """装配 M15 策略引擎；策略在模型决定之后、执行之前运行。"""
        self._policy_engine = engine
        self._secrets = secrets

    def set_security(self, service: Any) -> None:
        """装配 M16 内容安全服务；护栏在策略检查之后、执行之前运行。"""
        self._security = service

    def set_sandbox_executor(
        self,
        executor: Any,
        *,
        secrets: Any | None = None,
        egress_proxy: Any | None = None,
    ) -> None:
        """装配 M15 强制沙箱执行器与出口代理（不可绕过）。"""
        self._sandbox_executor = executor
        if secrets is not None:
            self._secrets = secrets
        self._egress_proxy = egress_proxy

    async def run_external_tool(
        self,
        name: str,
        payload: dict[str, object],
        *,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, object]:
        """把工具的外部副作用段（爬虫采集等）在强制沙箱子进程中执行。

        供 restricted_process 工具的 handler 内部调用；未装配沙箱执行器
        时 fail closed（绝不降级裸跑）。"""
        spec = self.get(name)
        if spec.execution_class not in {"restricted_process", "container"}:
            raise ApplicationError(
                f"tool {name} is not a sandboxed execution class",
                code="tool_sandbox_class_invalid",
            )
        if self._sandbox_executor is None:
            raise ApplicationError(
                f"tool {name} requires the sandbox executor (fail closed)",
                code="tool_sandbox_unavailable",
            )
        manifest = self.manifest_for(spec)
        proxy_env = {}
        if self._egress_proxy is not None:
            proxy_env = {
                "HTTPS_PROXY": self._egress_proxy.proxy_url,
                "HTTP_PROXY": self._egress_proxy.proxy_url,
                "ALL_PROXY": self._egress_proxy.proxy_url,
                "NO_PROXY": "",
            }
        timeout = float(spec.resources.get("timeout_seconds") or spec.timeout_seconds)
        return await self._sandbox_executor.execute(
            tool_name=name,
            payload=payload,
            manifest=manifest,
            secrets=self._secrets,
            proxy_env=proxy_env or None,
            timeout_seconds=timeout,
            cancel_event=cancel_event,
            run_id=run_id,
            tool_call_id=tool_call_id,
        )

    def manifest_for(self, spec: ToolSpec) -> Any:
        """把 ToolSpec 的 M15 扩展字段转成 ToolManifest（供策略决策）。"""
        from app.harness.sandbox import ToolManifest

        return ToolManifest(
            execution_class=spec.execution_class,
            filesystem=dict(spec.filesystem),
            network=dict(spec.network),
            secrets=tuple(spec.secrets),
            resources=dict(spec.resources),
            risk_level=spec.risk_level,
            approval_policy=spec.approval_policy
            if hasattr(spec, "approval_policy")
            else ("require" if spec.requires_approval else "none"),
            side_effects=spec.side_effect,
        )

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ApplicationError(
                f"Tool '{spec.name}' is already registered",
                code="duplicate_tool",
            )
        self._tools[spec.name] = spec

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "name": spec.name,
                "version": spec.version,
                "description": spec.description,
                "permissions": list(spec.permissions),
                "side_effect": spec.side_effect,
                "requires_approval": spec.requires_approval,
                "enabled": spec.enabled,
                "execution_mode": spec.execution_mode,
                "output_schema": (
                    spec.output_model.model_json_schema()
                    if spec.output_model is not None
                    else None
                ),
                "cache_ttl_seconds": spec.cache_ttl_seconds,
                "max_concurrency": spec.max_concurrency,
            }
            for spec in self._tools.values()
        ]

    def llm_tools(self, allowed_tools: set[str] | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for spec in self._tools.values():
            if not spec.enabled:
                continue
            if allowed_tools is not None and spec.name not in allowed_tools:
                continue
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.input_model.model_json_schema(),
                    },
                }
            )
        return result

    def names(self) -> set[str]:
        """All registered tool names (for skill manifest dependency checks)."""
        return set(self._tools)

    def allowed_egress_hosts(self) -> set[str]:
        """汇总全部工具声明的网络出口白名单域名（供 EgressProxy 装配）。"""
        hosts: set[str] = set()
        for spec in self._tools.values():
            net = dict(spec.network or {})
            for host in net.get("domains") or []:
                if isinstance(host, str) and host:
                    hosts.add(host)
        return hosts

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ApplicationError(f"Unknown tool '{name}'", code="tool_not_found")
        if not spec.enabled:
            raise ApplicationError(f"Tool '{name}' is disabled", code="tool_disabled")
        return spec

    async def invoke(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        granted_permissions: set[str] | None = None,
        approved: bool = False,
        on_retry: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, Any]:
        """Run a tool and return its output dict (legacy callers)."""
        invocation = await self.invoke_with_meta(
            name,
            arguments,
            granted_permissions=granted_permissions,
            approved=approved,
            on_retry=on_retry,
        )
        return invocation.output

    async def invoke_with_meta(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        granted_permissions: set[str] | None = None,
        approved: bool = False,
        on_retry: Callable[[dict[str, object]], None] | None = None,
        cancel_event: asyncio.Event | None = None,
        security_context: dict[str, str] | None = None,
    ) -> ToolInvocation:
        spec = self.get(name)
        # A missing permission set denotes a trusted internal/legacy caller.
        # Model-driven callers always pass an explicit set and are checked.
        missing = (
            set(spec.permissions) - granted_permissions
            if granted_permissions is not None
            else set()
        )
        if missing:
            raise ApplicationError(
                f"Tool '{name}' requires permissions: {', '.join(sorted(missing))}",
                code="tool_permission_denied",
            )
        if (
            granted_permissions is not None
            and spec.requires_approval
            and not approved
        ):
            raise ApplicationError(
                f"Tool '{name}' requires user approval",
                code="tool_approval_required",
            )
        validated = spec.input_model.model_validate(arguments)

        # M15: 策略引擎不可绕过——deny 直接拒绝；require_approval 且未被
        # 预批准时按审批需求处理（tool_factory 已将高风险工具标记
        # requires_approval，此处兜底其余情形）。
        if self._policy_engine is not None:
            decision = self._policy_engine.check(
                tool_name=name,
                manifest=self.manifest_for(spec),
                arguments=arguments,
                secrets=self._secrets,
            )
            if decision.verdict == "deny":
                raise ApplicationError(
                    decision.message or f"Tool '{name}' denied by sandbox policy",
                    code="tool_policy_denied",
                )
            if decision.verdict == "require_approval":
                if granted_permissions is not None and not approved:
                    raise ApplicationError(
                        decision.message or f"Tool '{name}' requires approval",
                        code="tool_policy_requires_approval",
                    )

        # M16: 工具输入 Guardrail——模型产出的参数不得携带注入信号。
        # 检测器只是信号层，deny 由确定性策略决定（fail closed）。
        if self._security is not None:
            decision = await self._security.check_tool_input(
                name,
                arguments,
                run_id=(
                    (security_context or {}).get("run_id")
                ),
                turn_id=(
                    (security_context or {}).get("turn_id")
                ),
                tool_call_id=(
                    (security_context or {}).get("tool_call_id")
                ),
            )
            if decision["decision"] == "deny":
                raise ApplicationError(
                    decision["reason"] or f"Tool '{name}' input blocked",
                    code="tool_input_blocked",
                )
            if decision["decision"] == "require_approval":
                if granted_permissions is not None and not approved:
                    raise ApplicationError(
                        decision["reason"] or f"Tool '{name}' requires approval",
                        code="tool_approval_required",
                    )

        cacheable = (
            spec.cache_ttl_seconds > 0
            and spec.idempotent
            and spec.side_effect == "none"
        )
        if cacheable:
            cached_output = self._cache_get(name, arguments)
            if cached_output is not None:
                return ToolInvocation(output=cached_output, cached=True)

        if spec.max_concurrency > 0:
            async with self._semaphore(name, spec.max_concurrency):
                return await self._invoke(
                    spec,
                    validated,
                    arguments,
                    cacheable=cacheable,
                    on_retry=on_retry,
                    cancel_event=cancel_event,
                    security_context=security_context,
                )
        return await self._invoke(
            spec,
            validated,
            arguments,
            cacheable=cacheable,
            on_retry=on_retry,
            cancel_event=cancel_event,
            security_context=security_context,
        )

    async def _invoke(
        self,
        spec: ToolSpec,
        validated: BaseModel,
        arguments: dict[str, object],
        *,
        cacheable: bool,
        on_retry: Callable[[dict[str, object]], None] | None,
        cancel_event: asyncio.Event | None,
        security_context: dict[str, str] | None = None,
    ) -> ToolInvocation:
        started_at = time.perf_counter()
        retry_history: list[dict[str, object]] = []
        last_error: Exception | None = None
        output: dict[str, Any] | None = None
        for attempt in range(spec.max_retries + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise _cancel_error(spec.name)
            try:
                output = await self._run_handler(spec, validated, cancel_event)
                break
            except TimeoutError as exc:
                last_error = exc
            except Exception as exc:
                # Cancellation must never be retried.
                if isinstance(exc, ApplicationError) and exc.code == "tool_cancelled":
                    raise
                last_error = exc
                if attempt >= spec.max_retries or not spec.idempotent:
                    raise
            if attempt < spec.max_retries:
                delay = min(2**attempt, 4)
                await asyncio.sleep(delay)
                if cancel_event is not None and cancel_event.is_set():
                    raise _cancel_error(spec.name)
                if on_retry is not None:
                    entry: dict[str, object] = {
                        "attempt": attempt + 1,
                        "error_code": (
                            last_error.code
                            if isinstance(last_error, ApplicationError)
                            else "tool_timeout"
                        ),
                        "error_message": str(last_error)[:500],
                        "delay_seconds": delay,
                    }
                    retry_history.append(entry)
                    on_retry(entry)
        else:
            raise ApplicationError(
                f"Tool '{spec.name}' timed out",
                code="tool_timeout",
            ) from last_error

        if output is None:  # pragma: no cover - guarded by the loop above
            raise ApplicationError(
                f"Tool '{spec.name}' produced no output",
                code="tool_output_invalid",
            )
        if spec.output_model is not None:
            try:
                output = spec.output_model.model_validate(output).model_dump(
                    mode="json"
                )
            except ValidationError as exc:
                raise ApplicationError(
                    f"Tool '{spec.name}' returned an invalid output",
                    code="tool_output_invalid",
                ) from exc

        # M16: 工具输出 Guardrail——扫描敏感值/注入信号并保守脱敏；
        # 常规输出保持不变，只有命中秘密模式或高风险信号才替换。
        if self._security is not None:
            decision, sanitized = await self._security.check_tool_output(
                spec.name,
                output,
                run_id=(security_context or {}).get("run_id"),
                turn_id=(security_context or {}).get("turn_id"),
                tool_call_id=(security_context or {}).get("tool_call_id"),
            )
            if decision["decision"] == "deny":
                raise ApplicationError(
                    decision["reason"] or f"Tool '{spec.name}' output blocked",
                    code="tool_output_blocked",
                )
            if decision["decision"] in {"truncate", "isolate"}:
                output = sanitized
        if cacheable:
            self._cache_put(spec, arguments, output)
        return ToolInvocation(
            output=output,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            retry_history=tuple(retry_history),
        )

    async def _run_handler(
        self,
        spec: ToolSpec,
        validated: BaseModel,
        cancel_event: asyncio.Event | None,
    ) -> dict[str, Any]:
        """Run the handler with a timeout and cooperative cancellation.

        The handler runs as a task so that a run-scoped cancel_event reaches
        any await point inside it (asyncio task cancellation). A cancellation
        that fires before the handler finishes is never retried.

        M15 强制沙箱：restricted_process / container 工具必须装配沙箱执行
        器，否则 fail closed（绝不降级裸跑）；声明 external_handler 的工具
        直接在独立子进程中执行，不调用父进程闭包处理器。"""
        if spec.execution_class in {"restricted_process", "container"}:
            if self._sandbox_executor is None:
                raise ApplicationError(
                    f"tool {spec.name} requires the sandbox executor (fail closed)",
                    code="tool_sandbox_unavailable",
                )
            if spec.external_handler:
                payload = (
                    validated.model_dump(mode="json")
                    if hasattr(validated, "model_dump")
                    else dict(validated)
                )
                result = await self.run_external_tool(
                    spec.name,
                    payload,
                    cancel_event=cancel_event,
                )
                if isinstance(result, dict) and result.get("ok") is True:
                    return dict(result)
                raise ApplicationError(
                    "sandbox tool produced no output",
                    code="tool_output_invalid",
                )
        token = run_cancel_event.set(cancel_event)
        task = asyncio.create_task(spec.handler(validated))
        watchers: set[asyncio.Task[Any]] = {task}
        cancel_listener: asyncio.Task[bool] | None = None
        if cancel_event is not None:
            cancel_listener = asyncio.create_task(cancel_event.wait())
            watchers.add(cancel_listener)
        try:
            done, _ = await asyncio.wait(
                watchers,
                timeout=spec.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            task.cancel()
            if cancel_listener is not None:
                cancel_listener.cancel()
            try:
                await task
            except BaseException:
                pass
            raise
        finally:
            run_cancel_event.reset(token)
        if cancel_listener is not None:
            if cancel_listener in done:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise _cancel_error(spec.name)
            cancel_listener.cancel()
        if task not in done:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            raise TimeoutError(f"Tool '{spec.name}' timed out")
        return task.result()

    # ------------------------------------------------------------------
    # Result cache (in-memory, TTL-based; only for read-only tools)
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(name: str, arguments: dict[str, object]) -> str:
        return f"{name}:{json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)}"

    def _cache_get(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, Any] | None:
        key = self._cache_key(name, arguments)
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, output = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(key, None)
            return None
        return copy.deepcopy(output)

    def _cache_put(
        self,
        spec: ToolSpec,
        arguments: dict[str, object],
        output: dict[str, Any],
    ) -> None:
        key = self._cache_key(spec.name, arguments)
        self._cache[key] = (time.monotonic() + spec.cache_ttl_seconds, output)

    # ------------------------------------------------------------------
    # Per-tool concurrency caps
    # ------------------------------------------------------------------

    def _semaphore(self, name: str, limit: int) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(name)
        if semaphore is None:
            semaphore = asyncio.Semaphore(limit)
            self._semaphores[name] = semaphore
        return semaphore


class MCPToolAdapter:
    """Reserved adapter boundary for read-only MCP tools in the next milestone."""

    async def invoke(self, server: str, tool_name: str, arguments: dict[str, object]) -> object:
        raise ApplicationError(
            f"MCP server '{server}' is not configured for tool '{tool_name}'",
            code="mcp_not_configured",
        )
