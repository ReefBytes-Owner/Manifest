"""Individual agent implementations: base class and concrete provider agents.

Dependency graph: agents.config → agents.runners (no other cross-module deps).
"""

import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
import threading
import time
from typing import Any

from manifest_model_policy import (
    FailureClass,
    FailureEvidence,
    FallbackAction,
    FallbackController,
    ModelFallbackMode,
    ResolvedModel,
    classify_failure,
    sdk_failure_evidence,
)

from agents.cli_policy import configured_fallback_tiers
from agents.config import (
    HAS_ANTHROPIC,
    HAS_GENAI,
    HAS_GENAI_NEW,
    Config,
    Logger,
    RateLimiter,
    genai,
)

if HAS_ANTHROPIC:
    from anthropic import AsyncAnthropic


_PROVIDER_STREAM_LIMIT = 64 * 1024
_FALLBACK_CONFIRM_LOCK = threading.Lock()


async def _read_bounded_stream(stream, limit: int = _PROVIDER_STREAM_LIMIT):
    """Drain a subprocess stream while retaining at most ``limit`` bytes."""
    retained = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        room = limit - len(retained)
        if room > 0:
            retained.extend(chunk[:room])
        if len(chunk) > room:
            truncated = True
    return bytes(retained), truncated


def _read_bounded_output_file(path: str, limit: int = _PROVIDER_STREAM_LIMIT):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    return data[:limit], len(data) > limit


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------


class ProviderAttemptError(RuntimeError):
    """Carry bounded provider evidence without exposing raw failure text."""

    def __init__(self, evidence: FailureEvidence):
        super().__init__("provider attempt failed")
        self.evidence = evidence


