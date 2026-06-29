import asyncio
import os
import tempfile
import threading
from pathlib import Path

import edge_tts
import miniaudio
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

_client          = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_modelo_whisper: WhisperModel | None = None
_whisper_lock    = threading.Lock()


def _get_modelo() -> WhisperModel:
    global _modelo_whisper
    if _modelo_whisper is None:
        with _whisper_lock:
            if _modelo_whisper is None:
                print("[ia] cargando Faster-Whisper small...")
                _modelo_whisper = WhisperModel("small", device="cpu", compute_type="float32")
                print("[ia] modelo listo")
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


def consultar_gpt(texto: str) -> str:
    """Envía texto a GPT-4o-mini. Retorna respuesta en texto plano."""
    resp = _client.chat.completions.create(
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
        max_tokens=200,
    )
    return resp.choices[0].message.content.strip()


def hablar_edge(texto: str) -> None:
    """Sintetiza texto con Edge TTS y reproduce con sounddevice sin archivo permanente."""
    async def _guardar(path: str) -> None:
        await edge_tts.Communicate(texto, voice="es-MX-JorgeNeural").save(path)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name

    try:
        asyncio.run(_guardar(tmp_path))
        decoded = miniaudio.decode_file(
            tmp_path,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=24000,
        )
        samples = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
        sd.play(samples, decoded.sample_rate)
        sd.wait()
    finally:
        os.unlink(tmp_path)
