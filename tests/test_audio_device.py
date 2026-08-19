from __future__ import annotations

from types import SimpleNamespace

from press_to_talk.audio.recorder import (
    calibrated_speech_threshold,
    open_input_stream_with_retry,
)
from press_to_talk.models.config import parse_args


def test_parse_args_reads_optional_input_device(monkeypatch) -> None:
    monkeypatch.setenv("PTT_INPUT_DEVICE", "USB Microphone")

    config = parse_args(["--user-id", "test-user"], load_env=False)

    assert config.input_device == "USB Microphone"


def test_input_stream_passes_selected_device_to_portaudio() -> None:
    calls = []

    def stream_factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    stream = open_input_stream_with_retry(
        stream_factory=stream_factory,
        device=3,
        samplerate=16000,
        channels=1,
        dtype="float32",
        callback=lambda *_args: None,
    )

    assert stream is not None
    assert calls[0]["device"] == 3


def test_calibrated_threshold_allows_lower_gain_microphones() -> None:
    threshold = calibrated_speech_threshold(0.018, 0.002)

    assert 0.008 <= threshold < 0.018


def test_wireless_input_keeps_both_channels_for_stream_open() -> None:
    calls = []

    def stream_factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    stream = open_input_stream_with_retry(
        stream_factory=stream_factory,
        device="Wireless Mic Rx",
        samplerate=16000,
        channels=2,
        dtype="float32",
        callback=lambda *_args: None,
    )

    assert stream is not None
    assert calls[0]["device"] == "Wireless Mic Rx"
    assert calls[0]["channels"] == 2
