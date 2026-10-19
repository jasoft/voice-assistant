import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "set_dsh_model.sh"


def test_default_model_registers_fast_idempotently(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text("""llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: fast
          maxTokens: 4096
agent-default-model:
  provider: cliproxyapp
  model: paid
""")
    env = {**os.environ, "DSH_SETTINGS": str(settings)}
    for _ in range(2):
        subprocess.run([str(SCRIPT)], check=True, env=env)
    text = settings.read_text()
    assert "model: fast\n" in text
    assert "- id: fast\n          maxTokens: 4096" in text


def test_explicit_paid_model_registers_idempotently(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text("""llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: fast
          maxTokens: 4096
agent-default-model:
  provider: cliproxyapp
  model: fast
""")
    env = {**os.environ, "DSH_SETTINGS": str(settings)}
    for _ in range(2):
        subprocess.run([str(SCRIPT), "paid"], check=True, env=env)
    text = settings.read_text()
    assert "model: paid\n" in text
    assert text.count("- id: paid\n") == 1
    assert "- id: fast\n          maxTokens: 4096" in text


def test_explicit_luna_model_registers_idempotently(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text("""llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: fast
          maxTokens: 4096
agent-default-model:
  provider: cliproxyapp
  model: fast
""")
    env = {**os.environ, "DSH_SETTINGS": str(settings)}
    for _ in range(2):
        subprocess.run([str(SCRIPT), "gpt-6-luna"], check=True, env=env)
    text = settings.read_text()
    assert "model: gpt-6-luna\n" in text
    assert text.count("- id: gpt-6-luna\n") == 1
    assert "- id: fast\n          maxTokens: 4096" in text


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
        "- id: fast\n"
        "          maxTokens: 4096\n"
    ) in text
    assert "model: fast" in text


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
    assert "agent-presets:\n  default: chat-fast\n" in text
    assert "memo-mem0" not in text
    assert "memo-minimal" not in text


def test_fast_route_preserves_model_neighbors(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """llm-pi-ai:
  providers:
    cliproxyapp:
      models:
        - id: slow
          maxTokens: 6000
        - id: fast
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
        "- id: fast\n"
        "          maxTokens: 4096\n"
    ) in text
    assert (
        "- id: gemini-3.1-flash-lite\n"
        "          contextWindow: 128000\n"
    ) in text
    assert "- id: free" in text
