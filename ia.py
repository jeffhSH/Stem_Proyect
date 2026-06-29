import asyncio
import base64
import io
import json
import os
import queue as _stdlib_queue
import subprocess
import threading
import time
import webbrowser
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
_tts_ya_reproducido = threading.Event()

VOICE = "es-MX-JorgeNeural"

_SYSTEM_AGENTE = """\
Eres el agente de acción de Stem, asistente de escritorio en Windows 11.
El usuario te dará una instrucción en voz. Responde SOLO con JSON válido, sin texto extra:
{
  "descripcion": "descripción corta en español de lo que vas a hacer",
  "codigo": "código Python ejecutable en una sola línea"
}
Para abrir URLs usa siempre: subprocess.Popen([r'C:\\\\Program Files\\\\BraveSoftware\\\\Brave-Browser\\\\Application\\\\brave.exe', 'URL'])
Puedes usar: subprocess, webbrowser, pyautogui, os. No uses librerías externas.\
"""

_SYSTEM_CLASIFICAR = (
    "Responde solo 'accion' o 'conversacion' según si el usuario quiere "
    "ejecutar algo en el PC o solo hablar."
)

_MAX_INTENTOS_AGENTE = 3


def _ts() -> str:
    now = datetime.now()
    return f"[{now.strftime('%H:%M:%S')}.{now.microsecond // 1000:03d}]"


# ── Faster-Whisper ─────────────────────────────────────────────────────────────

def _get_modelo() -> WhisperModel:
    global _modelo_whisper
    if _modelo_whisper is None:
        with _whisper_lock:
            if _modelo_whisper is None:
                print(f"{_ts()}[ia] cargando Faster-Whisper base (int8)...")
                _modelo_whisper = WhisperModel(
                    "base",
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=6,
                    num_workers=1,
                )
                print(f"{_ts()}[ia] modelo listo")
    return _modelo_whisper


def precargar_whisper() -> None:
    """Lanza _get_modelo() en hilo daemon para evitar carga en frío (~8s) en primer uso."""
    threading.Thread(target=_get_modelo, daemon=True).start()


def transcribir_whisper(audio_bytes: bytes) -> str:
    """Transcribe audio raw int16 bytes con Faster-Whisper. Retorna el texto."""
    arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    arr = np.ascontiguousarray(arr)
    segments, _ = _get_modelo().transcribe(
        arr,
        language="es",
        beam_size=1,
        best_of=1,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=300, threshold=0.5),
        condition_on_previous_text=False,
        word_timestamps=False,
    )
    return " ".join(seg.text for seg in segments).strip()


# ── Audio helpers (compartidos por modo IA y modo agente) ──────────────────────

def _drenar_audio(audio_q: _stdlib_queue.Queue) -> None:
    while not audio_q.empty():
        try:
            audio_q.get_nowait()
        except _stdlib_queue.Empty:
            break


def _capturar_audio(audio_q: _stdlib_queue.Queue, rec: object, timeout: float = 8.0) -> bytes:
    """Captura audio hasta que Vosk detecta silencio final o timeout."""
    chunks: list[bytes] = []
    rec.Reset()
    t_fin = time.time() + timeout
    while time.time() < t_fin:
        try:
            data = audio_q.get(timeout=0.5)
        except _stdlib_queue.Empty:
            continue
        chunks.append(data)
        if rec.AcceptWaveform(data):
            if json.loads(rec.Result()).get("text", "").strip():
                break
    rec.Reset()
    return b"".join(chunks)


def _escuchar_confirmacion(audio_q: _stdlib_queue.Queue, rec: object) -> str:
    """
    Escucha 8 s con Vosk. Retorna 'si', 'no' o 'cancelar'.
    Timeout → 'cancelar'.
    """
    _drenar_audio(audio_q)
    rec.Reset()
    t_fin = time.time() + 8.0
    while time.time() < t_fin:
        try:
            data = audio_q.get(timeout=max(0.1, t_fin - time.time()))
        except _stdlib_queue.Empty:
            break
        if rec.AcceptWaveform(data):
            texto = json.loads(rec.Result()).get("text", "").lower().strip()
        else:
            texto = json.loads(rec.PartialResult()).get("partial", "").lower().strip()
        if not texto:
            continue
        print(f"{_ts()}[agente] confirmación: '{texto}'")
        palabras = texto.split()
        if any(w in ("cancelar",) for w in palabras):
            rec.Reset()
            return "cancelar"
        if any(w == "no" for w in palabras):
            rec.Reset()
            return "no"
        if any(w in ("sí", "si") for w in palabras):
            rec.Reset()
            return "si"
    print(f"{_ts()}[agente] timeout sin confirmación → cancelar")
    rec.Reset()
    return "cancelar"


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

    if buffer.strip():
        tts_q.put(buffer.strip())

    tts_q.put(None)
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


# ── Modo agente ────────────────────────────────────────────────────────────────

