"""Round-7 remediation tests: sandbox env isolation / streaming limit /
container executor args / media failure semantics / run-cancel listening.

覆盖审核清单 P0-1（沙箱加固）与 P1-5（多模态失败语义/取消监听）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from app.core.errors import ApplicationError

# ---------- M15 沙箱：环境白名单（业务前缀不继承） -------------------------


async def _ensure_env() -> None:
    for key, value in {
        "PATH": os.environ.get("PATH") or "C:\\Windows\\System32",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT") or "C:\\Windows",
        "COMSPEC": os.environ.get("COMSPEC") or "cmd.exe",
        "TEMP": os.environ.get("TEMP") or ".",
        "TMP": os.environ.get("TMP") or ".",
        "USERPROFILE": os.environ.get("USERPROFILE") or ".",
        "HOME": os.environ.get("HOME") or ".",
        "PATHEXT": os.environ.get("PATHEXT") or ".EXE",
    }.items():
        os.environ.setdefault(key, value)


@pytest.mark.asyncio
async def test_restricted_env_blocks_business_prefixes() -> None:
    """子进程不得继承任何业务前缀变量（密钥），只保留系统必需变量。"""
    await _ensure_env()
    os.environ["COIFESP_SECRET_LEAK"] = "topsecret"
    os.environ["LLM_API_KEY"] = "sk-leak"
    os.environ["MEDIACRAWLER_COOKIE"] = "cookie-leak"
    os.environ["HTTPS_PROXY"] = "http://evil-proxy:3128"
    from app.harness.sandbox import RestrictedProcessExecutor, ToolManifest

    executor = RestrictedProcessExecutor()
    token = await executor.prepare("envt", ToolManifest(execution_class="restricted_process"))
    cwd = Path(token["workdir"])
    probe = (
        "import os, json; print(json.dumps({k: os.environ.get(k) for k in "
        "['COIFESP_SECRET_LEAK','LLM_API_KEY','MEDIACRAWLER_COOKIE','HTTPS_PROXY','PATH','TEMP']}))"
    )
    try:
        code, out, err = await executor.run(
            command=[sys.executable, "-c", probe],
            cwd=cwd,
            env={},
            timeout_seconds=30,
            max_output_bytes=1024 * 1024,
        )
    finally:
        await executor.destroy(token)
    assert code == 0, err
    got = json.loads(out)
    assert got["COIFESP_SECRET_LEAK"] is None
    assert got["LLM_API_KEY"] is None
    assert got["MEDIACRAWLER_COOKIE"] is None
    assert got["HTTPS_PROXY"] is None
    assert got["PATH"] is not None  # 系统必需变量保留
    assert got["TEMP"] is not None


@pytest.mark.asyncio
async def test_restricted_env_injects_explicit_env_only() -> None:
    """调用方显式传入的 env（manifest.secrets）才注入子进程。"""
    await _ensure_env()
    from app.harness.sandbox import RestrictedProcessExecutor, ToolManifest

    executor = RestrictedProcessExecutor()
    token = await executor.prepare("envt2", ToolManifest(execution_class="restricted_process"))
    cwd = Path(token["workdir"])
    probe = "import os; print(os.environ.get('INJECTED_SECRET'))"
    try:
        code, out, _err = await executor.run(
            command=[sys.executable, "-c", probe],
            cwd=cwd,
            env={"INJECTED_SECRET": "visible"},
            timeout_seconds=30,
        )
    finally:
        await executor.destroy(token)
    assert code == 0
    assert out.strip() == "visible"


@pytest.mark.asyncio
async def test_restricted_stream_limit_kills_process() -> None:
    """stdout 超出流式上限时立即终止进程并抛 tool_output_too_large。"""
    await _ensure_env()
    from app.harness.sandbox import RestrictedProcessExecutor, ToolManifest

    executor = RestrictedProcessExecutor()
    token = await executor.prepare("lim", ToolManifest(execution_class="restricted_process"))
    cwd = Path(token["workdir"])
    generator = "import sys; sys.stdout.write('x' * 20971520)"
    try:
        with pytest.raises(ApplicationError) as exc_info:
            await executor.run(
                command=[sys.executable, "-c", generator],
                cwd=cwd,
                env={},
                timeout_seconds=60,
                max_output_bytes=4096,
            )
    finally:
        await executor.destroy(token)
    assert exc_info.value.code == "tool_output_too_large"


@pytest.mark.asyncio
async def test_restricted_timeout_with_cancel_listener_kills_process() -> None:
    """传入 cancel_event 时正常超时也必须得到 tool_timeout，而非 InvalidStateError。"""
    await _ensure_env()
    from app.harness.sandbox import RestrictedProcessExecutor, ToolManifest

    executor = RestrictedProcessExecutor()
    token = await executor.prepare(
        "timeout-cancel", ToolManifest(execution_class="restricted_process")
    )
    try:
        with pytest.raises(ApplicationError) as exc_info:
            await executor.run(
                command=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=Path(token["workdir"]),
                env={},
                timeout_seconds=0.2,
                cancel_event=asyncio.Event(),
                max_output_bytes=4096,
                token=token,
            )
    finally:
        await executor.destroy(token)
    assert exc_info.value.code == "tool_timeout"


# ---------- M15 容器执行器：docker 参数构造 ---------------------------------


def test_container_executor_builds_docker_args() -> None:
    from app.harness.sandbox import ContainerExecutor

    executor = ContainerExecutor(
        proxy_url="http://egress-proxy:1234",
        network="coifesp-egress-only",
    )
    executor.set_code_root("E:/backend")
    token = {
        "name": "coifesp-sb-test-1234abcd",
        "workdir": Path("/tmp/w"),
        "network": "coifesp-egress-only",
    }
    args = executor._build_docker_args(
        token=token,
        command=[
            "python",
            "-m",
            "app.harness.sandbox_worker",
            "--payload-file",
            "/tmp/w/payload.json",
        ],
        env={"WEIBO_COOKIE": "cookieval"},
    )
    joined = " ".join(args)
    assert "--read-only" in args
    assert "--cap-drop" in args and "ALL" in args
    assert "--security-opt" in args and "no-new-privileges" in args
    assert "--user" in args and "65534:65534" in args
    assert "--network" in args and "coifesp-egress-only" in args
    assert "E:/backend:/app:ro" in args
    assert "PYTHONPATH=/app" in joined
    assert "HTTPS_PROXY=http://egress-proxy:1234" in joined
    # 秘密 env 显式注入
    assert "WEIBO_COOKIE=cookieval" in joined
    # 宿主 python 被替换为容器 python3
    assert "python3" in args
    assert "/tmp/w:/work:rw" in joined
    assert "/work/payload.json" in args


def test_container_supported_gates_on_runtime_and_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.harness.sandbox as sb

    monkeypatch.delenv("COIFESP_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setattr(sb.shutil, "which", lambda *a, **k: None)
    assert sb.container_supported() is False

    monkeypatch.setenv("COIFESP_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(sb.shutil, "which", lambda *a, **k: "/usr/bin/docker")
    assert sb.container_supported() is True

    monkeypatch.setenv("COIFESP_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(sb.shutil, "which", lambda *a, **k: None)
    assert sb.container_supported() is False


@pytest.mark.asyncio
async def test_container_network_requires_hardened_proxy() -> None:
    from app.harness.sandbox import ContainerExecutor, ToolManifest

    executor = ContainerExecutor(network="bridge", proxy_url="http://proxy:8080")
    with pytest.raises(ApplicationError) as exc:
        await executor.prepare(
            "net",
            ToolManifest(network={"mode": "allowlist", "domains": ["example.com"]}),
        )
    assert exc.value.code == "container_egress_network_unavailable"

    secure = ContainerExecutor(network="coifesp-egress-only", proxy_url="http://proxy:8080")
    token = await secure.prepare("net2", ToolManifest(network={"mode": "allowlist"}))
    try:
        assert token["network"] == "coifesp-egress-only"
    finally:
        await secure.destroy(token)


# ---------- M15 执行类选择：settings 装配（bootstrap 降级逻辑） --------------


def test_build_sandbox_executor_returns_container() -> None:

    from app.harness.sandbox import ContainerExecutor, build_sandbox_executor

    ex = build_sandbox_executor("container")
    assert isinstance(ex, ContainerExecutor)


def test_policy_denies_container_manifest_when_only_restricted_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.harness.sandbox as sb

    monkeypatch.setenv("COIFESP_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(sb.shutil, "which", lambda *a, **k: "/usr/bin/docker")
    engine = sb.ToolPolicyEngine(mode="enforce", available_execution_class="restricted_process")
    decision = engine.check(
        tool_name="danger",
        manifest=sb.ToolManifest(execution_class="container"),
        arguments={},
    )
    assert decision.verdict == "deny"
    assert "container_unavailable_fail_closed" in decision.reason_codes


# ---------- P1-5 多模态：失败语义（不得伪装成功） ---------------------------


@pytest.mark.asyncio
async def test_tesseract_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import media_providers

    async def fake_run(cmd, **kwargs):
        return 1, "", "tesseract: no such file or image"

    monkeypatch.setattr(media_providers, "_run_command", fake_run)
    provider = media_providers.TesseractOCRProvider()
    with pytest.raises(RuntimeError, match="tesseract OCR failed"):
        await provider.extract("/tmp/x.png", "image")


@pytest.mark.asyncio
async def test_whisper_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import media_providers

    async def fake_run(cmd, **kwargs):
        return 1, "", "whisper: model load failed"

    monkeypatch.setattr(media_providers, "_run_command", fake_run)
    provider = media_providers.WhisperASRProvider()
    provider._available = True
    with pytest.raises(RuntimeError, match="whisper ASR failed"):
        await provider.transcribe("/tmp/x.mp4", "video")


@pytest.mark.asyncio
async def test_whisper_missing_output_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import media_providers

    async def fake_run(cmd, **kwargs):
        return 0, "", ""

    monkeypatch.setattr(media_providers, "_run_command", fake_run)
    provider = media_providers.WhisperASRProvider()
    provider._available = True
    with pytest.raises(RuntimeError, match="output file missing"):
        await provider.transcribe("/tmp/x.mp4", "video")


# ---------- P1-5 多模态：运行中取消监听 ------------------------------------


@pytest.mark.asyncio
async def test_run_command_listens_cancel_during_run() -> None:
    from app.infrastructure.media_providers import _run_command

    cancel = asyncio.Event()
    task = asyncio.create_task(
        _run_command([sys.executable, "-c", "import time; time.sleep(60)"], cancel_event=cancel)
    )
    await asyncio.sleep(0.6)
    cancel.set()
    code, _out, err = await asyncio.wait_for(task, timeout=15)
    assert code == 130
    assert "cancelled" in err


# ---------- P1-5 多模态：DNS rebinding 加固（IP 固定 + SNI 绑定） ------------


@pytest.mark.asyncio
async def test_fetch_pins_ip_and_sni(monkeypatch: pytest.MonkeyPatch) -> None:
    """连接目标固定为解析 IP，域名经 sni_hostname 扩展保留；重定向重新解析。"""
    import socket

    import httpx

    from app.infrastructure.media_fetch import MediaFetchService

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "host": request.url.host,
                "sni": request.extensions.get("sni_hostname"),
                "header_host": request.headers.get("host"),
            }
        )
        return httpx.Response(200, content=b"ok")

    import tempfile

    service = MediaFetchService(
        Path(tempfile.mkdtemp(prefix="coifesp-r7-")),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await service.fetch("https://example.com/a.png", "image")
    assert result.ok is True
    assert seen and seen[0]["host"] == "8.8.8.8"
    assert seen[0]["sni"] == "example.com"
    assert seen[0]["header_host"] == "example.com"
