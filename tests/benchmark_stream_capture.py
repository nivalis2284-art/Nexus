"""Reproduce Python allocation impact of removing unused stream-event capture.

Run from the repository root: python tests/benchmark_stream_capture.py
This synthetic gateway deliberately excludes provider latency and parser costs.
"""
from __future__ import annotations

import asyncio
import gc
import json
import statistics
import subprocess
import sys
import time
import tracemalloc
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nexus.core import agent as current
from nexus.core.workspace import inspect
from nexus.model import APIFamily, ModelEvent, ModelProfile, RuntimeCredentials
from nexus.tools import build

FRAMES = 30_000


class Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Gateway:
    def __init__(self, *args, **kwargs):
        pass

    async def stream(self, *args, **kwargs):
        for i in range(FRAMES):
            yield ModelEvent("text_delta", text=f"fragment-{i:08d} ")
        yield ModelEvent("complete")


def measure(module):
    registry, context = build(root=ROOT)
    agent = module.Agent(
        profile=ModelProfile(api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
                             model="fake", base_url="https://fake.test"),
        credentials=RuntimeCredentials("fake-key"), registry=registry,
        context=context, workspace=inspect(ROOT),
    )
    messages = [{"role": "user", "content": "synthetic allocation measurement"}]
    async def drive():
        async for _ in agent.run(messages):
            pass
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    asyncio.run(drive())
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(messages[-1]["content"]) == FRAMES * 18
    return {"peak_bytes": peak, "seconds": round(elapsed, 4)}


def main():
    baseline = types.ModuleType("nexus.core._benchmark_baseline")
    baseline.__package__ = "nexus.core"
    sys.modules[baseline.__name__] = baseline
    source = subprocess.check_output(["git", "show", "6c3729f:nexus/core/agent.py"], cwd=ROOT, text=True)
    exec(compile(source, "baseline_agent.py", "exec"), baseline.__dict__)
    original_client, original_gateway = current._client, current.ModelGateway
    try:
        for module in (baseline, current):
            module._client = Client
            module.ModelGateway = Gateway
        measure(baseline)
        measure(current)
        results = {"frames": FRAMES, "baseline_commit": "6c3729f"}
        for name, module in (("before", baseline), ("after", current)):
            runs = [measure(module) for _ in range(3)]
            results[name] = {"runs": runs, "median_peak_bytes": statistics.median(r["peak_bytes"] for r in runs)}
        print(json.dumps(results, indent=2))
    finally:
        current._client, current.ModelGateway = original_client, original_gateway


if __name__ == "__main__":
    main()
