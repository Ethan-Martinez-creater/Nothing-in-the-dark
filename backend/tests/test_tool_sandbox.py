"""M15 工具沙箱、网络出口与密钥治理测试。"""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.core.errors import ApplicationError
from app.harness.sandbox import (
    ContainerExecutor,
    RestrictedProcessExecutor,
    SandboxedToolExecutor,
    SecretProvider,
    ToolManifest,
    ToolPolicyEngine,
    validate_egress_url,
)

# 受限沙箱（DSH/pwsh 受限模式）禁止 asyncio 命名管道（Windows）。
# 探测与 executor 相同的 PIPE 路径，失败时跳过依赖真实子进程的测试，
# 避免环境限制被误报为代码失败；真实环境全部运行。


async def _probe_piped_spawn() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "print(1)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, _err = await proc.communicate()
        return True
    except Exception:  # pragma: no cover - 环境探测
        return False


try:
    _PIPED_SPAWN_OK = asyncio.run(_probe_piped_spawn())
except Exception:  # pragma: no cover - 环境探测
    _PIPED_SPAWN_OK = False

needs_piped_spawn = pytest.mark.skipif(
    not _PIPED_SPAWN_OK,
    reason="当前受限沙箱禁止 asyncio 命名管道（EPERM），真实环境可运行",
)


# ---- ToolManifest 校验 ---------------------------------------------------


def test_manifest_validate_ok() -> None:
    manifest = ToolManifest(execution_class="restricted_process", risk_level="high")
    assert manifest.validate() == []


def test_manifest_validate_rejects_unknown_values() -> None:
    manifest = ToolManifest(execution_class="mystery", risk_level="extreme")
    errors = manifest.validate()
    assert any("execution_class" in e for e in errors)
    assert any("risk_level" in e for e in errors)


def test_manifest_validate_rejects_unknown_network_mode() -> None:
    manifest = ToolManifest(network={"mode": "everything"})
    assert any("network mode" in e for e in manifest.validate())


# ---- 策略引擎 ------------------------------------------------------------


def _engine(mode: str = "enforce") -> ToolPolicyEngine:
    return ToolPolicyEngine(mode=mode, default_network_mode="none")


def test_policy_denies_unknown_execution_class() -> None:
    decision = _engine().check(
        tool_name="t",
        manifest=ToolManifest(execution_class="mystery"),
        arguments={},
    )
    assert decision.verdict == "deny"
    assert "unknown_execution_class" in decision.reason_codes


def test_policy_container_fail_closed_without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COIFESP_CONTAINER_RUNTIME", raising=False)
    decision = _engine().check(
        tool_name="t",
        manifest=ToolManifest(execution_class="container"),
        arguments={},
    )
    assert decision.verdict == "deny"
    assert "container_unavailable_fail_closed" in decision.reason_codes


def test_policy_denies_missing_secret_in_enforce() -> None:
    secrets = SecretProvider()
    decision = _engine().check(
        tool_name="t",
        manifest=ToolManifest(secrets=("NON_EXISTENT_SECRET_XYZ",)),
        arguments={},
        secrets=secrets,
    )
    assert decision.verdict == "deny"
    assert "secret_missing" in decision.reason_codes


def test_policy_audit_only_records_but_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COIFESP_CONTAINER_RUNTIME", raising=False)
    decision = _engine(mode="audit_only").check(
        tool_name="t",
        manifest=ToolManifest(execution_class="container"),
        arguments={},
    )
    # audit_only 不阻断 container（记录为准）。
    assert decision.verdict == "allow"


def test_policy_denies_egress_outside_allowlist() -> None:
    manifest = ToolManifest(
        network={"mode": "allowlist", "domains": ["api.example.com"]},
        execution_class="restricted_process",
    )
    decision = _engine().check(
        tool_name="t",
        manifest=manifest,
        arguments={"url": "https://evil.example.org/x"},
    )
    assert decision.verdict == "deny"
    assert "egress_denied" in decision.reason_codes


def test_policy_allows_egress_within_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket as _socket

    def fake_getaddrinfo(host, *_args, **_kwargs):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)
    manifest = ToolManifest(
        network={"mode": "allowlist", "domains": ["api.example.com"]},
        execution_class="restricted_process",
    )
    decision = _engine().check(
        tool_name="t",
        manifest=manifest,
        arguments={"url": "https://api.example.com/v1"},
    )
    assert decision.verdict == "allow"


