from __future__ import annotations

from pathlib import Path


def generation_log_candidates(root: Path) -> list[Path]:
    """Enumerate only the two shallow generation-log namespaces.

    Sorting directory entries is unnecessary: callers merge and order parsed
    events by timestamp. Avoiding it saves metadata churn on rotating HDDs.
    """
    paths = [root / "photo_generation_trace.txt"]
    paths.extend(root.glob("photo_generation_trace.*.txt"))
    debug_root = root / "photo_debug"
    paths.append(debug_root / "generation.jsonl")
    paths.extend(debug_root.glob("generation.*.jsonl"))
    return paths
