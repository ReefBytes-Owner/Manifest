"""Multi-agent orchestration: runs agents concurrently, scores consensus,
coordinates synthesis and validation.

Dependency graph: config → {validation, synthesis, runners} → orchestrator.
"""

import asyncio
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

# `rich` is an optional terminal-rendering dependency: real classes are used
# when it is installed, and a same-named runtime fallback below covers the
# subset of the API this module actually calls when it is not. `TYPE_CHECKING`
# is always False at runtime, so the fallback's control flow (the `else`
# branch's `try`/`except ImportError`) is unchanged; it only gives the type
# checker a single declared type per name (rich's own) instead of two
# structurally mismatched ones, which is what made `reportAssignmentType`
# fire at every one of these names before.
if TYPE_CHECKING:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
else:
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.panel import Panel
        from rich.progress import (
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )
        from rich.table import Table
    except ImportError:
        # CLI orchestration remains usable without optional terminal rendering.
        class Console:
            def print(self, value=""):
                print(value)

        class Panel:
            def __init__(self, value, **_kwargs):
                self.value = value

            def __str__(self):
                return str(self.value)

        class Table:
            def __init__(self, title=None):
                self.title, self.rows = title, []

            def add_column(self, *_args, **_kwargs):
                return None

            def add_row(self, *values, **_kwargs):
                self.rows.append(values)

            def __str__(self):
                return "\n".join(
                    ([self.title] if self.title else [])
                    + [" | ".join(map(str, row)) for row in self.rows]
                )

        class Progress:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def add_task(self, *_args, **_kwargs):
                return 0

            def update(self, *_args, **_kwargs):
                return None

        class Live(Progress):
            def update(self, *_args, **_kwargs):
                return None

        class SpinnerColumn:
            pass

        class TextColumn:
            def __init__(self, *_args, **_kwargs):
                pass

        class TimeElapsedColumn:
            pass


import contextlib

from agents.config import (
    HAS_ANTHROPIC,
    HAS_GENAI,
    HAS_GENAI_NEW,
    Config,
    Logger,
    genai,
)
from agents.runners import BaseAgent, _read_bounded_stream
from agents.synthesis import SynthesisEngine
from agents.validation import ValidationEngine

if HAS_ANTHROPIC:
    from anthropic import AsyncAnthropic


async def _bounded_probe_output(proc, timeout: int, provider: str):
    """Wait for a provider probe while draining both streams within fixed caps."""
    stdout_task = asyncio.create_task(_read_bounded_stream(proc.stdout))
    stderr_task = asyncio.create_task(_read_bounded_stream(proc.stderr))
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except TimeoutError as error:
        proc.kill()
        await proc.wait()
        await asyncio.gather(stdout_task, stderr_task)
        raise TimeoutError(f"{provider} probe timed out after {timeout}s") from error
    (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
        stdout_task, stderr_task
    )
    if stdout_truncated or stderr_truncated:
        raise RuntimeError(f"{provider} probe output exceeded 64 KiB")
    return stdout, stderr


