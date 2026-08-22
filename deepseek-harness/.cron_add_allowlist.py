#!/usr/bin/env python3
"""Switch command_allowlist entries to glob form."""
import yaml

CONFIG = "/Users/weiwang/.hermes/config.yaml"
with open(CONFIG) as f:
    cfg = yaml.safe_load(f)
allow = cfg.get("command_allowlist") or []
for old, new in [("hermes update", "hermes update*"), ("hermes gateway restart", "hermes gateway *")]:
    if old in allow:
        allow[allow.index(old)] = new
cfg["command_allowlist"] = allow
with open(CONFIG, "w") as f:
    yaml.dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
print(allow)
