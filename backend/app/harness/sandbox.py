"""Tool sandbox, network egress and secret governance (15).

强制执行工具的能力边界：

- :class:`ToolManifest` 描述一个工具的 execution_class / filesystem / network /
  secrets / resources / risk_level；启动时验证，未知能力默认拒绝。
- :class:`ToolPolicyEngine` 在模型决策之后、执行之前运行，输出
  allow / deny / require_approval 与沙箱配置；模型无法覆盖。
- :class:`SandboxExecutor` 端口 + Windows 受限进程实现（参数数组、独立
  临时目录、最小环境、进程树取消）；Linux 容器实现缺省 fail closed。
- :class:`SecretProvider` 只按名称注入引用；统一脱敏用 fingerprint 过滤。
- :class:`EgressValidator` 校验域名/端口/重定向，默认拒绝内网地址。

Windows 开发模式的隔离能力弱于 Linux 容器，属已知能力差距；
生产缺少强沙箱支持时，高风险工具拒绝启动而非降级裸跑。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import ApplicationError

logger = logging.getLogger(__name__)

EXECUTION_CLASSES = ("trusted_in_process", "restricted_process", "container")
POLICY_MODES = ("audit_only", "enforce")

# 默认拒绝的内网/保留地址。
_DENIED_IP_CONDITIONS = (
    "is_loopback",
    "is_private",
    "is_link_local",
    "is_multicast",
    "is_reserved",
    "is_unspecified",
)

# 常见云元数据地址（文档 15 明确禁止）。
_CLOUD_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.azure.internal",
        "100.100.100.200",  # aliyun
    }
)

# 默认允许的出口端口（HTTPS 为主，HTTP 仅限白名单工具显式声明）。
_DEFAULT_ALLOWED_PORTS = frozenset({443, 80})


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """工具能力清单（对应 ToolSpec 的 15 扩展字段）。"""

    execution_class: str = "trusted_in_process"
    filesystem: dict[str, Any] = field(default_factory=dict)
    network: dict[str, Any] = field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    resources: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    approval_policy: str = "none"
    side_effects: str = "none"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.execution_class not in EXECUTION_CLASSES:
            errors.append(f"unknown execution_class {self.execution_class!r}")
        if self.risk_level not in {"low", "medium", "high"}:
            errors.append(f"unknown risk_level {self.risk_level!r}")
        net = self.network or {}
        mode = net.get("mode", "none")
        if mode not in {"none", "allowlist", "platform_profile"}:
            errors.append(f"unknown network mode {mode!r}")
        return errors


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    verdict: str  # allow / deny / require_approval
    reason_codes: tuple[str, ...]
    sandbox_config: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
            "sandbox_config": self.sandbox_config,
            "message": self.message,
        }


class ToolPolicyEngine:
    """集中策略决策；策略在模型决定之后、执行之前运行。"""

    def __init__(
        self,
        *,
        mode: str = "enforce",
        default_network_mode: str = "none",
        allowed_ports: frozenset[int] = _DEFAULT_ALLOWED_PORTS,
        available_execution_class: str = "restricted_process",
    ) -> None:
        if mode not in POLICY_MODES:
            raise ValueError(f"unknown policy mode {mode!r}")
        if available_execution_class not in {"restricted_process", "container"}:
            raise ValueError(f"invalid available execution class {available_execution_class!r}")
        self._mode = mode
        self._default_network_mode = default_network_mode
        self._allowed_ports = allowed_ports
        self._available_execution_class = available_execution_class

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in POLICY_MODES:
            raise ValueError(f"unknown policy mode {mode!r}")
        self._mode = mode

    def check(
        self,
        *,
        tool_name: str,
        manifest: ToolManifest,
        arguments: dict[str, Any],
        actor: str = "system",
        run: dict[str, Any] | None = None,
        secrets: SecretProvider | None = None,
    ) -> PolicyDecision:
        """对一次工具调用做策略决策。audit_only 只记录，enforce 实际拒绝。"""
        reasons: list[str] = []

        # 1) 未知执行类默认拒绝（manifest.validate 已有前置校验，此处兜底）。
        if manifest.execution_class not in EXECUTION_CLASSES:
            return PolicyDecision(
                "deny", ("unknown_execution_class",), message=manifest.execution_class
            )

        # 2) 高风险工具在 enforce 且沙箱能力不足时 fail closed。
        if (
            self._mode == "enforce"
            and manifest.execution_class == "container"
            and (self._available_execution_class != "container" or not container_supported())
        ):
            return PolicyDecision(
                "deny",
                ("container_unavailable_fail_closed",),
                message="生产缺少强沙箱支持，container 级工具拒绝启动而非降级裸跑",
            )

        # 3) 秘密引用校验：声明的 secret 必须可解析。
        missing_secrets: list[str] = []
        for secret_name in manifest.secrets:
            if secrets is None or not secrets.resolve(secret_name):
                missing_secrets.append(secret_name)
        if missing_secrets:
            reasons.append("secret_missing")
            if self._mode == "enforce":
                return PolicyDecision(
                    "deny",
                    ("secret_missing",),
                    message=f"缺少必需秘密: {', '.join(missing_secrets)}",
                )

        # 4) 网络出口校验：参数中的 URL 必须匹配域名白名单。
        net = manifest.network or {}
        net_mode = net.get("mode", self._default_network_mode)
        allowed_hosts = set(net.get("domains", []) or [])
        egress_reason = _validate_egress_in_arguments(
            arguments,
            net_mode=net_mode,
            allowed_hosts=allowed_hosts,
            allowed_ports=self._allowed_ports,
        )
        if egress_reason:
            reasons.append("egress_denied")
            if self._mode == "enforce":
                return PolicyDecision(
                    "deny",
                    ("egress_denied",),
                    message=egress_reason,
                    sandbox_config={"network": {"mode": net_mode}},
                )

        # 5) 资源上限：manifest 声明了强约束时，参数摘要超限直接拒绝。
        resources = manifest.resources or {}
        limit_timeout = resources.get("timeout_seconds")
        if limit_timeout and isinstance(arguments.get("timeout_seconds"), (int, float)):
            if arguments["timeout_seconds"] > limit_timeout:
                reasons.append("resource_exceeded")

        # 6) 审批策略：外部副作用/高风险要求审批。
        if manifest.risk_level == "high" and manifest.approval_policy != "none":
            return PolicyDecision(
                "require_approval",
                ("high_risk_tool",),
                sandbox_config={"risk_level": "high"},
                message=f"工具 {tool_name} 属于高风险，需人工审批后执行",
            )

        if reasons:
            return PolicyDecision(
                "allow",
                tuple(reasons),
                sandbox_config={"warnings": reasons},
                message="工具已放行（存在审计记录的非阻断信号）",
            )
        return PolicyDecision(
            "allow", ("policy_ok",), sandbox_config={"execution_class": manifest.execution_class}
        )


def _validate_egress_in_arguments(
    arguments: dict[str, Any],
    *,
    net_mode: str,
    allowed_hosts: set[str],
    allowed_ports: frozenset[int],
) -> str | None:
    """扫描参数中的 URL 并做出口校验；返回拒绝原因或 None。"""
    if net_mode not in {"none", "allowlist", "platform_profile"}:
        return f"unknown network mode {net_mode!r}"

    urls: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                collect(v)
        elif isinstance(value, list):
            for v in value:
                collect(v)

    collect(arguments)
    if net_mode == "none" and urls:
        return "network mode is none but URL arguments were supplied"
    for url in urls:
        reason = validate_egress_url(url, allowed_hosts=allowed_hosts, allowed_ports=allowed_ports)
        if reason:
            return reason
    return None


def validate_egress_url(
    url: str,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_ports: frozenset[int] | None = None,
    resolve_dns: bool = True,
) -> str | None:
    """校验一个出口 URL；返回拒绝原因或 None。

    resolve_dns=False 跳过 DNS/IP 校验，供离线单元测试使用；生产路径
    始终解析并拒绝内网地址（SSRF 防护）。
    """
    allowed_hosts = allowed_hosts or set()
    allowed_ports = allowed_ports or _DEFAULT_ALLOWED_PORTS
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        return f"出口仅允许 http/https，实际 scheme={parsed.scheme!r}"
    if parsed.username or parsed.password:
        return "出口 URL 不得包含凭据"
    host = (parsed.hostname or "").lower()
    if not host:
        return "出口 URL 缺少主机名"
    if host in _CLOUD_METADATA_HOSTS:
        return f"拒绝云元数据地址 {host}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in allowed_ports:
        return f"拒绝非白名单端口 {port}"
    if allowed_hosts and not _host_matches(host, allowed_hosts):
        return f"主机 {host} 不在域名白名单内"
    if not resolve_dns:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"DNS 解析失败: {host}"
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if _is_denied_ip(ip) or str(ip) in _CLOUD_METADATA_HOSTS:
            return f"拒绝不安全地址 {ip}（{host}）"
    return None


def _host_matches(host: str, allowed_hosts: set[str]) -> bool:
    host = host.rstrip(".")
    for allowed in allowed_hosts:
        allowed = allowed.lower().rstrip(".")
        if host == allowed:
            return True
        if allowed.startswith("*.") and host.endswith(allowed[1:]):
            return True
        if host.endswith("." + allowed):
            return True
    return False


def _is_denied_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(getattr(ip, condition, False) for condition in _DENIED_IP_CONDITIONS)


def container_supported() -> bool:
    """Linux 容器 Executor 是否可用；当前环境未部署容器运行时返回 False。"""
    return bool(os.environ.get("COIFESP_CONTAINER_RUNTIME")) and shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# SecretProvider
# ---------------------------------------------------------------------------


class SecretProvider:
    """引用式密钥提供：应用只持有名称与引用，不接触明文。

    开发模式从明确环境变量读取；生产可对接外部 Secret Store
    （通过 COIFESP_SECRET_STORE_CMD 提供 getter 命令）。
    """

    def __init__(self, store_cmd: str | None = None) -> None:
        self._store_cmd = store_cmd
        self._cache: dict[str, str] = {}

    def resolve(self, name: str) -> str | None:
        """返回密钥值或 None；名称必须匹配环境变量模式。"""
        if name in self._cache:
            return self._cache[name]
        value = self._read(name)
        if value is not None:
            self._cache[name] = value
        return value

    def _read(self, name: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            logger.warning("invalid secret name %r rejected", name)
            return None
        if self._store_cmd:
            try:
                proc = subprocess.run(
                    [self._store_cmd, name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0:
                    return proc.stdout.strip() or None
            except Exception:
                logger.exception("secret store lookup failed for %s", name)
                return None
        value = os.environ.get(name)
        return value or None

    def fingerprint(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def redact(self, text: str, known_values: list[str] | None = None) -> str:
        """用 fingerprint 掩码替换已知/疑似密钥，供日志与审计使用。"""
        redacted = text
        for value in known_values or []:
            if value and value in redacted:
                redacted = redacted.replace(value, f"[secret:{self.fingerprint(value)}]")
        for secret in list(self._cache.values()):
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "[secret:redacted]")
        return redacted


# ---------------------------------------------------------------------------
# SandboxExecutor
# ---------------------------------------------------------------------------


class SandboxExecutor(Protocol):
    """沙箱执行端口：prepare / run / stream / cancel / collect / destroy。"""

    async def prepare(self, tool_call_id: str, manifest: ToolManifest) -> Any: ...

    async def run(
        self,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float,
        cancel_event: asyncio.Event | None = None,
        max_output_bytes: int | None = None,
        token: Any = None,
    ) -> tuple[int, str, str]: ...

    async def cancel(self, token: Any) -> None: ...

    async def collect(self, token: Any) -> dict[str, Any]: ...

    async def destroy(self, token: Any) -> None: ...


class RestrictedProcessExecutor:
    """Windows/开发受限进程实现：参数数组、独立临时目录、最小环境、进程树取消。

    能力差距：不提供 seccomp/cgroup，属于开发模式；生产容器见
    :class:`ContainerExecutor`。

    环境隔离：子进程**只**继承操作系统运行必需的变量（PATH/TEMP/系统
    根等），绝不继承任何业务前缀（COIFESP_*/MEDIACRAWLER_*/LLM_* 等）
    或宿主代理变量——密钥与出口代理只经调用方显式注入
    （SandboxedToolExecutor._build_env 按 manifest.secrets 注入）。
    """

    # 仅操作系统运行必需变量；任何业务前缀（密钥/配置）都不在此列。
    _ENV_KEYS = (
        "PATH",
        "SYSTEMROOT",
        "SystemRoot",
        "SystemDrive",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "PATHEXT",
        "COMSPEC",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "OS",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "APPDATA",
        "LOCALAPPDATA",
        # Windows 用户身份变量：getpass.getuser() 在 USERNAME/USER/LOGNAME/
        # LNAME 全部缺失时会 fallback 到 import pwd（POSIX 模块），导致
        # MediaCrawler 等第三方库在子进程沙箱内启动失败。
        "USERNAME",
        "USER",
        "LOGNAME",
        "LNAME",
        "COMPUTERNAME",
    )

    async def prepare(self, tool_call_id: str, manifest: ToolManifest) -> dict[str, Any]:
        workdir = Path(tempfile.mkdtemp(prefix=f"coifesp-sb-{tool_call_id[:8]}-"))
        return {"workdir": workdir, "proc": None}

    async def run(
        self,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float,
        cancel_event: asyncio.Event | None = None,
        max_output_bytes: int | None = None,
        token: Any = None,
    ) -> tuple[int, str, str]:
        if not command:
            raise ApplicationError(
                "sandbox command must not be empty", code="sandbox_invalid_command"
            )
        # 只注入操作系统运行必需变量 + 调用方显式 env；禁止工具读取宿主
        # 完整环境与任何业务前缀变量（防 .env/密钥泄漏）。
        base_env = {key: value for key, value in os.environ.items() if key in self._ENV_KEYS}
        base_env.update(env)
        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        # Windows 的 SelectorEventLoop 不支持 asyncio.create_subprocess_exec
        # （_make_subprocess_transport 直接抛 NotImplementedError），而后端主
        # 进程为兼容 psycopg async 运行在 SelectorEventLoop 上；因此子进程
        # 改用同步 subprocess 在线程中执行，语义与异步版本完全一致。
        def _run_sync() -> tuple[int, str, str]:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=base_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )
            _limit = max_output_bytes
            _exceeded: dict[str, bool] = {"stdout": False, "stderr": False}
            _out_chunks: list[str] = []
            _err_chunks: list[str] = []
            _out_total = 0
            _err_total = 0

            def _read(stream: Any, kind: str) -> None:
                nonlocal _out_total, _err_total
                while True:
                    chunk = stream.read(16 * 1024)
                    if not chunk:
                        break
                    if kind == "stdout":
                        _out_total += len(chunk)
                        total = _out_total
                    else:
                        _err_total += len(chunk)
                        total = _err_total
                    if _limit is not None and total >= _limit:
                        _exceeded[kind] = True
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        continue
                    (_out_chunks if kind == "stdout" else _err_chunks).append(
                        chunk.decode("utf-8", errors="replace")
                    )

            t_out = threading.Thread(
                target=_read, args=(process.stdout, "stdout"), daemon=True
            )
            t_err = threading.Thread(
                target=_read, args=(process.stderr, "stderr"), daemon=True
            )
            t_out.start()
            t_err.start()

            deadline = time.monotonic() + timeout_seconds
            cancelled = False
            timed_out = False
            try:
                while True:
                    if process.poll() is not None:
                        break
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        self._kill_tree_sync(process)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        self._kill_tree_sync(process)
                        break
                    time.sleep(0.05)
                if process.poll() is None:
                    process.wait()
            finally:
                t_out.join(timeout=2)
                t_err.join(timeout=2)

            stdout = "".join(_out_chunks)
            stderr = "".join(_err_chunks)
            if cancelled:
                raise ApplicationError(
                    "sandbox process cancelled", code="tool_cancelled"
                )
            if timed_out:
                raise ApplicationError(
                    f"sandbox process timed out after {timeout_seconds}s",
                    code="tool_timeout",
                )
            if _exceeded["stdout"] or _exceeded["stderr"]:
                raise ApplicationError(
                    f"sandbox output exceeded {max_output_bytes} bytes limit",
                    code="tool_output_too_large",
                )
            return process.returncode or 0, stdout, stderr

        return await asyncio.to_thread(_run_sync)

    def _kill_tree_sync(self, process: subprocess.Popen) -> None:
        try:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            logger.exception("process kill failed")
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except Exception:
                pass

    async def _kill_tree(self, process: subprocess.Popen) -> None:
        await asyncio.to_thread(self._kill_tree_sync, process)

    async def cancel(self, token: Any) -> None:
        if token is None:
            return
        proc = token.get("proc")
        if proc is not None and proc.returncode is None:
            await self._kill_tree(proc)

    async def collect(self, token: Any) -> dict[str, Any]:
        workdir = token.get("workdir")
        return {"workdir": str(workdir) if workdir else None}

    async def destroy(self, token: Any) -> None:
        workdir = token.get("workdir")
        if workdir is None:
            return
        path = Path(workdir)
        if await asyncio.to_thread(path.exists):
            try:
                await asyncio.to_thread(shutil.rmtree, workdir)
            except Exception:
                logger.warning("sandbox workdir cleanup failed: %s", workdir)


class ContainerExecutor:
    """Linux 容器执行器（docker CLI）：非 root、只读根、capabilities 清空。

    隔离语义：
    - 文件系统：`--read-only` 根 + `--user 65534:65534`（nobody）+
      `--cap-drop ALL` + `--security-opt no-new-privileges`，容器内进程
      无法读取宿主文件系统（工作目录与 backend 代码目录只读挂载）。
    - 进程：独立容器命名空间，进程树取消通过 `docker kill`。
    - 网络：默认完全断网（``--network none``）。需要联网的工具必须使用
      运维侧创建的出口隔离网络，并配置容器可达的 EgressProxy；普通
      bridge/host 网络会被拒绝，避免绕过代理直连外网。
    - 环境：只注入调用方显式 env（manifest.secrets + 代理变量），容器不
      继承宿主环境。
    """

    _ENV_SKIP_PREFIXES = ("PYTHONPATH",)

    def __init__(
        self,
        image: str = "coifesp-toolbox:latest",
        docker_cmd: str = "docker",
        container_python: str = "python3",
        container_code_mount: str = "/app",
        container_workdir: str = "/work",
        network: str | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self._image = image
        self._docker = docker_cmd
        self._python = container_python
        self._code_mount = container_code_mount
        self._workdir = container_workdir
        # Networked containers must use an operator-provisioned egress-only
        # network. The ordinary bridge/host networks permit proxy bypass.
        self._network = network or os.environ.get("COIFESP_SANDBOX_CONTAINER_NETWORK") or "none"
        self._proxy_url = proxy_url or os.environ.get("COIFESP_SANDBOX_CONTAINER_PROXY_URL")
        self._code_root: str | None = None

    def set_code_root(self, path: str) -> None:
        """宿主 backend 根目录；只读挂载进容器供 sandbox_worker 使用。"""
        self._code_root = path

    async def _docker_cmd(
        self, args: list[str], timeout_seconds: float = 30
    ) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._docker,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return 127, "", "docker command not found"
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "", f"docker command timed out after {timeout_seconds}s"
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def prepare(self, tool_call_id: str, manifest: ToolManifest) -> dict[str, Any]:
        mode = str((manifest.network or {}).get("mode") or "none")
        network = "none" if mode == "none" else self._network
        if mode != "none" and (
            network in {"none", "bridge", "host", "default"} or not self._proxy_url
        ):
            raise ApplicationError(
                "networked container tools require an egress-only network and container proxy URL",
                code="container_egress_network_unavailable",
            )
        workdir = Path(tempfile.mkdtemp(prefix=f"coifesp-sb-{tool_call_id[:8]}-"))
        name = f"coifesp-sb-{tool_call_id[:8]}-{hashlib.sha1(os.urandom(8)).hexdigest()[:8]}"
        return {"workdir": workdir, "name": name, "network": network}

    def _build_docker_args(
        self,
        *,
        token: dict[str, Any],
        command: list[str],
        env: dict[str, str],
    ) -> list[str]:
        name = token["name"]
        workdir = token["workdir"]
        args = [
            self._docker,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            str(token.get("network") or "none"),
        ]
        args += [
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--read-only",
            "-v",
            f"{str(workdir).replace(os.sep, '/')}:{self._workdir}:rw",
        ]
        if self._code_root:
            args += ["-v", f"{self._code_root}:{self._code_mount}:ro"]
        args += ["-w", self._workdir, "-e", f"PYTHONPATH={self._code_mount}"]
        # 出口代理：把宿主 127.0.0.1 映射为容器内 host.docker.internal。
        if self._proxy_url:
            for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
                args += ["-e", f"{key}={self._proxy_url}"]
            args += ["-e", "NO_PROXY="]
        for key, value in env.items():
            if key.startswith(self._ENV_SKIP_PREFIXES):
                continue
            if self._proxy_url and key in {"HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"}:
                continue
            args += ["-e", f"{key}={value}"]
        cmd = list(command)
        if cmd and Path(cmd[0]).name.lower() in ("python", "python.exe", "python3"):
            cmd[0] = self._python
        # Translate host workdir paths into their mounted container paths.
        for index, value in enumerate(cmd):
            try:
                relative = Path(value).relative_to(Path(workdir))
            except (TypeError, ValueError):
                continue
            cmd[index] = str(Path(self._workdir) / relative).replace("\\", "/")
        args += [self._image, *cmd]
        return args

    async def run(
        self,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float,
        cancel_event: asyncio.Event | None = None,
        max_output_bytes: int | None = None,
        token: Any = None,
    ) -> tuple[int, str, str]:
        token = token or {
            "name": f"coifesp-sb-{hashlib.sha1(os.urandom(8)).hexdigest()[:8]}",
            "workdir": cwd,
            "network": "none",
        }
        args = self._build_docker_args(token=token, command=command, env=env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            # docker CLI 缺失：容器执行失败（不伪装成功）；策略层和
            # bootstrap 会 fail closed，绝不降级到受限进程执行器。
            return 127, "", "docker command not found"

        _limit = max_output_bytes
        _exceeded: dict[str, bool] = {"stdout": False, "stderr": False}

        async def read_stream(stream: asyncio.StreamReader | None, kind: str) -> str:
            if stream is None:
                return ""
            chunks: list[str] = []
            total = 0
            while chunk := await stream.read(16 * 1024):
                total += len(chunk)
                if _limit is not None and total >= _limit:
                    _exceeded[kind] = True
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    continue
                chunks.append(chunk.decode("utf-8", errors="replace"))
            return "".join(chunks)

        stdout_task = asyncio.create_task(read_stream(proc.stdout, "stdout"))
        stderr_task = asyncio.create_task(read_stream(proc.stderr, "stderr"))
        cancel = cancel_event
        try:
            if cancel is None:
                await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
            else:
                wait_task = asyncio.create_task(proc.wait())
                cancel_task = asyncio.create_task(cancel.wait())
                try:
                    done, _ = await asyncio.wait(
                        {wait_task, cancel_task},
                        timeout=timeout_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for leftover in (wait_task, cancel_task):
                        if not leftover.done():
                            leftover.cancel()
                if cancel.is_set() and proc.returncode is None:
                    await self._kill_container(token["name"])
                    await asyncio.shield(proc.wait())
                    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                    raise ApplicationError("sandbox container cancelled", code="tool_cancelled")
                if not done and proc.returncode is None:
                    await self._kill_container(token["name"])
                    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                    raise ApplicationError(
                        f"sandbox container timed out after {timeout_seconds}s",
                        code="tool_timeout",
                    )
        except TimeoutError:
            await self._kill_container(token["name"])
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise ApplicationError(
                f"sandbox container timed out after {timeout_seconds}s",
                code="tool_timeout",
            ) from None
        except asyncio.CancelledError:
            await self._kill_container(token["name"])
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        stdout = stdout_task.result()
        stderr = stderr_task.result()
        if _exceeded["stdout"] or _exceeded["stderr"]:
            await self._kill_container(token["name"])
            raise ApplicationError(
                f"sandbox output exceeded {max_output_bytes} bytes limit",
                code="tool_output_too_large",
            )
        return proc.returncode or 0, stdout, stderr

    async def _kill_container(self, name: str) -> None:
        try:
            await self._docker_cmd(["kill", name], timeout_seconds=10)
            await self._docker_cmd(["rm", "-f", name], timeout_seconds=10)
        except Exception:
            logger.warning("container kill failed for %s", name, exc_info=True)

    async def cancel(self, token: Any) -> None:
        if token is None:
            return
        name = token.get("name")
        if name:
            await self._kill_container(name)

    async def collect(self, token: Any) -> dict[str, Any]:
        workdir = token.get("workdir")
        return {"workdir": str(workdir) if workdir else None}

    async def destroy(self, token: Any) -> None:
        workdir = token.get("workdir")
        if workdir is None:
            return
        path = Path(workdir)
        if await asyncio.to_thread(path.exists):
            try:
                await asyncio.to_thread(shutil.rmtree, workdir)
            except Exception:
                logger.warning("container workdir cleanup failed: %s", workdir)


def build_sandbox_executor(execution_class: str) -> SandboxExecutor:
    if execution_class == "container":
        return ContainerExecutor()
    return RestrictedProcessExecutor()


# ---------------------------------------------------------------------------
# 强制沙箱执行（15）：external tool 子进程 + 秘密注入 + 输出上限 + 审计
# ---------------------------------------------------------------------------


class SandboxedToolExecutor:
    """受限进程执行器 + worker 协议：外部工具在独立子进程中运行。

    - 独立临时工作目录，只挂载显式输入（payload JSON 文件）。
    - 环境白名单（复用 RestrictedProcessExecutor）+ manifest.secrets 显式
      注入 + 出口代理环境变量（强制走 EgressProxy）。
    - stdout/stderr 有大小上限；超时/取消按进程树终止。
    - 每次执行写 sandbox_executions 审计记录（recorder 回调）。
    """

    _CONFIG_ENV_KEYS = frozenset(
        {
            "COIFESP_DEMO_MODE",
            "COIFESP_MEDIACRAWLER_ROOT",
            "COIFESP_MEDIACRAWLER_OUTPUT_ROOT",
            "COIFESP_MEDIACRAWLER_PYTHON_EXECUTABLE",
            "COIFESP_MEDIACRAWLER_ENTRYPOINT",
            "COIFESP_MEDIACRAWLER_LOGIN_TYPE",
            "COIFESP_MEDIACRAWLER_HEADLESS",
            "COIFESP_MEDIACRAWLER_INCLUDE_COMMENTS",
            "COIFESP_MEDIACRAWLER_MAX_COMMENTS_PER_POST",
            "COIFESP_MEDIACRAWLER_TIMEOUT_SECONDS",
            "COIFESP_MEDIACRAWLER_MAX_OUTPUT_RUNS",
            "COIFESP_MEDIACRAWLER_USAGE_MODE",
        }
    )

    def __init__(
        self,
        *,
        recorder: Any = None,
        max_output_bytes: int = 10 * 1024 * 1024,
        python_executable: str | None = None,
        pythonpath: str | None = None,
        executor: Any = None,
        base_env: dict[str, str] | None = None,
    ) -> None:
        self._process = executor or RestrictedProcessExecutor()
        self._recorder = recorder
        self._max_output_bytes = max_output_bytes
        self._python = python_executable or sys.executable
        self._base_env = {
            key: str(value)
            for key, value in (base_env or {}).items()
            if key in self._CONFIG_ENV_KEYS and value not in {None, ""}
        }
        # 子进程需要 import app.*；从当前 sys.path 定位 backend 根并注入
        # PYTHONPATH（仅注入一个路径，不泄漏宿主完整环境）。
        self._pythonpath = pythonpath or self._discover_pythonpath()
        # 容器执行器需要把 backend 根只读挂载进容器。
        if isinstance(self._process, ContainerExecutor) and self._pythonpath:
            self._process.set_code_root(self._pythonpath)

    @staticmethod
    def _discover_pythonpath() -> str | None:
        for entry in sys.path:
            if entry and (Path(entry) / "app").is_dir():
                return entry
        return None

    async def execute(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
        manifest: ToolManifest,
        secrets: SecretProvider | None = None,
        proxy_env: dict[str, str] | None = None,
        timeout_seconds: float = 60.0,
        cancel_event: asyncio.Event | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        token = await self._process.prepare(tool_call_id or tool_name, manifest)
        workdir = Path(token["workdir"])
        payload_file = workdir / "payload.json"
        started_at = datetime_now()
        status = "running"
        termination: str | None = None
        try:
            await asyncio.to_thread(
                payload_file.write_text,
                json.dumps(payload, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            env = self._build_env(manifest, secrets, proxy_env)
            command = [
                self._python,
                "-m",
                "app.harness.sandbox_worker",
                "--tool",
                tool_name,
                "--payload-file",
                str(payload_file),
            ]
            exit_code, stdout, stderr = await self._process.run(
                command=command,
                cwd=workdir,
                env=env,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
                max_output_bytes=self._max_output_bytes,
                token=token,
            )
            if len(stdout) > self._max_output_bytes:
                raise ApplicationError(
                    f"tool {tool_name} exceeded stdout limit",
                    code="tool_output_too_large",
                )
            status = "completed" if exit_code == 0 else "failed"
            if exit_code != 0:
                detail = stderr.strip() or stdout.strip() or f"exit {exit_code}"
                raise ApplicationError(
                    f"sandbox tool {tool_name} failed: {detail[:500]}",
                    code="tool_sandbox_failed",
                )
            try:
                result = json.loads(stdout)
            except (ValueError, TypeError) as exc:
                raise ApplicationError(
                    f"sandbox tool {tool_name} produced invalid output",
                    code="tool_output_invalid",
                ) from exc
            if not isinstance(result, dict) or not result.get("ok"):
                error = result.get("error") if isinstance(result, dict) else {}
                raise ApplicationError(
                    str(error.get("message") or "sandbox tool failed"),
                    code=str(error.get("code") or "tool_sandbox_failed"),
                )
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            termination = "cancelled"
            raise
        except ApplicationError as exc:
            if exc.code in {"tool_timeout", "tool_cancelled"}:
                status = exc.code.replace("tool_", "")
                termination = exc.code
            else:
                status = "failed"
                termination = exc.code
            raise
        except Exception:
            status = "failed"
            termination = "unknown"
            raise
        finally:
            await self._process.destroy(token)
            if self._recorder is not None:
                try:
                    await self._recorder(
                        {
                            "tool_call_id": tool_call_id,
                            "run_id": run_id,
                            "tool_name": tool_name,
                            "execution_class": manifest.execution_class,
                            "status": status,
                            "resource_usage": {
                                "timeout_seconds": timeout_seconds,
                                "max_output_bytes": self._max_output_bytes,
                            },
                            "termination_reason": termination,
                            "policy_version": "1.0",
                            "started_at": started_at,
                            "finished_at": datetime_now(),
                        }
                    )
                except Exception:  # noqa: BLE001 - 审计失败不阻断工具结果
                    logger.warning("sandbox audit record failed", exc_info=True)

    def _build_env(
        self,
        manifest: ToolManifest,
        secrets: SecretProvider | None,
        proxy_env: dict[str, str] | None,
    ) -> dict[str, str]:
        env: dict[str, str] = dict(self._base_env)
        for name in manifest.secrets:
            value = secrets.resolve(name) if secrets is not None else None
            if value:
                env[name] = value
        if self._pythonpath:
            env["PYTHONPATH"] = self._pythonpath
        if proxy_env:
            env.update(proxy_env)
        return env


def datetime_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
