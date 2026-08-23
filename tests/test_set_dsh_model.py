import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "set_dsh_model.sh"


def set_model_fast(settings: Path) -> None:
    env = os.environ.copy()
    env["DSH_SETTINGS"] = str(settings)
    subprocess.run([str(SCRIPT), "fast"], check=True, env=env)


EXPECTED_FAST_PROFILE = """- id: fast
          maxTokens: 256
          reasoningEfforts:
            off: "none"
            high: high
          compat:
            supportsDeveloperRole: false
            supportsReasoningEffort: true
"""


def test_fast_model_pins_existing_max_tokens(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: fast
          maxTokens: 6000
agent-default-model:
  provider: cliproxyapp
  model: free
""",
        encoding="utf-8",
    )

    set_model_fast(settings)

    text = settings.read_text(encoding="utf-8")
    assert EXPECTED_FAST_PROFILE in text
    assert "model: fast" in text
    assert "reasoningEffort: off" in text


def test_fast_model_adds_missing_max_tokens(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: fast
agent-default-model:
  provider: cliproxyapp
  model: free
""",
        encoding="utf-8",
    )

    set_model_fast(settings)

    text = settings.read_text(encoding="utf-8")
    assert EXPECTED_FAST_PROFILE in text
    assert "reasoningEffort: off" in text


def test_fast_model_replaces_existing_reasoning_and_preserves_neighbors(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: slow
          maxTokens: 6000
        - id: fast
          maxTokens: 6000
          reasoningEfforts:
            high: high
          compat:
            supportsReasoningEffort: false
          contextWindow: 128000
        - id: free
agent-default-model:
  provider: cliproxyapp
  model: free
""",
        encoding="utf-8",
    )

    set_model_fast(settings)

    text = settings.read_text(encoding="utf-8")
    assert "- id: slow\n          maxTokens: 6000" in text
    assert (
        "- id: fast\n"
        "          maxTokens: 256\n"
        "          reasoningEfforts:\n"
        "            off: \"none\"\n"
        "            high: high\n"
        "          compat:\n"
        "            supportsDeveloperRole: false\n"
        "            supportsReasoningEffort: true\n"
        "          contextWindow: 128000\n"
    ) in text
    assert "supportsReasoningEffort: false" not in text
    assert "- id: free" in text
