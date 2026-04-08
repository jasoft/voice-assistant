#!/usr/bin/env python3
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16000


def render_chime(kind: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    if kind == "start":
        notes = [(587.33, 0.09, 0.16), (783.99, 0.12, 0.13)]
        echo_delay = 0.055
        echo_decay = 0.18
    elif kind == "end":
        notes = [(783.99, 0.09, 0.15), (587.33, 0.14, 0.12)]
        echo_delay = 0.06
        echo_decay = 0.16
    else:
        raise ValueError(f"unknown chime kind: {kind}")

    def synth_note(freq: float, dur: float, amp: float) -> np.ndarray:
        frames = max(int(sample_rate * dur), 1)
        t = np.linspace(0, dur, frames, False)
        wave_data = (
            np.sin(2 * np.pi * freq * t)
            + 0.12 * np.sin(2 * np.pi * freq * 2 * t)
        )
        fade_len = max(int(sample_rate * min(dur * 0.35, 0.02)), 1)
        envelope = np.ones(frames, dtype=np.float64)
        fade_in = np.sin(np.linspace(0.0, np.pi / 2, fade_len)) ** 2
        fade_out = np.cos(np.linspace(0.0, np.pi / 2, fade_len)) ** 2
        envelope[:fade_len] = fade_in
        envelope[-fade_len:] = np.minimum(envelope[-fade_len:], fade_out)
        return wave_data * envelope * amp

    dry = np.concatenate([synth_note(freq, dur, amp) for freq, dur, amp in notes])
    delay_samples = max(int(sample_rate * echo_delay), 1)
    wet = np.pad(dry * echo_decay, (delay_samples, 0))
    dry = np.pad(dry, (0, max(len(wet) - len(dry), 0)))
    chime = dry + wet[: len(dry)]

    master_fade_len = max(int(sample_rate * 0.02), 1)
    master_envelope = np.ones(len(chime), dtype=np.float64)
    master_envelope[:master_fade_len] = np.sin(
        np.linspace(0.0, np.pi / 2, master_fade_len)
    ) ** 2
    master_envelope[-master_fade_len:] = np.minimum(
        master_envelope[-master_fade_len:],
        np.cos(np.linspace(0.0, np.pi / 2, master_fade_len)) ** 2,
    )
    chime = chime * master_envelope * 0.82
    return np.clip(chime, -0.8, 0.8).astype(np.float32)


def write_wav(path: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def main() -> int:
    out_dir = Path(__file__).resolve().parents[1] / "tmp" / "chimes"
    for kind in ("start", "end"):
        write_wav(out_dir / f"{kind}.wav", render_chime(kind))
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
