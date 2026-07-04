import asyncio
import queue as _stdlib_queue
import threading

import edge_tts
import miniaudio
import numpy as np
import sounddevice as sd

import ia_state
from ia_state import (
    VOICE,
    _CARTESIA_MODEL,
    _CARTESIA_VOICE_ID,
    _client,
    _interrumpir_tts,
    _SENTENCE_END,
    _ts,
    _tts_reproduciendo,
    _tts_ya_reproducido,
)


# ── Edge TTS streaming ─────────────────────────────────────────────────────────

async def _tts_bytes_async(texto: str) -> bytes:
    """Recolecta bytes de audio MP3 desde el stream de Edge TTS."""
    chunks: list[bytes] = []
    async for chunk in edge_tts.Communicate(texto, voice=VOICE).stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def _reproducir_audio(samples: np.ndarray, sample_rate: int) -> None:
    """Reproducción interrompible vía barge-in. `_interrumpir_tts` activo → sd.stop() inmediato."""
    _interrumpir_tts.clear()
    _tts_reproduciendo.set()
    try:
        sd.play(samples, sample_rate)
        while True:
            if _interrumpir_tts.wait(timeout=0.05):
                sd.stop()
                print(f"{_ts()}[ia] [INTERRUPCIÓN] audio cortado por barge-in")
                return
            try:
                activo = sd.get_stream().active
            except Exception:
                break
            if not activo:
                break
    finally:
        _tts_reproduciendo.clear()


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
        _reproducir_audio(samples, decoded.sample_rate)
        print(f"{_ts()}[ia] fin TTS oración")
    except Exception as exc:
        print(f"{_ts()}[ia] TTS error en oración: {exc}")


def _hablar_stem(texto: str) -> None:
    """TTS para el Orchestrator: Cartesia (Mateo) si disponible, Edge TTS si no."""
    if ia_state._cartesia_client is not None:
        _hablar_cartesia(texto)
    else:
        _hablar_edge_original(texto)


def _tts_worker(q: _stdlib_queue.Queue) -> None:
    """Consume oraciones de la cola y las reproduce secuencialmente."""
    while True:
        item = q.get()
        if item is None:
            q.task_done()
            return
        if _interrumpir_tts.is_set():
            print(f"{_ts()}[ia] [INTERRUPCIÓN] oración descartada por barge-in")
            q.task_done()
        else:
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

    if buffer.strip():
        tts_q.put(buffer.strip())

    tts_q.put(None)
    tts_q.join()
    worker.join()

    _tts_ya_reproducido.set()
    return full_text.strip()


def _hablar_edge_original(texto: str) -> None:
    """Sintetiza y reproduce texto con Edge TTS. Fallback cuando Cartesia no está disponible."""
    print(f"{_ts()}[ia] inicio TTS Edge (sintetizando...)")
    try:
        audio_bytes = asyncio.run(_tts_bytes_async(texto))
        decoded = miniaudio.decode(
            audio_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=24000,
        )
        samples = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
        print(f"{_ts()}[ia] TTS Edge reproduciendo...")
        _reproducir_audio(samples, decoded.sample_rate)
        print(f"{_ts()}[ia] fin TTS Edge")
    except Exception as exc:
        print(f"{_ts()}[ia] TTS Edge error: {exc}")


def _hablar_cartesia(texto: str) -> None:
    """Sintetiza y reproduce texto con Cartesia (voz Mateo). Si la API key activa falla
    (cuota agotada, rate limit, etc.), rota a la siguiente key configurada en
    CARTESIA_API_KEYS_BACKUP y reintenta. Si se agotan todas, cae a Edge TTS."""
    intentos = max(len(ia_state._CARTESIA_KEYS), 1)
    for _ in range(intentos):
        cliente = ia_state._cartesia_client
        if cliente is None:
            break
        print(f"{_ts()}[ia] inicio TTS Cartesia (key #{ia_state._cartesia_key_index + 1}/{len(ia_state._CARTESIA_KEYS)}, sintetizando...)")
        try:
            audio_bytes = b"".join(
                cliente.tts.bytes(
                    model_id=_CARTESIA_MODEL,
                    transcript=texto,
                    voice={"mode": "id", "id": _CARTESIA_VOICE_ID},
                    language="es",
                    output_format={
                        "container": "wav",
                        "encoding": "pcm_s16le",
                        "sample_rate": 44100,
                    },
                )
            )
            decoded = miniaudio.decode(
                audio_bytes,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=44100,
            )
            samples = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
            print(f"{_ts()}[ia] TTS Cartesia reproduciendo...")
            _reproducir_audio(samples, decoded.sample_rate)
            print(f"{_ts()}[ia] fin TTS Cartesia")
            return
        except Exception as exc:
            print(f"{_ts()}[ia] error TTS Cartesia (key #{ia_state._cartesia_key_index + 1}): {exc} — probando siguiente key")
            if ia_state._rotar_cartesia_client() is None:
                break
    print(f"{_ts()}[ia] Cartesia agotado (todas las keys fallaron) — fallback a Edge TTS")
    _hablar_edge_original(texto)


def hablar_edge(texto: str) -> None:
    """
    Punto de entrada TTS principal. Usa Cartesia (Mateo) si CARTESIA_API_KEY está en .env,
    Edge TTS (Jorge) como fallback. No-op si el audio ya fue reproducido en este turno.
    """
    if _tts_ya_reproducido.is_set():
        _tts_ya_reproducido.clear()
        return

    if ia_state._cartesia_client is not None:
        _hablar_cartesia(texto)
    else:
        _hablar_edge_original(texto)
    print(f"{_ts()}[diag] TTS fin")