def test_policy_requires_approval_for_high_risk() -> None:
    manifest = ToolManifest(
        risk_level="high", approval_policy="require", execution_class="restricted_process"
    )
    decision = _engine().check(tool_name="t", manifest=manifest, arguments={})
    assert decision.verdict == "require_approval"


# ---- 出口 URL 校验 --------------------------------------------------------


def test_egress_rejects_private_ip_url() -> None:
    reason = validate_egress_url("https://127.0.0.1/x")
    assert reason is not None
    assert "127.0.0.1" in reason


def test_egress_rejects_cloud_metadata() -> None:
    assert validate_egress_url("https://169.254.169.254/latest/meta-data") is not None
    assert validate_egress_url("https://metadata.google.internal/") is not None


def test_egress_rejects_credentials_and_bad_port() -> None:
    assert validate_egress_url("https://user:pass@example.com/") is not None
    assert validate_egress_url("https://example.com:8443/") is not None


def test_egress_accepts_public_https() -> None:
    assert validate_egress_url("https://example.com/path", resolve_dns=False) is None


def test_egress_host_match_subdomain() -> None:
    from app.harness.sandbox import _host_matches

    assert _host_matches("api.weibo.com", {"weibo.com"})
    assert _host_matches("www.bilibili.com", {"*.bilibili.com"})
    assert not _host_matches("evil.com", {"example.com"})


# ---- SecretProvider -------------------------------------------------------


def test_secret_provider_resolves_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COIFESP_TEST_SECRET", "s3cret-value")
    provider = SecretProvider()
    assert provider.resolve("COIFESP_TEST_SECRET") == "s3cret-value"
    assert provider.resolve("NON_EXISTENT_SECRET_XYZ") is None


def test_secret_provider_rejects_invalid_name() -> None:
    provider = SecretProvider()
    assert provider.resolve("bad name!") is None


def test_secret_provider_redacts_known_value() -> None:
    provider = SecretProvider()
    redacted = provider.redact("token is abc123456", known_values=["abc123456"])
    assert "abc123456" not in redacted
    assert "[secret:" in redacted


def test_sandbox_base_env_only_allows_explicit_nonsecret_config() -> None:
    executor = SandboxedToolExecutor(
        base_env={
            "COIFESP_DEMO_MODE": "1",
            "COIFESP_MEDIACRAWLER_USAGE_MODE": "research",
            "COIFESP_LLM_API_KEY": "must-not-leak",
            "PATH": "must-not-override-host-minimum",
        }
    )
    env = executor._build_env(ToolManifest(), None, None)
    assert env["COIFESP_DEMO_MODE"] == "1"
    assert env["COIFESP_MEDIACRAWLER_USAGE_MODE"] == "research"
    assert "COIFESP_LLM_API_KEY" not in env
    assert "PATH" not in env


# ---- RestrictedProcessExecutor -------------------------------------------


@needs_piped_spawn
async def test_restricted_executor_runs_command() -> None:
    import tempfile

    executor = RestrictedProcessExecutor()
    token = await executor.prepare("tc-1", ToolManifest(execution_class="restricted_process"))
    with tempfile.TemporaryDirectory() as workdir:
        code, stdout, stderr = await executor.run(
            command=[sys.executable, "-c", "print('hello-sandbox')"],
            cwd=__import__("pathlib").Path(workdir),
            env={},
            timeout_seconds=10,
        )
    assert code == 0
    assert "hello-sandbox" in stdout
    await executor.destroy(token)


@needs_piped_spawn
async def test_restricted_executor_timeout_kills() -> None:
    import tempfile

    executor = RestrictedProcessExecutor()
    token = await executor.prepare("tc-2", ToolManifest(execution_class="restricted_process"))
    with tempfile.TemporaryDirectory() as workdir:
        with pytest.raises(ApplicationError) as exc:
            await executor.run(
                command=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=__import__("pathlib").Path(workdir),
                env={},
                timeout_seconds=1,
            )
    assert exc.value.code == "tool_timeout"
    await executor.destroy(token)


