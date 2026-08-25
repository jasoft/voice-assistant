import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "set_dsh_model.sh"


def set_model_fast(settings: Path) -> None:
    env = os.environ.copy()
    env["DSH_SETTINGS"] = str(settings)
    subprocess.run([str(SCRIPT), "fast"], check=True, env=env)


def test_fast_route_maps_to_non_reasoning_flash_lite(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: fast
          maxTokens: 6000
        - id: gemini-3.1-flash-lite
agent-default-model:
  provider: cliproxyapp
  model: fast
  reasoningEffort: off
""",
        encoding="utf-8",
    )

    set_model_fast(settings)

    text = settings.read_text(encoding="utf-8")
    assert (
        "- id: gemini-3.1-flash-lite\n"
        "          maxTokens: 512\n"
    ) in text
    assert "model: gemini-3.1-flash-lite" in text
    assert "reasoningEffort:" not in text


def test_fast_route_pins_default_agent_preset(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: gemini-3.1-flash-lite
agent-default-model:
  model: free
agent-presets:
  default: memo-mem0
""",
        encoding="utf-8",
    )

    set_model_fast(settings)

    text = settings.read_text(encoding="utf-8")
    assert "agent-presets:\n  default: memo-minimal\n" in text
    assert "memo-mem0" not in text


def test_fast_route_preserves_model_neighbors(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: slow
          maxTokens: 6000
        - id: gemini-3.1-flash-lite
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
        "- id: gemini-3.1-flash-lite\n"
        "          maxTokens: 512\n"
        "          contextWindow: 128000\n"
    ) in text
    assert "- id: free" in text