class BaseAgent:
    """Abstract base class for all agents"""

    def __init__(
        self,
        name: str,
        model: str,
        timeout: int,
        rate_limiter: RateLimiter,
        config: Config | None = None,
        logger: Logger | None = None,
        streaming: bool = False,
        progress_callback=None,
        model_chain=None,
        fallback_mode: str | ModelFallbackMode = ModelFallbackMode.CONFIRM,
        interactive: bool = False,
        confirm_callback=None,
    ):
        self.name = name
        self.model = model
        self.model_name = (
            model  # Concrete name; subclasses re-resolve via _resolve_model
        )
        self.original_model = model  # Track original for fallback
        self.timeout = timeout
        self.rate_limiter = rate_limiter
        self.config = config or Config()
        self.logger = logger
        self.credit_fallback_used = False
        self.streaming = streaming
        self.progress_callback = progress_callback
        if model_chain is None:
            model_chain = configured_fallback_tiers(
                self.config.config, self.name, self.original_model
            )
        self.model_chain = tuple(
            item
            if isinstance(item, ResolvedModel)
            else ResolvedModel(str(item), self._resolve_model(str(item)))
            for item in model_chain
        )
        self.fallback_mode = ModelFallbackMode(fallback_mode)
        self.interactive = interactive
        self.confirm_callback = confirm_callback

    async def _fallback_decision(self, controller, index, failure):
        if not self.interactive or self.confirm_callback is None:
            return controller.decide(index, failure)

        def decide():
            with _FALLBACK_CONFIRM_LOCK:
                return controller.decide(index, failure)

        return await asyncio.to_thread(decide)

    async def execute(self, prompt: str, mode: str = "prompt") -> dict:
        """Execute agent with rate limiting, timeout, and credit fallback"""
        await self.rate_limiter.acquire()
        start_time = time.time()
        deadline = time.monotonic() + self.timeout
        if self.logger:
            self.logger.info(
                f"[{self.name}] Starting execution with model {self.model}"
            )
        chain = self.model_chain
        if chain is None:
            chain = (ResolvedModel(self.model, self.model_name),)
        controller = FallbackController(
            chain,
            self.fallback_mode,
            interactive=self.interactive,
            confirm_callback=self.confirm_callback,
        )
        attempts = []
        last_decision = None
        for index, resolved in enumerate(chain):
            self.model = resolved.tier
            self.model_name = resolved.model_id
            attempts.append({"tier": resolved.tier, "model": resolved.model_id})
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                result = await self._execute_attempt(prompt, mode, remaining)
            except Exception as error:
                timed_out = isinstance(error, TimeoutError)
                evidence = (
                    error.evidence
                    if isinstance(error, ProviderAttemptError)
                    else sdk_failure_evidence(self.name, self.name, error)
                )
                failure = classify_failure(evidence, error=error)
                decision = await self._fallback_decision(controller, index, failure)
                last_decision = decision
                if decision.action is FallbackAction.RETRY:
                    await self._announce_retry(decision)
                    continue
                return self._exception_result(
                    error, timed_out, failure, decision, start_time, attempts
                )
            if result.get("status") == "complete":
                return self._completed_result(
                    result, start_time, attempts, last_decision
                )
            failure = self._returned_failure(result)
            decision = await self._fallback_decision(controller, index, failure)
            last_decision = decision
            if decision.action is FallbackAction.RETRY:
                await self._announce_retry(decision)
                continue
            return self._returned_result(
                result, failure, decision, start_time, attempts
            )
        return self._exhausted_result(start_time, attempts, last_decision)

    async def _execute_attempt(self, prompt: str, mode: str, remaining: float) -> dict:
        if self.streaming and hasattr(self, "_execute_streaming"):
            operation = self._execute_streaming(prompt, mode)
        else:
            operation = self._execute_impl(prompt, mode)
        return await asyncio.wait_for(operation, timeout=remaining)

    async def _announce_retry(self, decision) -> None:
        self.credit_fallback_used = True
        if self.logger:
            self.logger.warning(f"[{self.name}] {decision.message}")
        print(f"  [{self.name}] {decision.message}", file=sys.stderr)
        await asyncio.sleep(1)

    def _completed_result(self, result, start_time, attempts, last_decision):
        completed = dict(result)
        completed["duration_seconds"] = round(time.time() - start_time, 2)
        completed["credit_fallback"] = self.credit_fallback_used
        completed["model_attempts"] = attempts
        completed["fallback_reason"] = (
            last_decision.failure.value if last_decision else None
        )
        completed["fallback_confirmed"] = (
            last_decision.confirmed if last_decision else False
        )
        if self.logger:
            self.logger.info(
                f"[{self.name}] Completed in {completed['duration_seconds']}s"
            )
        return completed

    def _returned_failure(self, result: dict) -> FailureClass:
        try:
            return FailureClass(result.get("fallback_reason"))
        except (TypeError, ValueError):
            pass
        if result.get("status") == "missing":
            return FailureClass.CONFIG
        summary = result.get("failure_summary")
        if not isinstance(summary, dict):
            return FailureClass.UNKNOWN
        exit_status = summary.get("exit_status")
        if isinstance(exit_status, bool) or not isinstance(exit_status, int):
            exit_status = None
        evidence = FailureEvidence(
            provider=self.name,
            harness=self.name,
            exit_status=exit_status,
            output_envelope_status=summary.get("output_envelope_status") or None,
            task_status=summary.get("task_status") or None,
            truncated=summary.get("truncated") in {True, "true"},
        )
        return classify_failure(evidence)

    def _returned_result(self, result, failure, decision, start_time, attempts):
        failed = dict(result)
        failed["duration_seconds"] = round(time.time() - start_time, 2)
        failed["credit_fallback"] = self.credit_fallback_used
        failed["model_attempts"] = attempts
        failed["fallback_reason"] = failure.value
        failed["fallback_confirmed"] = decision.confirmed
        failed["fallback_pending"] = (
            decision.action is FallbackAction.NEEDS_CONFIRMATION
        )
        return failed

    def _exception_result(
        self, error, timed_out, failure, decision, start_time, attempts
    ):
        message = (
            f"Timeout after {self.timeout}s"
            if timed_out
            else f"{type(error).__name__}: provider attempt failed"
        )
        if self.logger:
            self.logger.error(f"[{self.name}] {message}")
        return {
            "status": "failed",
            "error": message.lower() if timed_out else message,
            "duration_seconds": round(time.time() - start_time, 2),
            "credit_fallback": self.credit_fallback_used,
            "model_attempts": attempts,
            "fallback_reason": failure.value,
            "fallback_confirmed": decision.confirmed,
            "fallback_pending": decision.action is FallbackAction.NEEDS_CONFIRMATION,
        }

    def _exhausted_result(self, start_time, attempts, last_decision):
        if self.logger:
            self.logger.error(f"[{self.name}] All credit fallback attempts exhausted")
        return {
            "status": "failed",
            "error": "all credit fallback attempts exhausted",
            "duration_seconds": round(time.time() - start_time, 2),
            "credit_fallback": self.credit_fallback_used,
            "model_attempts": attempts,
            "fallback_reason": last_decision.failure.value if last_decision else None,
            "fallback_confirmed": last_decision.confirmed if last_decision else False,
        }

    def _is_credit_exhaustion_error(self, error: str) -> bool:
        """Check if error indicates credit/quota exhaustion"""
        exhaustion_patterns = [
            "quota",
            "credit",
            "rate limit",
            "capacity",
            "429",
            "too many requests",
            "resource_exhausted",
        ]
        return any(pattern in error for pattern in exhaustion_patterns)

    def _resolve_model(self, tier: str) -> str | None:
        """Resolve a model tier to a concrete model name (subclasses override)."""
        return tier

    def _get_fallback_model(self) -> str | None:
        """Get next fallback model tier"""
        fallback_chain = self.config.get(f"credit_fallback.{self.name}", [])

        # Find current position in fallback chain
        try:
            current_index = fallback_chain.index(self.original_model)
            if current_index < len(fallback_chain) - 1:
                return fallback_chain[current_index + 1]
        except (ValueError, IndexError):
            pass

        return None

    async def _execute_impl(self, prompt: str, mode: str) -> dict:
        """Implementation-specific execution logic"""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ClaudeAgent
