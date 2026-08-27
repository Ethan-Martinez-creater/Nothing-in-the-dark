"""Security: one-time authorization, replay, sandbox isolation, SSRF, secrets.

安全专项测试（审核项 1/2/3）：
- M21/M22 一次性授权消费：同一审批只能签发一次、消费一次；参数篡改/
  作用域不匹配/过期/并发重复消费全部拒绝（防重放）。
- M15 强制沙箱：restricted_process 工具未装配沙箱时 fail closed；子进程
  独立工作目录、最小环境 + 显式秘密注入、出口代理强制。
- SSRF：EgressProxy 拒绝 loopback/私网/云元数据，DNS 解析后的不安全地址
  也拒绝。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.application.authorization_service import AuthorizationService
from app.application.repositories import ApplicationRepository
from app.bootstrap import ApplicationContainer as ApplicationContainerRef
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.harness.egress_proxy import EgressProxy
from app.harness.sandbox import (
    SandboxedToolExecutor,
    SecretProvider,
    ToolManifest,
    validate_egress_url,
)
from app.harness.tools import ToolRegistry, ToolSpec
from app.infrastructure.database.engine import Database
from app.main import create_app
from app.schemas.cases import CreateCaseRequest

_DB_ROOT = "E:/Graduate_work_folder/Agent_develop/Project/COIFESP_Agent/Project/backend/data"


def _db_url(name: str) -> str:
    return "sqlite+aiosqlite:///" + _DB_ROOT.replace("\\", "/") + "/" + name


def _cleanup_db(name: str) -> None:
    path = os.path.join(_DB_ROOT, name)
    try:
        os.remove(path)
    except OSError:
        pass


# ---------- SSRF / Egress 出口校验（单元） ----------

def test_validate_egress_url_denies_ssrf_targets() -> None:
    denied = [
        "http://127.0.0.1:80/health",
        "https://localhost:443/admin",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/x",
        "http://100.100.100.200/x",
        "http://weibo.com:8080/x",  # 非白名单端口
        "ftp://weibo.com/x",  # 非 http/https
        "http://user:pass@weibo.com/x",  # URL 内嵌凭据
        "http://evil.com/x",  # 不在白名单
        "http://weibo.com.evil.com/x",  # 域名后缀伪装
    ]
    for url in denied:
        reason = validate_egress_url(url, allowed_hosts={"weibo.com", "*.weibo.com"})
        assert reason is not None, f"{url} should be denied"


def test_validate_egress_url_allows_allowlisted_hosts() -> None:
    allowed = [
        "https://weibo.com/search",
        "https://m.weibo.cn/detail/1",
        "https://img.bilibili.com/a.png",
    ]
    for url in allowed:
        reason = validate_egress_url(
            url,
            allowed_hosts={"weibo.com", "weibo.cn", "*.bilibili.com"},
            resolve_dns=False,
        )
        assert reason is None, f"{url} should be allowed: {reason}"


# ---------- EgressProxy（asyncio，无 DB） ----------

def test_egress_proxy_rejects_loopback_tunnel() -> None:
    async def run() -> None:
        proxy = EgressProxy(allowed_hosts={"weibo.com"})
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(512), timeout=5)
            assert b"403" in response, response
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            # 私网/元数据同样拒绝（DNS 解析后的不安全地址）。
            for target in (b"169.254.169.254:80", b"10.0.0.5:80"):
                reader2, writer2 = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer2.write(
                    b"CONNECT " + target + b" HTTP/1.1\r\nHost: " + target + b"\r\n\r\n"
                )
                await writer2.drain()
                response2 = await asyncio.wait_for(reader2.read(512), timeout=5)
                assert b"403" in response2, (target, response2)
                writer2.close()
                try:
                    await writer2.wait_closed()
                except Exception:
                    pass
        finally:
            await proxy.stop()

    asyncio.run(run())


# ---------- 强制沙箱（asyncio，无 DB） ----------

def test_restricted_tool_fail_closed_without_sandbox() -> None:
    """未装配沙箱执行器时，restricted_process 工具必须拒绝执行（fail closed）。"""

    from pydantic import BaseModel

    class In(BaseModel):
        x: int = 1

    async def handler(arguments) -> dict[str, object]:
        return {"ok": True}

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="restricted_echo",
                version="1.0.0",
                description="test",
                input_model=In,
                handler=handler,
                execution_class="restricted_process",
            )
        )
        try:
            await registry.invoke_with_meta(
                "restricted_echo",
                {"x": 1},
                granted_permissions=set(),
            )
            raise AssertionError("should have failed closed")
        except ApplicationError as exc:
            assert exc.code == "tool_sandbox_unavailable"

    asyncio.run(run())


def test_sandboxed_tool_executor_isolated_process() -> None:
    """子进程执行：独立工作目录、最小环境 + 显式秘密注入、代理环境注入。"""

    async def run() -> None:
        os.environ["COIFESP_TEST_SECRET"] = "s3cr3t-value"
        manifest = ToolManifest(
            execution_class="restricted_process",
            secrets=("COIFESP_TEST_SECRET",),
        )
        executor = SandboxedToolExecutor()
        result = await executor.execute(
            tool_name="echo",
            payload={"k": "v"},
            manifest=manifest,
            secrets=SecretProvider(),
            proxy_env={
                "HTTPS_PROXY": "http://127.0.0.1:9999",
                "HTTP_PROXY": "http://127.0.0.1:9999",
                "NO_PROXY": "",
            },
            timeout_seconds=30,
        )
        assert result["ok"] is True
        assert result["echo"]["k"] == "v"
        # 子进程运行在独立临时目录，而非宿主工作区。
        assert result["cwd"] != os.getcwd()
        # 显式秘密注入子进程环境（仅名称可见，不打印值）。
        assert "COIFESP_TEST_SECRET" in result["leaked_env_secrets"]
        # 出口代理环境强制注入。
        assert result["proxy"] == "http://127.0.0.1:9999"
        # 输出不得包含秘密明文。
        assert "s3cr3t-value" not in result.get("echo", {}).get("cwd", "")

    asyncio.run(run())


# ---------- 一次性授权消费（集成，单次建库） ----------

def test_authorization_one_time_consumption() -> None:
    _cleanup_db("security_auth.db")
    database = Database(_db_url("security_auth.db"))
    repo = ApplicationRepository(database)
    service = AuthorizationService(repo)

    async def _approval(run_id: str, *, action: str = "collect_social_posts") -> str:
        record = await repo.create_approval(
            run_id=run_id,
            action=action,
            reason="test",
            request_payload={},
        )
        await repo.update_approval_full(
            record.id,
            status="approved",
            decision="approve",
            actor="operator",
        )
        return record.id

    async def run() -> None:
        await database.create_schema()
        case = await repo.create_case(
            CreateCaseRequest(title="auth test", topic="tt", platforms=["weibo"])
        )
        run = await repo.create_agent_run(
            case_id=case.id, turn_id=None, objective="x"
        )
        params = {"platforms": ["weibo"], "limit": 10}
        # 1) 未批准的审批不能签发授权。
        pending = await repo.create_approval(
            run_id=run.id, action="collect_social_posts", reason="x", request_payload={}
        )
        try:
            await service.issue(
                pending.id, action_family="tool:collect_social_posts", resource_id=run.id
            )
            raise AssertionError("should reject unapproved approval")
        except ApplicationError as exc:
            assert exc.code == "authorization_approval_not_approved"

        # 2) 批准后签发成功；同一审批完全匹配时复用稳定记录 ID。
        #    并发/崩溃重试不会轮换 token，也不会让先返回结果失效。
        approval_id = await _approval(run.id)
        token = await service.issue(
            approval_id,
            action_family="tool:collect_social_posts",
            resource_id=run.id,
            parameters=params,
        )
        assert token
        token2 = await service.issue(
            approval_id,
            action_family="tool:collect_social_posts",
            resource_id=run.id,
            parameters=params,
        )
        assert token2 == token

        concurrent_approval = await _approval(run.id)
        issued = await asyncio.gather(
            *(
                service.issue(
                    concurrent_approval,
                    action_family="tool:collect_social_posts",
                    resource_id=run.id,
                    parameters=params,
                ) for _ in range(4)
            )
        )
        assert len(set(issued)) == 1

        # 2b) 二次签发但 scope 不匹配（不同资源/参数）→ 仍拒绝，防一票多用。
        try:
            await service.issue(
                approval_id,
                action_family="tool:collect_social_posts",
                resource_id="different-run-id",
                parameters=params,
            )
            raise AssertionError("should reject scope-mismatched second issue")
        except ApplicationError as exc:
            assert exc.code == "authorization_already_issued"

        # 3) 消费一次成功；再次消费被拒（防重放）。
        await service.consume(
            approval_id,
            action_family="tool:collect_social_posts",
            resource_id=run.id,
            parameters=params,
        )
        try:
            await service.consume(
                approval_id,
                action_family="tool:collect_social_posts",
                resource_id=run.id,
                parameters=params,
            )
            raise AssertionError("should reject second consume")
        except ApplicationError as exc:
            assert exc.code == "authorization_already_consumed"

        # 4) 参数篡改（同 approval 不同参数哈希）被拒。
        approval2 = await _approval(run.id)
        await service.issue(
            approval2,
            action_family="tool:collect_social_posts",
            resource_id=run.id,
            parameters={"platforms": ["weibo"]},
        )
        try:
            await service.consume(
                approval2,
                action_family="tool:collect_social_posts",
                resource_id=run.id,
                parameters={"platforms": ["douyin"]},
            )
            raise AssertionError("should reject tampered parameters")
        except ApplicationError as exc:
            assert exc.code == "authorization_parameter_mismatch"

        # 5) 作用域不匹配（把采集审批用于 Kill Switch）被拒。
        approval3 = await _approval(run.id)
        await service.issue(
            approval3,
            action_family="tool:collect_social_posts",
            resource_id=run.id,
            parameters=params,
        )
        try:
            await service.consume(
                approval3,
                action_family="kill_switch",
                resource_id="enable:tool:crawl",
                parameters={"scope": "tool", "target": "crawl"},
            )
            raise AssertionError("should reject scope mismatch")
        except ApplicationError as exc:
            assert exc.code == "authorization_scope_mismatch"

        # 6) 并发消费：两个并发 consume 只有一个成功。
        approval4 = await _approval(run.id)
        await service.issue(
            approval4,
            action_family="kill_switch",
            resource_id="enable:tool:crawl",
            parameters={"scope": "tool", "target": "crawl"},
        )

        async def try_consume() -> str:
            try:
                await service.consume(
                    approval4,
                    action_family="kill_switch",
                    resource_id="enable:tool:crawl",
                    parameters={"scope": "tool", "target": "crawl"},
                )
                return "ok"
            except ApplicationError as exc:
                return exc.code

        outcomes = await asyncio.gather(try_consume(), try_consume())
        assert outcomes.count("ok") == 1
        assert "authorization_already_consumed" in outcomes

        # 7) 过期审批不能签发。
        expired_run = await repo.create_agent_run(
            case_id=case.id, turn_id=None, objective="y"
        )
        expired = await repo.create_approval(
            run_id=expired_run.id,
            action="kill_switch",
            reason="x",
            request_payload={},
        )
        await repo.update_approval_full(
            expired.id,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await repo.update_approval_full(
            expired.id,
            status="approved",
            decision="approve",
            actor="operator",
        )
        try:
            await service.issue(
                expired.id, action_family="kill_switch", resource_id="enable:x:y"
            )
            raise AssertionError("should reject expired approval")
        except ApplicationError as exc:
            assert exc.code == "authorization_approval_expired"

    async def _main() -> None:
        await run()
        await database.dispose()

    asyncio.run(_main())
    _cleanup_db("security_auth.db")


# ---------- API 防重放（集成，单次建库） ----------

def test_api_kill_switch_and_dead_letter_replay_denied() -> None:
    _cleanup_db("security_api.db")
    app = create_app(
        Settings(
            database_url=_db_url("security_api.db"),
            demo_mode=True,
        )
    )
    with TestClient(app) as client:
        container = app.state.container

        async def seed_kill() -> str:
            return await _seed_approval(container, "kill_switch")

        approval_id = client.portal.call(seed_kill)
        # 第一次开启：成功
        first = client.post(
            "/api/v1/system/resilience/kill-switches",
            json={
                "scope": "tool",
                "target": "crawl",
                "reason": "incident",
                "approval_id": approval_id,
            },
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "on"
        # 同一审批第二次用于另一个 Kill Switch：409（防重放）。
        second = client.post(
            "/api/v1/system/resilience/kill-switches",
            json={
                "scope": "global",
                "target": "*",
                "reason": "incident",
                "approval_id": approval_id,
            },
        )
        assert second.status_code == 409, second.text

        # 死信重试防重放：同一审批不能重放两次。
        async def seed_dl() -> str:
            return await _seed_dead_letter(container)

        dead_letter_id = client.portal.call(seed_dl)
        async def seed_retry() -> str:
            return await _seed_approval(container, "dead_letter_retry")

        retry_approval = client.portal.call(seed_retry)
        replay = client.post(
            f"/api/v1/system/resilience/dead-letters/{dead_letter_id}:retry",
            json={"actor": "ops", "approval_id": retry_approval},
        )
        assert replay.status_code == 200, replay.text
        replay_again = client.post(
            f"/api/v1/system/resilience/dead-letters/{dead_letter_id}:retry",
            json={"actor": "ops", "approval_id": retry_approval},
        )
        assert replay_again.status_code == 409, replay_again.text


async def _seed_approval(container: ApplicationContainerRef, action: str) -> str:
    """建 case/run/approval 并直接置为 approved（绕过决策流程）。"""
    case = await container.repository.create_case(
        CreateCaseRequest(title="sec", topic="tt", platforms=["weibo"])
    )
    run = await container.repository.create_agent_run(
        case_id=case.id, turn_id=None, objective="ops"
    )
    approval = await container.repository.create_approval(
        run_id=run.id, action=action, reason="ops", request_payload={}
    )
    await container.repository.update_approval_full(
        approval.id, status="approved", decision="approve", actor="operator"
    )
    return approval.id


async def _seed_dead_letter(container: ApplicationContainerRef) -> str:
    item = await container.resilience_repository.enqueue_dead_letter(
        operation_key="op:replay:1",
        dependency="douyin",
        scope="platform",
        classification="unknown",
        error_code="boom",
        attempts=3,
        payload_hash="h",
        policy_version="1.0",
        code_version=container.settings.app_version,
        recovery_hint="manual",
    )
    return item.id

