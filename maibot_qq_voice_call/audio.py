"""PulseAudio capture and lightweight adaptive voice activity detection."""

from __future__ import annotations

import array
import asyncio
import contextlib
import io
import math
import os
import wave
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol

from .config import AudioSection
from .models import CallUtterance, RuntimeStatus


class StreamingASR(Protocol):
    async def begin(self, frames: list[bytes]) -> bool: ...

    async def append(self, frame: bytes) -> bool: ...

    async def commit(self) -> asyncio.Future[tuple[str, float]] | None: ...

    async def abort_buffer(self) -> None: ...


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def frame_dbfs(frame: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(frame)
    if not samples:
        return -100.0
    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / len(samples))
    if rms <= 0:
        return -100.0
    return 20.0 * math.log10(rms / 32768.0)


class AudioSegmenter:
    def __init__(
        self,
        config: AudioSection,
        status: RuntimeStatus,
        logger,
        *,
        on_speech_started: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.config = config
        self.status = status
        self.logger = logger
        self.process: asyncio.subprocess.Process | None = None
        self.streaming_asr: StreamingASR | None = None
        self.on_speech_started = on_speech_started
        self.frame_bytes = config.sample_rate * config.frame_ms // 1000 * 2

    async def close(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=3)
        if process.returncode is None:
            process.kill()
            await process.wait()

    async def _start_process(self) -> asyncio.subprocess.Process:
        process_env = os.environ.copy()
        if self.config.pulse_server:
            process_env["PULSE_SERVER"] = self.config.pulse_server
        process = await asyncio.create_subprocess_exec(
            "parec",
            "--raw",
            f"--device={self.config.capture_device}",
            "--format=s16le",
            f"--rate={self.config.sample_rate}",
            "--channels=1",
            "--client-name=maibot-qq-call-vad",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
        )
        self.process = process
        return process

    @staticmethod
    def _consume_transcript(future: asyncio.Future[tuple[str, float]]) -> None:
        if future.cancelled():
            return
        with contextlib.suppress(Exception):
            future.exception()

    async def run(
        self,
        active: asyncio.Event,
        output_queue: asyncio.Queue[CallUtterance],
        stop_event: asyncio.Event,
    ) -> None:
        pre_roll: deque[bytes] = deque(maxlen=10)
        recent_voice: deque[bool] = deque(maxlen=5)
        recording_frames: list[bytes] = []
        speech_frames = 0
        silent_frames = 0
        noise_floor = -60.0
        streaming_asr_active = False
        barge_in_notified = False
        barge_in_candidate = False
        max_frames = max(1, round(15_000 / self.config.frame_ms))

        while not stop_event.is_set():
            process = await self._start_process()
            assert process.stdout is not None
            try:
                while not stop_event.is_set():
                    frame = await process.stdout.readexactly(self.frame_bytes)
                    dbfs = frame_dbfs(frame)

                    if not active.is_set():
                        if streaming_asr_active and self.streaming_asr is not None:
                            await self.streaming_asr.abort_buffer()
                        pre_roll.clear()
                        recent_voice.clear()
                        recording_frames.clear()
                        speech_frames = 0
                        silent_frames = 0
                        streaming_asr_active = False
                        barge_in_notified = False
                        barge_in_candidate = False
                        self.status.recording = False
                        continue

                    threshold = max(-48.0, min(-34.0, noise_floor + 10.0))
                    voiced = dbfs >= threshold
                    if not voiced and dbfs < -32.0:
                        noise_floor = noise_floor * 0.98 + dbfs * 0.02

                    if not recording_frames:
                        pre_roll.append(frame)
                        recent_voice.append(voiced)
                        if len(recent_voice) == recent_voice.maxlen and sum(recent_voice) >= 3:
                            recording_frames = list(pre_roll)
                            speech_frames = sum(recent_voice)
                            silent_frames = 0
                            self.status.recording = True
                            if self.streaming_asr is not None:
                                streaming_asr_active = await self.streaming_asr.begin(
                                    recording_frames
                                )
                        continue

                    recording_frames.append(frame)
                    if streaming_asr_active and self.streaming_asr is not None:
                        streaming_asr_active = await self.streaming_asr.append(frame)
                    if voiced:
                        speech_frames += 1
                        silent_frames = 0
                    else:
                        silent_frames += 1

                    if (
                        not barge_in_notified
                        and speech_frames >= self.config.barge_in_speech_frames
                        and self.on_speech_started is not None
                    ):
                        barge_in_notified = True
                        try:
                            barge_in_candidate = bool(await self.on_speech_started())
                        except Exception as exc:
                            self.logger.debug("检查 TTS 插话候选失败: %s", exc)

                    reached_silence = (
                        silent_frames >= self.config.end_of_speech_frames
                    )
                    reached_limit = len(recording_frames) >= max_frames
                    if not reached_silence and not reached_limit:
                        continue

                    utterance_seconds = (
                        len(recording_frames) * self.config.frame_ms / 1000
                    )
                    speech_seconds = speech_frames * self.config.frame_ms / 1000
                    realtime_transcript = None
                    if streaming_asr_active and self.streaming_asr is not None:
                        realtime_transcript = await self.streaming_asr.commit()
                    if (
                        utterance_seconds >= self.config.min_utterance_seconds
                        and speech_seconds >= self.config.min_speech_seconds
                    ):
                        wav_bytes = pcm_to_wav(
                            b"".join(recording_frames),
                            self.config.sample_rate,
                        )
                        if output_queue.full():
                            with contextlib.suppress(asyncio.QueueEmpty):
                                dropped = output_queue.get_nowait()
                                if (
                                    dropped.realtime_transcript is not None
                                    and not dropped.realtime_transcript.done()
                                ):
                                    dropped.realtime_transcript.cancel()
                        output_queue.put_nowait(
                            CallUtterance(
                                wav_bytes=wav_bytes,
                                realtime_transcript=realtime_transcript,
                                barge_in_candidate=barge_in_candidate,
                            )
                        )
                        self.status.queue_size = output_queue.qsize()
                        self.logger.info(
                            "检测到通话语音 %.2f 秒（有效语音 %.2f 秒）",
                            utterance_seconds,
                            speech_seconds,
                        )
                    elif realtime_transcript is not None:
                        realtime_transcript.add_done_callback(self._consume_transcript)

                    pre_roll.clear()
                    recent_voice.clear()
                    recording_frames.clear()
                    speech_frames = 0
                    silent_frames = 0
                    streaming_asr_active = False
                    barge_in_notified = False
                    barge_in_candidate = False
                    self.status.recording = False
            except asyncio.CancelledError:
                raise
            except asyncio.IncompleteReadError:
                stderr = b""
                if process.stderr is not None:
                    stderr = await process.stderr.read()
                self.logger.warning(
                    "parec 已退出，将重启: %s",
                    stderr.decode(errors="replace").strip(),
                )
                await asyncio.sleep(1)
            finally:
                await self.close()