def _clasificar_intencion(texto: str) -> str:
    """Clasificación rápida vía GPT-4o-mini. Retorna 'accion' o 'conversacion'."""
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_CLASIFICAR},
                {"role": "user",   "content": texto},
            ],
            max_tokens=5,
        )
        resultado = resp.choices[0].message.content.strip().lower()
        return "accion" if "accion" in resultado else "conversacion"
    except Exception:
        return "conversacion"


def _build_exec_ns() -> dict:
    """Namespace para exec() con módulos seguros disponibles al código generado."""
    ns: dict = {"subprocess": subprocess, "os": os, "webbrowser": webbrowser}
    try:
        import pyautogui
        ns["pyautogui"] = pyautogui
    except ImportError:
        pass
    return ns


def _pedir_accion_gpt(texto: str) -> dict | None:
    """
    Llama GPT-4o-mini con _SYSTEM_AGENTE y parsea JSON.
    Retorna dict con 'descripcion' y 'codigo', o None si falla.
    """
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_AGENTE},
                {"role": "user",   "content": texto},
            ],
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        print(f"{_ts()}[agente] error GPT/JSON: {exc}")
        return None


def _corregir_con_vision(codigo_fallido: str) -> str | None:
    """
    Toma screenshot, lo comprime como JPEG base64 y llama GPT-4o-mini con visión
    para obtener código corregido. Retorna el nuevo código o None si falla.
    """
    try:
        import pyautogui
    except ImportError:
        print(f"{_ts()}[agente] pyautogui no disponible para screenshot")
        return None
    try:
        screenshot = pyautogui.screenshot()
        buf = io.BytesIO()
        screenshot.save(buf, format="JPEG", quality=70)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode()

        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_AGENTE},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Intenté: {codigo_fallido}\n"
                                "Falló. Analiza el screenshot y responde JSON con el código corregido."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}",
                                "detail": "low",
                            },
                        },
                    ],
                },
            ],
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content)
        return parsed.get("codigo")
    except Exception as exc:
        print(f"{_ts()}[agente] vision error: {exc}")
        return None


def activar_modo_agente(audio_q: _stdlib_queue.Queue, rec: object) -> None:
    """
    Modo agente: captura instrucción, genera código con GPT, pide confirmación,
    ejecuta y verifica. Con visión para corrección si falla.
    """
    _drenar_audio(audio_q)
    mostrar_prompt = True

    while True:
        if mostrar_prompt:
            _reproducir_oracion("Di la acción.")
            mostrar_prompt = False

        print(f"{_ts()}[agente] esperando instrucción...")
        audio_bytes = _capturar_audio(audio_q, rec)
        if not audio_bytes:
            _reproducir_oracion("No te escuché.")
            return

        print(f"{_ts()}[agente] transcribiendo...")
        texto = transcribir_whisper(audio_bytes)
        if not texto:
            _reproducir_oracion("No te escuché, intenta de nuevo.")
            mostrar_prompt = True
            continue

        print(f"{_ts()}[agente] texto: '{texto}'")
        print(f"{_ts()}[agente] generando código...")
        parsed = _pedir_accion_gpt(texto)

        if parsed is None:
            _reproducir_oracion("No entendí, intenta de nuevo.")
            mostrar_prompt = True
            continue

        descripcion = parsed.get("descripcion", "algo")
        codigo      = parsed.get("codigo", "").strip()

        if not codigo:
            _reproducir_oracion("No pude generar el código, intenta de nuevo.")
            mostrar_prompt = True
            continue

        print(f"{_ts()}[agente] plan: {descripcion} | código: {codigo}")
        _reproducir_oracion(f"Voy a {descripcion}. ¿Procedo?")
        confirmacion = _escuchar_confirmacion(audio_q, rec)

        if confirmacion == "cancelar":
            _reproducir_oracion("Cancelado.")
            return

        if confirmacion == "no":
            _reproducir_oracion("Di la acción de nuevo.")
            mostrar_prompt = True
            continue

        # "sí" → ejecutar
        print(f"{_ts()}[agente] ejecutando: {codigo}")
        try:
            exec(codigo, _build_exec_ns())  # noqa: S102
        except Exception as exc:
            print(f"{_ts()}[agente] exec error: {exc}")
            _reproducir_oracion("Hubo un error al ejecutar.")

        # Loop de verificación con hasta _MAX_INTENTOS_AGENTE correcciones
        for intento in range(_MAX_INTENTOS_AGENTE):
            _reproducir_oracion("¿Salió bien?")
            verificacion = _escuchar_confirmacion(audio_q, rec)

            if verificacion in ("si", "cancelar"):
                return

            # "no" → corrección con visión
            print(f"{_ts()}[agente] fallo reportado, corrección {intento + 1}/{_MAX_INTENTOS_AGENTE}...")
            nuevo_codigo = _corregir_con_vision(codigo)

            if not nuevo_codigo:
                break

            codigo = nuevo_codigo
            print(f"{_ts()}[agente] ejecutando corrección: {codigo}")
            try:
                exec(codigo, _build_exec_ns())  # noqa: S102
            except Exception as exc:
                print(f"{_ts()}[agente] exec error en corrección: {exc}")
                _reproducir_oracion("Hubo un error al ejecutar.")

        _reproducir_oracion("No pude corregirlo.")
        return
