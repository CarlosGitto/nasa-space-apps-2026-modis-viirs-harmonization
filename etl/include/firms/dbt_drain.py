"""Work through a dbt backlog: run `dbt build` over and over until no bounded model has anything pending.

The incremental models take a few blocks (days) or months per run and leave the rest pending (macros/incremental.sql). Each run
logs, per model, `N pending blocks of 7 days, this run: <the ones it takes>`. A backlog (a long backfill, or dbt that failed for a
while) therefore needs many runs. This loops them: it stops when nothing is left, when a run fails (a failing model or test: a
loop must not hide it), or after `max_iterations`.

dbt runs from a scratch working folder with its own copy of profiles.yml, so it needs nothing to be writable in the project:
its target and logs, the `.user.yml` it likes to create and DuckDB's temporary folder for spilling to disk (`.tmp` in the
working folder) all go there.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
PENDING = re.compile(r'(?P<model>"[\w"."]+|[\w.]+)"?: (?P<n>\d+) pending (?:blocks of \d+ days|months), this run: (?P<taken>.*)$')


def remaining_after_run(output: str) -> dict[str, int]:
    """model -> pending blocks / months LEFT after the run that printed `output` (pending - the ones that run took)."""
    left: dict[str, int] = {}
    for line in output.splitlines():
        m = PENDING.search(ANSI.sub("", line).strip())
        if not m:
            continue
        taken = m["taken"].strip()
        n_taken = 0 if taken in ("", "none") else len([d for d in taken.split(",") if d.strip()])
        left[m["model"].strip('"').split('"."')[-1]] = max(int(m["n"]) - n_taken, 0)
    return left


def drain(dbt_executable, project_dir, profiles_yml, max_iterations: int = 150, extra_args: tuple[str, ...] = ()) -> int:
    """Run `dbt build` until nothing is pending; returns how many runs it took. Raises if a run fails or `max_iterations` is not enough."""
    work = Path(tempfile.mkdtemp(prefix="firms_dbt_drain_"))
    try:
        (work / "profiles").mkdir()
        shutil.copy(profiles_yml, work / "profiles" / "profiles.yml")
        cwd = work / "cwd"
        cwd.mkdir()
        env = {**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
        for i in range(1, max_iterations + 1):
            started = time.time()
            result = subprocess.run(
                [str(dbt_executable), "build", "--project-dir", str(project_dir), "--profiles-dir", str(work / "profiles"),
                 "--target-path", str(work / "target"), "--log-path", str(work / "logs"), *extra_args],
                cwd=cwd, env=env, capture_output=True, text=True,
            )
            output = result.stdout + result.stderr
            left = remaining_after_run(output)
            total = sum(left.values())
            detail = ", ".join(f"{k} {v}" for k, v in left.items() if v) or "none"
            print(f"[run {i}] dbt build exit {result.returncode} in {time.time() - started:.0f}s; pending left: {total} ({detail})", flush=True)
            if result.returncode != 0:
                print("\n".join(ANSI.sub("", output).splitlines()[-60:]), flush=True)
                raise RuntimeError(f"dbt build failed in run {i} (exit {result.returncode}); the backlog is not drained")
            if total == 0:
                print(f"Nothing pending after {i} run(s).", flush=True)
                return i
        raise RuntimeError(f"still pending after {max_iterations} runs: raise max_iterations or look at why it does not shrink")
    finally:
        shutil.rmtree(work, ignore_errors=True)
