import asyncio
import os
import queue as _stdlib_queue
import threading
from datetime import datetime
from pathlib import Path

import edge_tts
import miniaudio
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

_client             = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_modelo_whisper: WhisperModel | None = None
_whisper_lock       = threading.Lock()
_SENTENCE_END       = frozenset(".?!")
_tts_ya_reproducido = threading.Event()  # set por consultar_gpt; check por hablar_edge

VOICE = "es-MX-JorgeNeural"


def _ts() -> str:
    now = datetime.now()
    return f"[{now.strftime('%H:%M:%S')}.{now.microsecond // 1000:03d}]"


# ── Faster-Whisper ─────────────────────────────────────────────────────────────

def _get_modelo() -> WhisperModel:
    global _modelo_whisper
    if _modelo_whisper is None:
        with _whisper_lock:
            if _modelo_whisper is None:
                print(f"{_ts()}[ia] cargando Faster-Whisper small (int8)...")
                _modelo_whisper = WhisperModel("small", device="cpu", compute_type="int8")
                print(f"{_ts()}[ia] modelo listo")
    return _modelo_whisper


def transcribir_whisper(audio_bytes: bytes) -> str:
    """Transcribe audio raw int16 bytes con Faster-Whisper. Retorna el texto."""
    arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = _get_modelo().transcribe(
        arr,
        language="es",
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    return " ".join(seg.text for seg in segments).strip()


# ── Edge TTS streaming ─────────────────────────────────────────────────────────

async def _tts_bytes_async(texto: str) -> bytes:
    """Recolecta bytes de audio MP3 desde el stream de Edge TTS."""
    chunks: list[bytes] = []
    async for chunk in edge_tts.Communicate(texto, voice=VOICE).stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def _reproducir_oracion(texto: str) -> None:
    """Sintetiza una oración con Edge TTS streaming y la reproduce bloqueando hasta el fin."""
    print(f"{_ts()}[ia] oración → TTS: '{texto}'")
    try:
        audio_bytes = asyncio.run(_tts_bytes_async(texto))
        if not audio_bytes:
            return
        decoded = miniaudio.decode(
            audio_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=24000,
        )
        samples = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
        print(f"{_ts()}[ia] TTS reproduciendo...")
        sd.play(samples, decoded.sample_rate)
        sd.wait()
        print(f"{_ts()}[ia] fin TTS oración")
    except Exception as exc:
        print(f"{_ts()}[ia] TTS error en oración: {exc}")


def _tts_worker(q: _stdlib_queue.Queue) -> None:
    """Consume oraciones de la cola y las reproduce secuencialmente."""
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            return
        _reproducir_oracion(item)
        q.task_done()


def _flush_oraciones(buffer: str, tts_q: _stdlib_queue.Queue) -> str:
    """Extrae todas las oraciones completas del buffer y las encola para TTS."""
    while True:
        for i, ch in enumerate(buffer):
            if ch in _SENTENCE_END and i > 0:
                oracion = buffer[: i + 1].strip()
                buffer  = buffer[i + 1 :].lstrip()
                if oracion:
                    tts_q.put(oracion)
                break
        else:
            break
    return buffer


# ── Pipeline GPT → TTS ────────────────────────────────────────────────────────

def consultar_gpt(texto: str) -> str:
    """
    Streams GPT-4o-mini y envía cada oración completa al TTS en cuanto se detecta,
    sin esperar que GPT termine. Retorna el texto completo una vez que el TTS finalizó.
    """
    stream = _client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres Stem, asistente de escritorio por voz en Windows. "
                    "Responde en español, de forma breve y directa, en máximo 2 oraciones."
                ),
            },
            {"role": "user", "content": texto},
        ],
        max_tokens=80,
        stream=True,
    )

    tts_q: _stdlib_queue.Queue = _stdlib_queue.Queue()
    worker = threading.Thread(target=_tts_worker, args=(tts_q,), daemon=True)
    worker.start()

    buffer    = ""
    full_text = ""

    for chunk in stream:
        token      = chunk.choices[0].delta.content or ""
        buffer    += token
        full_text += token
        buffer     = _flush_oraciones(buffer, tts_q)

    # Texto restante sin puntuación final
    if buffer.strip():
        tts_q.put(buffer.strip())

    tts_q.put(None)   # señal de fin al worker
    tts_q.join()
    worker.join()

    _tts_ya_reproducido.set()
    return full_text.strip()


def hablar_edge(texto: str) -> None:
    """
    Sintetiza y reproduce texto con Edge TTS streaming.
    No-op si consultar_gpt() ya reprodujo el audio en esta vuelta.
    """
    if _tts_ya_reproducido.is_set():
        _tts_ya_reproducido.clear()
        return

    # Llamado directo sin consultar_gpt previo
    print(f"{_ts()}[ia] inicio TTS (sintetizando...)")
    try:
        audio_bytes = asyncio.run(_tts_bytes_async(texto))
        decoded = miniaudio.decode(
            audio_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=24000,
        )
        samples = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
        print(f"{_ts()}[ia] TTS reproduciendo...")
        sd.play(samples, decoded.sample_rate)
        sd.wait()
        print(f"{_ts()}[ia] fin TTS")
    except Exception as exc:
        print(f"{_ts()}[ia] TTS error: {exc}")