# ---------------------------------------------------------------------------


class ClaudeAgent(BaseAgent):
    """Claude agent using official Anthropic SDK (API key only for now)"""

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 120,
        rate_limiter: RateLimiter = None,
        config: Config | None = None,
        logger: Logger | None = None,
        streaming: bool = False,
        progress_callback=None,
    ):
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package not installed")

        config = config or Config()
        super().__init__(
            "claude",
            model,
            timeout,
            rate_limiter,
            config,
            logger,
            streaming,
            progress_callback,
        )
        self.model_name = self._resolve_model(model)
        self.client = self._create_client()

    def _create_client(self) -> "AsyncAnthropic":
        """Create Claude client (API key required)"""
        # Anthropic SDK reads from ANTHROPIC_API_KEY env var automatically
        # No OAuth support yet, but prepared for future
        api_key = os.environ.get("ANTHROPIC_API_KEY")

        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable not set\n"
                "Get your API key from: https://console.anthropic.com/\n"
                "Then: export ANTHROPIC_API_KEY='sk-...'"
            )

        return AsyncAnthropic(api_key=api_key)

    def _resolve_model(self, tier: str) -> str:
        """Resolve model tier to full model name"""
        return self.config.get(f"model_tiers.claude.{tier}", tier)

    async def _execute_impl(self, prompt: str, mode: str) -> dict:
        """Execute Claude API request"""
        response = await self.client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        return {
            "status": "complete",
            "output": response.content[0].text,
            "model": self.model_name,
            "validated": False,
        }

    async def _execute_streaming(self, prompt: str, mode: str) -> dict:
        """Execute Claude API request with streaming"""
        output_buffer = []

        async with self.client.messages.stream(
            model=self.model_name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                output_buffer.append(text)
                if self.progress_callback:
                    await self.progress_callback(self.name, "".join(output_buffer))

        return {
            "status": "complete",
            "output": "".join(output_buffer),
            "model": self.model_name,
            "validated": False,
        }


# ---------------------------------------------------------------------------
# GeminiAgent
# ---------------------------------------------------------------------------


class GeminiAgent(BaseAgent):
    """Gemini agent using official Google SDK with OAuth support"""

    def __init__(
        self,
        model: str = "flash",
        timeout: int = 120,
        rate_limiter: RateLimiter = None,
        config: Config | None = None,
        logger: Logger | None = None,
        streaming: bool = False,
        progress_callback=None,
    ):
        if not HAS_GENAI:
            raise ImportError("google-generativeai package not installed")

        config = config or Config()
        super().__init__(
            "gemini",
            model,
            timeout,
            rate_limiter,
            config,
            logger,
            streaming,
            progress_callback,
        )
        self.model_name = self._resolve_model(model)
        self.client = self._create_client()

    def _create_client(self) -> Any:
        """Create Gemini client with OAuth or API key"""
        api_key = os.environ.get("GOOGLE_API_KEY")

        if HAS_GENAI_NEW:
            # Use new google.genai package
            if api_key:
                return genai.Client(api_key=api_key)
            else:
                # Use OAuth/ADC
                try:
                    return genai.Client()
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"[gemini] OAuth not configured: {e}")
                    print(
                        "  [gemini] OAuth not configured, trying without credentials",
                        file=sys.stderr,
                    )
                    print(
                        "  [gemini] Run the gemini CLI once to complete OAuth"
                        " (or set GOOGLE_API_KEY)",
                        file=sys.stderr,
                    )
                    raise
        else:
            # Use legacy google-generativeai package
            if api_key:
                genai.configure(api_key=api_key)
            else:
                # OAuth with legacy package
                try:
                    genai.configure()
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"[gemini] OAuth not configured: {e}")
                    print("  [gemini] OAuth not configured", file=sys.stderr)
                    print(
                        "  [gemini] Run the gemini CLI once to complete OAuth"
                        " or set GOOGLE_API_KEY",
                        file=sys.stderr,
                    )
                    raise
            return genai

    def _resolve_model(self, tier: str) -> str:
        """Resolve model tier to full model name"""
        return self.config.get(f"model_tiers.gemini.{tier}", tier)

    async def _execute_impl(self, prompt: str, mode: str) -> dict:
        """Execute Gemini API request"""
        if HAS_GENAI_NEW:
            # New package API
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_name,
                contents=prompt,
            )
        else:
            # Legacy package API
            model = genai.GenerativeModel(self.model_name)
            response = await asyncio.to_thread(model.generate_content, prompt)

        return {
            "status": "complete",
            "output": response.text,
            "model": self.model_name,
            "validated": False,
        }

    async def _execute_streaming(self, prompt: str, mode: str) -> dict:
        """Execute Gemini API request with streaming"""
        output_buffer = []

        if HAS_GENAI_NEW:
            # New package streaming API
            response_stream = await asyncio.to_thread(
                self.client.models.generate_content_stream,
                model=self.model_name,
                contents=prompt,
            )
        else:
            # Legacy package streaming API
            model = genai.GenerativeModel(self.model_name)
            response_stream = await asyncio.to_thread(
                model.generate_content, prompt, stream=True
            )

        # Process stream
        for chunk in response_stream:
            if hasattr(chunk, "text"):
                output_buffer.append(chunk.text)
                if self.progress_callback:
                    await self.progress_callback(self.name, "".join(output_buffer))

        return {
            "status": "complete",
            "output": "".join(output_buffer),
            "model": self.model_name,
            "validated": False,
        }


