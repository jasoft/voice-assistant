from __future__ import annotations

import sys
import time
from threading import Lock
from typing import Any
from ..utils.logging import log, log_timing
from ..events import GuiEventWriter
from ..models.config import Config

def audio_visual_level(rms: float, threshold: float) -> float:
    floor = max(threshold * 0.55, 0.002)
    ceiling = max(threshold * 3.2, floor * 4.0)
    if rms <= floor:
        return 0.0
    level = (rms - floor) / (ceiling - floor)
    return max(0.0, min(level, 1.0)) ** 0.72

def open_input_stream_with_retry(
    *,
    stream_factory: Any,
    samplerate: int,
    channels: int,
    dtype: str,
    callback: Any,
    max_attempts: int = 2,
    retry_delay_seconds: float = 0.12,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return stream_factory(
                samplerate=samplerate,
                channels=channels,
                dtype=dtype,
                callback=callback,
            )
        except Exception as exc:
            last_error = exc
            message = str(exc)
            is_retryable = "Internal PortAudio error [PaErrorCode -9986]" in message
            if not is_retryable or attempt >= max_attempts:
                raise
            log(
                f"input stream open failed attempt={attempt}/{max_attempts}: {message}; retrying"
            )
            time.sleep(retry_delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("failed to open input stream")

class VisualRecorder:
    SPEECH_RELEASE_HOLD_SECONDS = 0.35

    def __init__(self, cfg: Config, events: GuiEventWriter | None = None) -> None:
        init_start = time.perf_counter()
        import numpy as np
        import sounddevice as sd

        self.np = np
        self.sd = sd
        self.keyboard = None
        self.Live = None
        self.Table = None
        self.cfg = cfg
        self.events = events or GuiEventWriter(enabled=False)
        self.frames: list[Any] = []
        self.total_samples = 0
        self.silent_samples = 0
        self.silence_target = int(cfg.silence_seconds * cfg.sample_rate)
        self.speech_release_hold_target = max(
            1, int(self.SPEECH_RELEASE_HOLD_SECONDS * cfg.sample_rate)
        )
        self.speech_release_hold_remaining = 0
        self.no_speech_timeout_target = int(
            cfg.no_speech_timeout_seconds * cfg.sample_rate
        )
        self.calibration_target = int(cfg.calibration_seconds * cfg.sample_rate)
        self.calibration_rms: list[float] = []
        self.last_rms = 0.0
        self.ema_rms = 0.0
        self.is_speech_active = False
        self.speech_started = False
        self.effective_threshold = cfg.threshold
        self.should_stop = False
        self.audio_level_peak = 0.0
        self.audio_level_sum = 0.0
        self.audio_level_count = 0
        self.last_audio_status_text = ""
        self.last_diagnostic_key = ""
        self.lock = Lock()
        log(
            "timing %8.1fms | recorder init ready (imports+state)"
            % ((time.perf_counter() - init_start) * 1000.0)
        )

    def _refresh_thresholds(self) -> None:
        ambient_rms = 0.0
        if self.calibration_rms:
            ambient_rms = float(self.np.percentile(self.np.array(self.calibration_rms), 80))
        # Increase threshold slightly to be less sensitive to background hum
        self.effective_threshold = max(self.cfg.threshold, ambient_rms * 3.2 + 0.005)

    def _emit_diagnostic(self, key: str, level: str, message: str) -> None:
        with self.lock:
            if key == self.last_diagnostic_key:
                return
            self.last_diagnostic_key = key
        self.events.emit("diagnostic", level=level, message=message)

    def on_press(self, key: Any) -> Any:
        if self.keyboard is not None and key == self.keyboard.Key.enter:
            self.should_stop = True
            return False  # Stop listener

    def _callback(self, indata: Any, frames: int, _time_info, status) -> None:
        if status:
            status_text = str(status)
            with self.lock:
                is_new_status = status_text != self.last_audio_status_text
                if is_new_status:
                    self.last_audio_status_text = status_text
            if is_new_status:
                log(f"audio status: {status_text}")
                self._emit_diagnostic(
                    f"audio-status:{status_text}",
                    "warning",
                    f"麦克风状态异常：{status_text}",
                )
        chunk = indata.copy()
        rms = float(self.np.sqrt(self.np.mean(self.np.square(chunk), dtype=self.np.float64)))
        should_stop_stream = False
        audio_level = 0.0
        is_speech = False
        timeout_progress = 0.0
        pending_diagnostic: tuple[str, str, str] | None = None
        with self.lock:
            self.frames.append(chunk)
            self.total_samples += frames
            self.last_rms = rms

            if self.ema_rms == 0.0:
                self.ema_rms = rms
            else:
                self.ema_rms = self.ema_rms * 0.7 + rms * 0.3

            if (
                not self.speech_started
                and self.total_samples <= self.calibration_target
            ):
                self.calibration_rms.append(rms)
                self._refresh_thresholds()

            is_speech = self.ema_rms >= self.effective_threshold
            audio_level = audio_visual_level(self.ema_rms, self.effective_threshold)
            self.audio_level_peak = max(self.audio_level_peak, audio_level)
            self.audio_level_sum += audio_level
            self.audio_level_count += 1

            if not self.speech_started:
                if is_speech:
                    self.speech_started = True
                    self.silent_samples = 0
                    self.speech_release_hold_remaining = self.speech_release_hold_target
                    self.is_speech_active = True
                    pending_diagnostic = (
                        "speech-started",
                        "success",
                        "已检测到声音，正在录音",
                    )
                elif self.total_samples >= self.no_speech_timeout_target:
                    pending_diagnostic = (
                        "no-speech-timeout",
                        "warning",
                        "未检测到语音，请检查麦克风或提高说话音量",
                    )
                    timeout_progress = 1.0
                    should_stop_stream = True
                else:
                    timeout_progress = min(
                        self.total_samples / max(self.no_speech_timeout_target, 1),
                        1.0,
                    )
                    self.is_speech_active = False
            else:
                if is_speech:
                    self.silent_samples = 0
                    self.speech_release_hold_remaining = self.speech_release_hold_target
                    self.is_speech_active = True
                    timeout_progress = 0.0
                else:
                    if self.speech_release_hold_remaining > 0:
                        self.speech_release_hold_remaining = max(
                            0,
                            self.speech_release_hold_remaining - frames,
                        )
                        self.is_speech_active = True
                        timeout_progress = 0.0
                    else:
                        self.is_speech_active = False
                        self.silent_samples += frames
                        timeout_progress = min(
                            self.silent_samples / max(self.silence_target, 1),
                            1.0,
                        )
                if self.silent_samples >= self.silence_target:
                    timeout_progress = 1.0
                    should_stop_stream = True

            if self.should_stop:
                should_stop_stream = True

        if pending_diagnostic is not None:
            self._emit_diagnostic(*pending_diagnostic)
        self.events.emit(
            "audio_level",
            level=audio_level,
            rms=rms,
            speaking=self.is_speech_active,
            timeout_progress=timeout_progress,
        )
        if should_stop_stream:
            raise self.sd.CallbackStop()

    def get_ui(self) -> Any:
        if self.Table is None:
            return "Loading recorder UI..."
        with self.lock:
            rms = self.last_rms
            ema_rms = self.ema_rms
            silence_ratio = self.silent_samples / self.cfg.sample_rate
            elapsed = self.total_samples / self.cfg.sample_rate
            started = self.speech_started
            is_speech_active = self.is_speech_active
            timeout_left = max(
                self.cfg.no_speech_timeout_seconds - elapsed,
                0.0,
            )

        table = self.Table.grid()
        table.add_column(width=15)
        table.add_column()
        table.add_column(width=20, justify="right")

        # Status text
        if self.should_stop:
            status = "[bold red]STOPPING[/bold red]"
        elif started:
            status = "[bold green]RECORDING[/bold green]"
        else:
            status = "[bold yellow]WAITING FOR SPEECH[/bold yellow]"

        detail = ""
        if started and silence_ratio > 0:
            detail = f"[dim]Silence {silence_ratio:.1f}s[/dim]"
        elif not started:
            detail = f"[dim]Timeout in {timeout_left:.1f}s[/dim]"

        # Bar color
        if started:
            bar_color = "green"
        elif is_speech_active:
            bar_color = "cyan"
        elif ema_rms >= self.effective_threshold:
            bar_color = "green"
        else:
            bar_color = "blue"

        # Manual Scale RMS for progress bar (0.0 to 0.15)
        progress = min(rms / 0.15, 1.0) * 100

        table.add_row(
            " [bold]Audio Input[/bold]",
            f"[{bar_color}]{'#' * int(progress / 3.3)}[/{bar_color}]",
            "",
        )
        table.add_row(
            " [bold]Elapsed[/bold]", f"[dim]{elapsed:.1f}s captured[/dim]", status
        )
        table.add_row(" [dim]State[/dim]", detail, "")
        table.add_row(
            " [dim]Manual Stop[/dim]",
            "[italic cyan]Press ENTER to finish recording manually[/italic cyan]",
            "",
        )
        return table

    def get_plain_ui(self) -> str:
        with self.lock:
            rms = self.last_rms
            ema_rms = self.ema_rms
            silence_ratio = self.silent_samples / self.cfg.sample_rate
            elapsed = self.total_samples / self.cfg.sample_rate
            started = self.speech_started
            is_speech_active = self.is_speech_active
            timeout_left = max(
                self.cfg.no_speech_timeout_seconds - elapsed,
                0.0,
            )

        if self.should_stop:
            status = "STOPPING"
        elif started:
            status = "RECORDING"
        else:
            status = "WAITING FOR SPEECH"

        if started and silence_ratio > 0:
            detail = f"Silence {silence_ratio:.1f}s"
        elif not started:
            detail = f"Timeout in {timeout_left:.1f}s"
        else:
            detail = ""

        progress = min(rms / 0.15, 1.0)
        bar = "#" * max(1, int(progress * 20)) if rms > 0 else ""
        bar_color = "green" if started else ("cyan" if is_speech_active else "blue")
        return "\n".join(
            [
                f"Audio Input: [{bar_color}] {bar}",
                f"Elapsed: {elapsed:.1f}s captured | {status}",
                f"State: {detail}" if detail else "State:",
                "Manual Stop: Press ENTER to finish recording manually",
                f"RMS: {rms:.4f} | EMA: {ema_rms:.4f}",
            ]
        )

    def get_audio_level_stats(self) -> tuple[float, float]:
        with self.lock:
            peak_level = self.audio_level_peak
            count = self.audio_level_count
            mean_level = self.audio_level_sum / count if count else 0.0
        return peak_level, mean_level

    def record(self) -> Any:
        log("ptt-flow: Recording session started")
        log_timing("record() entered")
        self.events.emit("status", phase="recording")
        self._emit_diagnostic(
            "recording-started",
            "info",
            "麦克风已打开，正在等待你的声音",
        )
        listener = None

        try:
            with open_input_stream_with_retry(
                stream_factory=self.sd.InputStream,
                samplerate=self.cfg.sample_rate,
                channels=self.cfg.channels,
                dtype="float32",
                callback=self._callback,
            ) as stream:
                log_timing("input stream opened")
                ui_import_start = time.perf_counter()
                from pynput import keyboard
                from rich.live import Live
                from rich.table import Table

                self.keyboard = keyboard
                self.Live = Live
                self.Table = Table
                log(
                    "timing %8.1fms | recorder ui imports ready"
                    % ((time.perf_counter() - ui_import_start) * 1000.0)
                )

                listener = self.keyboard.Listener(on_press=self.on_press)
                listener.start()
                log_timing("keyboard listener started")

                use_rich_live = sys.stdout.isatty() and sys.stderr.isatty()
                if use_rich_live:
                    with self.Live(self.get_ui(), refresh_per_second=20, transient=True) as live:
                        log_timing("rich live UI entered")
                        while stream.active and not self.should_stop:
                            live.update(self.get_ui())
                            time.sleep(0.05)
                else:
                    log("tty not available; using plain text recorder UI for Raycast")
                    last_snapshot = ""
                    while stream.active and not self.should_stop:
                        if not self.events.enabled:
                            snapshot = self.get_plain_ui()
                            if snapshot != last_snapshot:
                                print(snapshot, flush=True)
                                last_snapshot = snapshot
                        time.sleep(0.25)
        except Exception as e:
            log(f"recording error: {e}", level="error")
        finally:
            if listener is not None and listener.running:
                listener.stop()

        with self.lock:
            if not self.frames:
                raise RuntimeError("no audio captured")
            if not self.speech_started:
                return None
            import numpy as np
            audio = np.concatenate(self.frames, axis=0)
        log(
            f"recording finished: {audio.shape[0] / self.cfg.sample_rate:.1f}s captured"
        )
        return audio
