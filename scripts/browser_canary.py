#!/usr/bin/env python3
"""Deterministic browser lifecycle and cgroup resource admission canary."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


CONTENT = " ".join(f"browser-canary-word-{index}" for index in range(160))
URL = (
    "data:text/html,"
    "<html><head><title>Argus Browser Canary</title></head>"
    f"<body><main>{CONTENT}</main></body></html>"
)


def _read_cgroup_value(name: str) -> int:
    candidates = (
        Path("/sys/fs/cgroup") / name,
        Path("/sys/fs/cgroup/memory") / name,
    )
    for candidate in candidates:
        try:
            return int(candidate.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            continue
    raise RuntimeError(f"required cgroup metric is unavailable: {name}")


def _memory_peak_bytes() -> int:
    try:
        return _read_cgroup_value("memory.peak")
    except RuntimeError:
        return _read_cgroup_value("memory.max_usage_in_bytes")


def _oom_events() -> int:
    events = Path("/sys/fs/cgroup/memory.events")
    if events.is_file():
        values = dict(
            line.split(maxsplit=1)
            for line in events.read_text(encoding="utf-8").splitlines()
        )
        return int(values.get("oom", "0")) + int(values.get("oom_kill", "0"))
    try:
        return _read_cgroup_value("memory.failcnt")
    except RuntimeError:
        return 0


def _runtime_kind(command: str) -> str | None:
    if any(marker in command for marker in ("chrome-headless-shell", "chromium")):
        return "browser"
    if "playwright/driver" in command or "playwright/driver/package/cli.js" in command:
        return "playwright_driver"
    return None


def _runtime_processes() -> list[dict[str, object]]:
    processes: list[dict[str, object]] = []
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            command = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode()
            status = (proc_dir / "status").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, UnicodeDecodeError):
            continue
        kind = _runtime_kind(command)
        if kind is None:
            continue
        uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
        real_uid = int(uid_line.split()[1])
        processes.append(
            {
                "pid": int(proc_dir.name),
                "kind": kind,
                "uid": real_uid,
                "no_sandbox": "--no-sandbox" in command,
            }
        )
    return processes


async def _wait_for_runtime_exit() -> list[dict[str, object]]:
    for _ in range(50):
        remaining = _runtime_processes()
        if not remaining:
            return []
        await asyncio.sleep(0.1)
    return _runtime_processes()


async def run(expect_missing: bool, attempts: int, memory_limit_mib: int) -> None:
    import playwright.async_api

    import argus.extraction.playwright_extractor as extractor

    runtime_starts = 0
    real_factory = playwright.async_api.async_playwright

    def counted_factory():
        nonlocal runtime_starts
        runtime_starts += 1
        return real_factory()

    playwright.async_api.async_playwright = counted_factory
    oom_before = _oom_events()
    results = []
    try:
        for _ in range(attempts):
            results.append(await extractor._extract_playwright(URL, timeout_ms=5_000))

        if expect_missing:
            assert runtime_starts == 1, runtime_starts
            assert all(result.error == "playwright: not available" for result in results)
            status = extractor.browser_capability_status()
            assert status["declared"] is True
            assert status["available"] is False
            assert status["loaded"] is False
            assert status["matches_declared"] is False
            assert status["degraded_reason"] == "browser_artifact_unavailable"
            live_processes = _runtime_processes()
            assert not live_processes, live_processes
        else:
            assert runtime_starts == 1, runtime_starts
            assert all(result.error is None for result in results)
            assert all(result.title == "Argus Browser Canary" for result in results)
            status = extractor.browser_capability_status()
            assert status["declared"] is True
            assert status["available"] is True
            assert status["loaded"] is True
            assert status["sandboxed"] is True
            live_processes = _runtime_processes()
            browser_processes = [
                process for process in live_processes if process["kind"] == "browser"
            ]
            assert browser_processes
            assert all(process["uid"] != 0 for process in live_processes)
            assert not any(process["no_sandbox"] for process in browser_processes)
    finally:
        playwright.async_api.async_playwright = real_factory
        await extractor.close_browser()

    remaining = await _wait_for_runtime_exit()
    peak_bytes = _memory_peak_bytes()
    oom_after = _oom_events()
    assert not remaining, remaining
    assert oom_after == oom_before, (oom_before, oom_after)
    if not expect_missing:
        assert peak_bytes <= int(memory_limit_mib * 1024 * 1024 * 0.8), peak_bytes

    print(
        json.dumps(
            {
                "attempts": attempts,
                "expect_missing": expect_missing,
                "runtime_starts": runtime_starts,
                "peak_mib": round(peak_bytes / 1024 / 1024, 2),
                "oom_events": oom_after - oom_before,
                "orphan_runtime_processes": len(remaining),
                "uid": os.getuid(),
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-missing", action="store_true")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--memory-limit-mib", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.expect_missing, args.attempts, args.memory_limit_mib))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