class Orchestrator:
    """Main orchestrator for parallel agent execution.

    Coordinates multiple agents concurrently, aggregates results, and scores consensus.
    """

    def __init__(
        self,
        agents: list[BaseAgent],
        config: Config,
        validate: bool = False,
        logger: Logger | None = None,
        enable_synthesis: bool = True,
        streaming: bool = True,
    ):
        self.agents = agents
        self.config = config
        self.validate = validate
        self.logger = logger
        self.enable_synthesis = enable_synthesis
        self.streaming = streaming
        self.console = Console()

    async def execute(
        self, prompt: str, mode: str = "prompt", command: str | None = None
    ) -> dict:
        """Run all agents concurrently and synthesize results"""
        start_time = time.time()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.logger:
            self.logger.info(
                f"Starting orchestration: mode={mode}, agents={len(self.agents)}"
            )

        # Run agents in parallel (with or without streaming)
        if self.streaming and all(
            hasattr(agent, "_execute_streaming") for agent in self.agents
        ):
            agent_results = await self._execute_with_streaming(prompt, mode, timestamp)
        else:
            agent_results = await self._execute_without_streaming(prompt, mode)

        # Calculate consensus
        consensus = self._calculate_consensus(agent_results)

        if self.logger:
            self.logger.info(f"Consensus score: {consensus['consensus_score']}%")

        # Synthesis (if needed and enabled)
        synthesis_result = None
        if self.enable_synthesis and self.config.get("synthesis.enabled", True):
            synthesizer = SynthesisEngine(self.config, self.logger)
            synthesis_result = await synthesizer.synthesize(
                prompt, agent_results, consensus
            )
            if synthesis_result and synthesis_result.get("triggered"):
                consensus["synthesis"] = synthesis_result
                if self.logger:
                    self.logger.info("Synthesis completed")

        # Validation (if requested)
        validation_result = None
        if self.validate:
            validation_result = self._validate_results(
                agent_results, consensus, mode, command
            )
            if self.logger:
                self.logger.info(f"Validation verdict: {validation_result['verdict']}")

        total_duration = round(time.time() - start_time, 2)
        minutes, seconds = divmod(int(total_duration), 60)
        duration_formatted = f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"

        result = {
            "timestamp": timestamp,
            "mode": mode,
            "prompt": prompt,
            "duration_seconds": total_duration,
            "duration_formatted": duration_formatted,
            "agents": agent_results,
            "cross_verification": consensus,
            "validation": validation_result,
            "output_files": {},
        }

        # Write output files
        output_files = await self._write_output_files(result, timestamp)
        result["output_files"] = output_files

        # Log performance metrics
        if self.logger:
            self._log_performance_metrics(result, start_time)

        return result

    async def _execute_without_streaming(self, prompt: str, mode: str) -> dict:
        """Execute agents without streaming (legacy mode)"""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        ) as progress:
            _task = progress.add_task(
                f"Running {len(self.agents)} agents...", total=None
            )

            results = await asyncio.gather(
                *[agent.execute(prompt, mode) for agent in self.agents],
                return_exceptions=True,
            )

        # Build results dictionary
        agent_results = {}
        for agent, result in zip(self.agents, results, strict=False):
            if isinstance(result, Exception):
                agent_results[agent.name] = {
                    "status": "failed",
                    "error": str(result),
                    "output": "",
                }
            else:
                agent_results[agent.name] = result

        return agent_results

    async def _execute_with_streaming(
        self, prompt: str, mode: str, timestamp: str
    ) -> dict:
        """Execute agents with live streaming display"""
        agent_panels = {agent.name: "" for agent in self.agents}

        async def update_callback(agent_name: str, partial_output: str):
            """Update streaming display"""
            max_display_chars = self.config.get("streaming.max_display_chars", 500)
            agent_panels[agent_name] = partial_output[:max_display_chars]

        # Set progress callback for all agents
        for agent in self.agents:
            agent.progress_callback = update_callback

        # Create live display
        try:
            with Live(
                self._build_streaming_layout(agent_panels),
                refresh_per_second=self.config.get("streaming.refresh_rate", 4),
                console=self.console,
                transient=True,
            ) as live:
                # Run agents in parallel
                results = await asyncio.gather(
                    *[agent.execute(prompt, mode) for agent in self.agents],
                    return_exceptions=True,
                )

                # Final update
                live.update(self._build_streaming_layout(agent_panels))

        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"Streaming display failed: {e}, falling back to non-streaming"
                )
            # Fallback to non-streaming
            return await self._execute_without_streaming(prompt, mode)

        # Build results dictionary
        agent_results = {}
        for agent, result in zip(self.agents, results, strict=False):
            if isinstance(result, Exception):
                agent_results[agent.name] = {
                    "status": "failed",
                    "error": str(result),
                    "output": "",
                }
            else:
                agent_results[agent.name] = result

        return agent_results

    def _build_streaming_layout(self, agent_panels: dict[str, str]) -> Panel:
        """Build rich panel layout for streaming display"""
        panel_text = ""
        for agent_name, output in agent_panels.items():
            status = "🔄" if output else "⏳"
            panel_text += f"\n[bold cyan]{status} {agent_name.title()}:[/bold cyan]\n"
            if output:
                panel_text += f"{output[:500]}{'...' if len(output) > 500 else ''}\n"
            else:
                panel_text += "[dim]Waiting for response...[/dim]\n"

        return Panel(panel_text, title="Parallel Agent Execution", border_style="blue")

    def _log_performance_metrics(self, result: dict, start_time: float):
        """Log performance metrics"""
        if not self.logger:
            return

        total_duration = time.time() - start_time
        consensus_score = result["cross_verification"].get("consensus_score", 0)

        self.logger.info(f"Total duration: {total_duration:.2f}s")
        self.logger.info(f"Consensus: {consensus_score}%")

        for agent_name, agent_result in result["agents"].items():
            duration = agent_result.get("duration_seconds", 0)
            status = agent_result.get("status", "unknown")
            credit_fallback = agent_result.get("credit_fallback", False)

            self.logger.info(
                f"[{agent_name}] status={status}, duration={duration}s, "
                f"credit_fallback={credit_fallback}"
            )

    def _calculate_consensus(self, results: dict) -> dict:
        """Calculate cross-verification consensus score"""
        outputs = [
            r.get("output", "")
            for r in results.values()
            if r.get("status") == "complete"
        ]

        if len(outputs) < 2:
            return {
                "consensus_score": 0,
                "confidence": "low",
                "agent_count": len(outputs),
            }

        # Simple keyword-based consensus (placeholder for more sophisticated analysis)
        # Count common significant words (>4 chars) across outputs
        # Performance optimization: collections.Counter is ~20% faster than manual
        # dict.get() updates with set merging due to C-level optimizations.
        # Further optimization: set comprehension and direct length calculation
        # reduces overhead and memory allocations.
        word_counts = Counter(
            word
            for output in outputs
            for word in {w for w in output.lower().split() if len(w) > 4}
        )
        total_words = len(word_counts)

        # Calculate consensus as % of words appearing in multiple outputs
        if not total_words:
            consensus_score = 0
        else:
            # ⚡ Bolt: list-comp + len() avoids per-item generator overhead (measurably
            # faster on the small word_counts this once-per-run path sees)
            common_words = len([1 for count in word_counts.values() if count > 1])
            consensus_score = int((common_words / total_words) * 100)

        # Determine confidence level. Thresholds are fractions (0.80/0.50)
        # while consensus_score is 0-100 — normalize before comparing,
        # matching validation.py and synthesis.py (issue #305).
        thresholds = self.config.get("validation.consensus_threshold", {})
        consensus_fraction = consensus_score / 100.0
        if consensus_fraction >= thresholds.get("high", 0.80):
            confidence = "high"
        elif consensus_fraction >= thresholds.get("medium", 0.50):
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "consensus_score": consensus_score,
            "confidence": confidence,
            "agent_count": len(outputs),
        }

    def _validate_results(
        self, results: dict, consensus: dict, mode: str, command: str | None = None
    ) -> dict:
        """Validate results against success criteria"""
        validator = ValidationEngine(self.config, self.logger)
        return validator.validate(results, consensus, mode, command)

    def _resolve_output_dir(self, custom_output_dir: str | None = None) -> Path:
        """Resolve output directory with sandbox-aware fallback.

        Tries directories in order:
        1. custom_output_dir (if provided via --output)
        2. ~/.claude/.agent_outputs (default from config)
        3. /tmp/.claude_agent_outputs_{pid} (fallback on permission error)
        """
        if custom_output_dir:
            return Path(custom_output_dir).expanduser()

        default_dir = Path(
            self.config.get("output.directory", "~/.claude/.agent_outputs")
        ).expanduser()

        try:
            default_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return default_dir
        except (OSError, PermissionError) as e:
            fallback = Path(f"/tmp/.claude_agent_outputs_{os.getpid()}")
            if self.logger:
                self.logger.warning(
                    f"Cannot write to {default_dir}: {e}. Falling back to {fallback}"
                )
            print(
                f"  Warning: Cannot write to {default_dir}, using fallback: {fallback}",
                file=sys.stderr,
            )
            return fallback

    async def _write_output_files(
        self,
        result: dict,
        timestamp: str,
        custom_output_dir: str | None = None,
        full_output: bool = True,
    ) -> dict:
        """Write output files to disk with sandbox-aware fallback"""
        output_dir = self._resolve_output_dir(custom_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        output_files = {}

        # Write individual agent outputs
        for agent_name, agent_result in result["agents"].items():
            output_file = output_dir / f"{agent_name}_{timestamp}.txt"
            with open(output_file, "w") as f:
                f.write(f"Agent: {agent_name}\n")
                f.write(f"Status: {agent_result.get('status')}\n")
                f.write(f"Model: {agent_result.get('model', 'N/A')}\n")
                f.write(f"Duration: {agent_result.get('duration_seconds')}s\n")
                if agent_result.get("credit_fallback"):
                    f.write("Credit Fallback: Yes\n")
                f.write("\n--- Output ---\n\n")

                output_text = agent_result.get("output", agent_result.get("error", ""))
                if full_output:
                    f.write(output_text)
                else:
                    # Truncate to first 1000 chars if not full output
                    f.write(output_text[:1000])
                    if len(output_text) > 1000:
                        f.write("\n\n... [truncated] ...")

            output_files[agent_name] = str(output_file)

        # Write JSON results
        json_file = output_dir / f"results_{timestamp}.json"
        with open(json_file, "w") as f:
            json.dump(result, f, indent=2)
        output_files["json"] = str(json_file)

        # Write markdown summary
        md_file = output_dir / f"summary_{timestamp}.md"
        with open(md_file, "w") as f:
            f.write("# Parallel Agent Results\n\n")
            f.write(f"**Timestamp**: {timestamp}\n")
            f.write(f"**Mode**: {result['mode']}\n")
            f.write(f"**Prompt**: {result['prompt']}\n\n")

            f.write("## Cross-Verification\n\n")
            consensus = result["cross_verification"]
            f.write(f"- **Consensus Score**: {consensus['consensus_score']}%\n")
            f.write(f"- **Confidence**: {consensus['confidence'].upper()}\n")
            f.write(f"- **Agent Count**: {consensus['agent_count']}\n\n")

            if result.get("validation"):
                f.write("## Validation\n\n")
                f.write(f"- **Verdict**: {result['validation']['verdict']}\n\n")

            f.write("## Agent Results\n\n")
            for agent_name, agent_result in result["agents"].items():
                status = agent_result.get("status")
                if status == "complete":
                    status_icon = "✓"
                elif status in ("failed", "error"):
                    status_icon = "✗"
                else:
                    status_icon = "○"
                f.write(f"### {status_icon} {agent_name.title()}\n\n")
                f.write(f"- **Status**: {agent_result.get('status')}\n")
                f.write(f"- **Model**: {agent_result.get('model', 'N/A')}\n")
                f.write(f"- **Duration**: {agent_result.get('duration_seconds')}s\n")
                if agent_result.get("credit_fallback"):
                    f.write("- **Credit Fallback**: Used\n")
                if agent_result.get("error"):
                    f.write(f"- **Error**: {agent_result['error']}\n")
                f.write("\n")

        output_files["summary"] = str(md_file)

        self._prune_old_outputs(output_dir)

        return output_files

    def _prune_old_outputs(self, output_dir: Path) -> None:
        """Keep only the newest output.keep_last runs (issue #310).

        Runs are identified by their results_<timestamp>.json file; all files
        sharing a pruned run's timestamp are removed with it.
        """
        # Fail open on invalid config — a non-numeric keep_last must not
        # fail the whole run during output writing
        try:
            keep_last = int(self.config.get("output.keep_last") or 0)
        except (TypeError, ValueError):
            return
        if keep_last <= 0:
            return
        # Timestamps are YYYYMMDD_HHMMSS, so lexicographic == chronological
        runs = sorted(output_dir.glob("results_*.json"))
        for stale in runs[:-keep_last]:
            ts = stale.stem[len("results_") :]
            for f in output_dir.glob(f"*_{ts}.*"):
                with contextlib.suppress(OSError):
                    f.unlink()

    def print_results(self, result: dict, json_output: bool = False):
        """Print results in table or JSON format"""
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            self._print_table(result)
            self._print_summary(result)

    def _print_table(self, result: dict):
        """Print results as formatted table"""
        table = Table(title="Parallel Agent Results")
        table.add_column("Agent", style="cyan")
        table.add_column("Status")
        table.add_column("Time", justify="right", style="yellow")
        table.add_column("Model", style="blue")

        for agent_name, agent_result in result["agents"].items():
            status = agent_result.get("status", "unknown")
            duration = f"{agent_result.get('duration_seconds', 0):.2f}s"
            model = agent_result.get("model", "N/A")

            if status == "complete":
                status_icon, status_color = "✔", "green"
            elif status in ("failed", "error"):
                status_icon, status_color = "✗", "red"
            else:
                status_icon, status_color = "○", "yellow"
            table.add_row(
                agent_name.title(),
                f"[{status_color}]{status_icon} {status}[/{status_color}]",
                duration,
                model,
            )

        self.console.print(table)

    def _print_summary(self, result: dict):
        """Print consensus summary"""
        consensus = result["cross_verification"]

        confidence = consensus["confidence"].upper()
        conf_color = (
            "green"
            if confidence == "HIGH"
            else "yellow"
            if confidence == "MEDIUM"
            else "red"
        )

        self.console.print(
            f"\n[bold]Consensus:[/bold] {consensus['consensus_score']}% ([{conf_color}]{confidence}[/{conf_color}])"
        )
        self.console.print(f"[bold]Agents:[/bold] {consensus['agent_count']}")

        if result.get("validation"):
            verdict = result["validation"]["verdict"]
            color = (
                "green"
                if verdict == "APPROVED"
                else "yellow"
                if verdict == "NEEDS_REVIEW"
                else "red"
            )
            self.console.print(f"[bold]Validation:[/bold] [{color}]{verdict}[/{color}]")


# ---------------------------------------------------------------------------
# check_credits (module-level utility closely tied to Orchestrator)
# ---------------------------------------------------------------------------


async def check_credits(
    config: Config,
    logger: Logger | None = None,
    probe_timeout: float = 15,
) -> dict:
    """Pre-flight credit check with minimal API calls"""
    results = {}

    # Claude credit check
    if HAS_ANTHROPIC:
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                client = AsyncAnthropic(api_key=api_key)
                # Make minimal call (haiku, 10 tokens)
                await asyncio.wait_for(
                    client.messages.create(
                        model=config.get(
                            "model_tiers.claude.haiku", "claude-haiku-4-5-20251001"
                        ),
                        max_tokens=10,
                        messages=[{"role": "user", "content": "test"}],
                    ),
                    timeout=10,
                )
                results["claude"] = {"status": "available"}
            else:
                results["claude"] = {"status": "no_api_key"}
        except Exception as e:
            error_str = str(e).lower()
            if "quota" in error_str or "credit" in error_str:
                results["claude"] = {"status": "quota_exceeded", "error": str(e)}
            else:
                results["claude"] = {"status": "error", "error": str(e)}
    else:
        results["claude"] = {"status": "not_installed"}

    # Gemini credit check
    if HAS_GENAI:
        try:
            api_key = os.environ.get("GOOGLE_API_KEY")
            gemini_flash = config.get(
                "model_tiers.gemini.flash", "gemini-3-flash-preview"
            )
            if HAS_GENAI_NEW:
                client = genai.Client(api_key=api_key) if api_key else genai.Client()
                await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=gemini_flash,
                        contents="test",
                    ),
                    timeout=10,
                )
            else:
                if api_key:
                    genai.configure(api_key=api_key)
                model = genai.GenerativeModel(gemini_flash)
                await asyncio.wait_for(
                    asyncio.to_thread(model.generate_content, "test"), timeout=10
                )
            results["gemini"] = {"status": "available"}
        except Exception as e:
            error_str = str(e).lower()
            if "quota" in error_str or "resource_exhausted" in error_str:
                results["gemini"] = {"status": "quota_exceeded", "error": str(e)}
            else:
                results["gemini"] = {"status": "error", "error": str(e)}
    else:
        results["gemini"] = {"status": "not_installed"}

    # Cursor (no API to check, assume available)
    results["cursor"] = {"status": "assumed_available"}

    # Antigravity credit check (subscription CLI, no credit API — probe like codex)
    if shutil.which("agy"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "agy",
                "-p",
                "respond with OK",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await _bounded_probe_output(
                proc, probe_timeout, "antigravity"
            )
            error_output = stderr.decode("utf-8", errors="ignore").lower()

            if any(
                p in error_output
                for p in ("quota", "credit", "rate limit", "429", "unauthorized")
            ):
                results["antigravity"] = {
                    "status": "quota_exceeded",
                    "error": stderr.decode("utf-8", errors="ignore"),
                }
            elif proc.returncode == 0:
                results["antigravity"] = {"status": "available"}
            else:
                results["antigravity"] = {
                    "status": "error",
                    "error": stderr.decode("utf-8", errors="ignore"),
                }
        except (TimeoutError, Exception) as e:
            results["antigravity"] = {"status": "error", "error": str(e)}
    else:
        results["antigravity"] = {"status": "not_installed"}

    # Codex credit check
    if shutil.which("codex"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--model",
                config.get("model_tiers.codex.mini", "gpt-5.6-luna"),
                "respond with OK",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await _bounded_probe_output(proc, probe_timeout, "codex")
            error_output = stderr.decode("utf-8", errors="ignore").lower()

            if any(
                p in error_output
                for p in ("quota", "credit", "rate limit", "429", "unauthorized")
            ):
                results["codex"] = {
                    "status": "quota_exceeded",
                    "error": stderr.decode("utf-8", errors="ignore"),
                }
            elif proc.returncode == 0:
                results["codex"] = {"status": "available"}
            else:
                results["codex"] = {
                    "status": "error",
                    "error": stderr.decode("utf-8", errors="ignore"),
                }
        except (TimeoutError, Exception) as e:
            results["codex"] = {"status": "error", "error": str(e)}
    else:
        results["codex"] = {"status": "not_installed"}

    # Devin credit check. Unlike the codex/agy probes above this spends no
    # tokens: `devin models list` prints the account's catalog when logged in
    # and exits 1 with "Not logged in." when it is not, so the account state
    # is readable without an inference call. `devin auth status` is NOT usable
    # here — it prints "Not logged in." and still exits 0.
    if shutil.which("devin"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "devin",
                "models",
                "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _bounded_probe_output(proc, probe_timeout, "devin")
            combined = (stderr + stdout).decode("utf-8", errors="ignore")
            lowered = combined.lower()

            if "not logged in" in lowered or "unauthorized" in lowered:
                results["devin"] = {
                    "status": "not_authenticated",
                    "error": combined.strip(),
                }
            elif any(p in lowered for p in ("quota", "credit", "rate limit", "429")):
                results["devin"] = {"status": "quota_exceeded", "error": combined}
            elif proc.returncode == 0:
                results["devin"] = {"status": "available"}
            else:
                results["devin"] = {"status": "error", "error": combined}
        except (TimeoutError, Exception) as e:
            results["devin"] = {"status": "error", "error": str(e)}
    else:
        results["devin"] = {"status": "not_installed"}

    return results