# ---------------------------------------------------------------------------
# CLIAgent
# ---------------------------------------------------------------------------


class CLIAgent(BaseAgent):
    """Generic CLI-based agent driven by the cli_agents config block.

    All provider variation (binary, argument shape, output capture) is data in
    parallel_agent.yml — adding a CLI provider is a configuration change, not a
    new class. Args are always exec'd as a list (never a shell string).
    """

    def __init__(
        self,
        provider: str,
        model: str = "flash",
        timeout: int = 120,
        rate_limiter: RateLimiter = None,
        config: Config | None = None,
        logger: Logger | None = None,
        streaming: bool = False,
        progress_callback=None,
    ):
        config = config or Config()
        super().__init__(
            provider,
            model,
            timeout,
            rate_limiter,
            config,
            logger,
            streaming,
            progress_callback,
        )
        # get_cli_agent_spec falls back to agent_roster.yml when `provider`
        # has no cli_agents.<provider> entry — this is what lets a new
        # CLI-only agent be added purely via the roster, with no code
        # change and no new subclass (see Config.get_cli_agent_spec).
        spec = config.get_cli_agent_spec(provider)
        if not spec:
            raise ValueError(f"no cli_agents config for provider: {provider}")
        self.binary = spec.get("binary")
        if not self.binary:
            raise ValueError(f"cli_agents.{provider}.binary is required but missing")
        self.base_args = list(spec.get("base_args", []))
        self.model_args = list(spec.get("model_args", []))
        self.prompt_args = list(spec.get("prompt_args", ["{prompt}"]))
        self.output_strategy = spec.get("output", "stdout")
        self.model_name = self._resolve_model(model)

    def _resolve_model(self, tier: str) -> str | None:
        """Resolve model tier to full model name. Returns None for 'auto'.

        Delegates to cli_invoke.resolve_provider_model: exact tier first,
        then the cross-provider tier equivalence (a route can hand this
        provider another provider's tier alias, e.g. synthesis's "sonnet"
        landing on antigravity), then verbatim passthrough — which is the
        right behavior for a provider with no `model_tiers.<name>` block
        (devin), so `--devin-model opus` still sends `--model opus`.
        """
        from agents.cli_invoke import resolve_provider_model

        return resolve_provider_model(self.config, self.name, tier)

    def _build_command(self, prompt: str, output_file: str | None = None) -> list[str]:
        """Assemble argv: binary + base_args + optional model group + prompt_args.

        model_args are appended only when a model is resolved — the group is
        dropped atomically, so an optional model can never leave a dangling flag.
        prompt_args controls how the prompt is passed (default: trailing positional
        {prompt}).  The prompt content itself is never template-substituted — only
        the surrounding template text in a prompt_args entry is substituted, then
        the raw prompt is spliced in via {prompt}.
        """

        def _subst(arg: str) -> str:
            arg = arg.replace("{output_file}", output_file or "")
            arg = arg.replace("{model}", self.model_name or "")
            return arg

        def _subst_prompt(arg: str) -> str:
            if "{prompt}" in arg:
                # Split on the placeholder, substitute non-prompt placeholders in the
                # surrounding template text only, then rejoin with the raw prompt —
                # the prompt content itself is never template-substituted.
                return prompt.join(_subst(piece) for piece in arg.split("{prompt}"))
            return _subst(arg)

        cmd = [self.binary]
        for arg in self.base_args:
            substituted = _subst(arg)
            if substituted:
                cmd.append(substituted)
        if self.model_name:
            # Drop empty substitutions here too — a stray {output_file} in
            # model_args would otherwise inject a "" argv element
            cmd += [a for a in (_subst(a) for a in self.model_args) if a]
        for arg in self.prompt_args:
            substituted = _subst_prompt(arg)
            # Keep an empty result only when it carries the prompt itself
            # (preserves positional semantics for an empty prompt)
            if substituted or "{prompt}" in arg:
                cmd.append(substituted)
        return cmd

    def _collect_output(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
        output_file: str | None,
        *,
        truncated: bool = False,
    ) -> dict:
        """Apply the provider's output strategy: file > stdout > stderr-on-error."""
        if returncode != 0:
            evidence = self._failure_evidence(
                returncode, stdout, stderr, truncated=truncated
            )
            return self._failed_result(evidence, classify_failure(evidence))

        output = ""
        output_file_truncated = False
        if output_file and os.path.exists(output_file):
            output_bytes, output_file_truncated = _read_bounded_output_file(output_file)
            output = output_bytes.decode("utf-8", errors="replace").strip()
        if truncated or output_file_truncated:
            evidence = self._failure_evidence(
                returncode,
                stdout,
                stderr,
                truncated=True,
            )
            return self._failed_result(evidence, FailureClass.UNKNOWN)
        if not output:
            output = stdout.decode("utf-8", errors="ignore").strip()
        if not output:
            evidence = FailureEvidence(
                provider=self.name,
                harness=self.name,
                exit_status=returncode,
                output_envelope_status="empty",
            )
            return self._failed_result(evidence, FailureClass.MALFORMED_OUTPUT)
        return {
            "status": "complete",
            "output": output,
            "model": self.model_name or "auto",
            "validated": False,
        }

    def _failure_evidence(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
        *,
        truncated: bool = False,
    ) -> FailureEvidence:
        return FailureEvidence(
            provider=self.name,
            harness=self.name,
            exit_status=returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            truncated=truncated,
        )

    def _failed_result(self, evidence: FailureEvidence, failure: FailureClass) -> dict:
        return {
            "status": "failed",
            "error": f"provider command failed (exit code {evidence.exit_status})",
            "failure_summary": evidence.persisted_summary(),
            "fallback_reason": failure.value,
            "output": "",
            "model": self.model_name or "auto",
        }

    async def _execute_impl(self, prompt: str, mode: str) -> dict:
        if not shutil.which(self.binary):
            return {
                "status": "missing",
                "error": f"{self.binary} command not found",
                "output": "",
            }
        output_file = self._create_output_file()
        try:
            cmd = self._build_command(prompt, output_file)
            return self._process_result(
                *(await self._run_cli_process(cmd)), output_file
            )
        finally:
            if output_file:
                with contextlib.suppress(OSError):
                    os.unlink(output_file)

    def _create_output_file(self) -> str | None:
        if self.output_strategy != "file_then_stdout":
            return None
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix=f"{self.name}_out_"
        ) as temporary:
            return temporary.name

    async def _run_cli_process(self, cmd):
        # Headless CLIs get EOF instead of inheriting stdin and blocking forever.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_task = asyncio.create_task(_read_bounded_stream(proc.stdout))
        stderr_task = asyncio.create_task(_read_bounded_stream(proc.stderr))
        try:
            await proc.wait()
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            await asyncio.gather(stdout_task, stderr_task)
            raise
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
            stdout_task, stderr_task
        )
        return proc.returncode, stdout, stderr, stdout_truncated or stderr_truncated

    def _process_result(self, returncode, stdout, stderr, truncated, output_file):
        if returncode != 0:
            evidence = self._failure_evidence(
                returncode, stdout, stderr, truncated=truncated
            )
            failure = classify_failure(evidence)
            if failure in {
                FailureClass.MODEL_UNAVAILABLE,
                FailureClass.RATE_LIMIT,
                FailureClass.TRANSIENT,
                FailureClass.CAPACITY,
                FailureClass.QUOTA,
                FailureClass.BILLING,
            }:
                raise ProviderAttemptError(evidence)
            return self._failed_result(evidence, failure)
        return self._collect_output(
            returncode,
            stdout,
            stderr,
            output_file,
            truncated=truncated,
        )
