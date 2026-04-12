from __future__ import annotations

from pathlib import Path
from .models.config import load_intent_samples
from .agent.agent import OpenAICompatibleAgent

async def run_intent_regression(agent: OpenAICompatibleAgent, sample_path: Path) -> int:
    samples = load_intent_samples(sample_path)
    passed = 0
    counts: dict[str, int] = {}
    failures: list[tuple[int, str, str, str]] = []

    for index, sample in enumerate(samples, start=1):
        predicted = await agent.classify_intent(sample["text"])
        expected = sample["intent"]
        counts[expected] = counts.get(expected, 0) + 1
        ok = predicted == expected
        status = "OK" if ok else "FAIL"
        print(
            f"[{status}] {index:02d} expected={expected} predicted={predicted} text={sample['text']}"
        )
        if ok:
            passed += 1
        else:
            failures.append((index, expected, predicted, sample["text"]))

    total = len(samples)
    print(f"summary: {passed}/{total} matched")
    print(
        "counts: "
        + ", ".join(f"{intent}={count}" for intent, count in sorted(counts.items()))
    )
    if failures:
        print("mismatches:")
        for index, expected, predicted, text in failures:
            print(f"  - {index:02d} expected={expected} predicted={predicted} text={text}")
    return 0 if passed == total else 1
