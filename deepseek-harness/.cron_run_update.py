#!/usr/bin/env python3
"""Run the non-interactive Hermes update (update performs its own graceful
gateway restart as final phase). Launched from cron because the in-process
approval allowlist only reloads on gateway restart."""
import subprocess
import sys

r = subprocess.run(
    ["hermes", "update", "--yes"],
    stdin=subprocess.DEVNULL,
    capture_output=True,
    text=True,
    timeout=540,
)
log = "/Users/weiwang/.hermes/logs/cron_update.log"
with open(log, "a") as f:
    f.write("=== wrapper start ===\n")
    f.write(r.stdout[-8000:] if r.stdout else "")
    f.write(r.stderr[-2000:] if r.stderr else "")
    f.write(f"\n=== wrapper exit: {r.returncode} ===\n")
print("exit:", r.returncode)
print((r.stdout or "")[-3000:])
if r.stderr:
    print("STDERR:", r.stderr[-1500:])
sys.exit(r.returncode)