@needs_piped_spawn
async def test_restricted_executor_cancel() -> None:
    import tempfile

    executor = RestrictedProcessExecutor()
    token = await executor.prepare("tc-3", ToolManifest(execution_class="restricted_process"))
    cancel_event = asyncio.Event()

    async def do_run() -> tuple[int, str, str]:
        return await executor.run(
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=__import__("pathlib").Path(tempfile.gettempdir()),
            env={},
            timeout_seconds=30,
            cancel_event=cancel_event,
        )

    task = asyncio.create_task(do_run())
    await asyncio.sleep(0.5)
    cancel_event.set()
    with pytest.raises(ApplicationError) as exc:
        await task
    assert exc.value.code == "tool_cancelled"
    await executor.destroy(token)


@needs_piped_spawn
async def test_restricted_executor_filters_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """业务前缀变量绝不继承（仅系统必需 + 显式注入）；宿主密钥不可达。"""
    import os
    import tempfile

    for k, v in {
        "COIFESP_SANDBOX_MARKER": "must-not-leak",
        "HOST_SECRET_LEAK": "should-not-leak",
        "LLM_API_KEY": "sk-leak",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", os.environ.get("PATH") or "C:\\Windows\\System32")
    monkeypatch.setenv("SYSTEMROOT", os.environ.get("SYSTEMROOT") or "C:\\Windows")
    monkeypatch.setenv("COMSPEC", os.environ.get("COMSPEC") or "cmd.exe")
    monkeypatch.setenv("TEMP", os.environ.get("TEMP") or tempfile.gettempdir())
    monkeypatch.setenv("TMP", os.environ.get("TMP") or tempfile.gettempdir())
    executor = RestrictedProcessExecutor()
    token = await executor.prepare("tc-4", ToolManifest(execution_class="restricted_process"))
    probe = (
        "import os; print(os.environ.get('COIFESP_SANDBOX_MARKER', 'absent')); "
        "print(os.environ.get('HOST_SECRET_LEAK', 'absent')); "
        "print(os.environ.get('LLM_API_KEY', 'absent')); "
        "print(os.environ.get('PATH', '') != '')"
    )
    code, stdout, _ = await executor.run(
        command=[sys.executable, "-c", probe],
        cwd=__import__("pathlib").Path(tempfile.gettempdir()),
        env={},
        timeout_seconds=10,
    )
    assert code == 0
    lines = stdout.strip().splitlines()
    assert lines[0] == "absent"
    assert lines[1] == "absent"
    assert lines[2] == "absent"
    assert lines[3] == "True"  # 系统必需变量保留
    # 显式注入（manifest.secrets）可见
    code2, stdout2, _ = await executor.run(
        command=[
            sys.executable,
            "-c",
            "import os; print(os.environ.get('COIFESP_SANDBOX_MARKER', 'absent'))",
        ],
        cwd=__import__("pathlib").Path(tempfile.gettempdir()),
        env={"COIFESP_SANDBOX_MARKER": "injected"},
        timeout_seconds=10,
    )
    assert code2 == 0 and stdout2.strip() == "injected"
    await executor.destroy(token)


# ---- ContainerExecutor ----------------------------------------------------


async def test_container_executor_docker_missing_fails_fast() -> None:
    """docker 运行时缺失时容器执行快速失败（127），不伪装成功；
    container_supported() 门控确保不可用时不会走到容器路径。"""
    from app.harness.sandbox import container_supported

    executor = ContainerExecutor(docker_cmd="coifesp-definitely-missing-docker")
    code, _out, err = await executor.run(
        command=["true"],
        cwd=__import__("pathlib").Path("."),
        env={},
        timeout_seconds=5,
    )
    # docker CLI 缺失返回 127；daemon 不可用返回非零——两种情况都快速失败
    # （绝不伪装成功）。
    assert code != 0
    assert err.strip() != ""
    # 本机未部署容器运行时（无 COIFESP_CONTAINER_RUNTIME / docker）时门控为 False。
    assert container_supported() is False


def test_network_none_rejects_url_arguments() -> None:
    engine = ToolPolicyEngine(mode="enforce", default_network_mode="none")
    decision = engine.check(
        tool_name="local_only",
        manifest=ToolManifest(
            execution_class="trusted_in_process",
            network={"mode": "none"},
        ),
        arguments={"callback_url": "https://example.com/hook"},
    )
    assert decision.verdict == "deny"
    assert "egress_denied" in decision.reason_codes