import json
import queue as _stdlib_queue
import re
import threading
import time

import numpy as np
from faster_whisper import WhisperModel

from ia_state import _cancelado, _cancelar, _modelo_whisper, _ts, _whisper_lock

_PALABRAS_SI = {"sí", "si", "dale", "ok", "procede", "adelante", "perfecto", "correcto", "listo", "va", "claro"}
_PALABRAS_NO = {"no", "cancela", "cancelar", "para", "detén", "detente", "stop", "olvídalo", "olvida"}


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
    arr = np.clip(arr * 2.5, -1.0, 1.0)  # amplificación 150%
    arr = np.ascontiguousarray(arr)
    segments, _ = _get_modelo().transcribe(
        arr,
        language="es",
        beam_size=1,
        best_of=1,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=300, threshold=0.3),
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
    """Captura audio esperando 900ms de silencio sostenido tras inicio de habla, o timeout absoluto."""
    SILENCIO_CORTE = 0.9
    chunks: list[bytes] = []
    rec.Reset()
    t_fin = time.time() + timeout
    hablando = False
    t_ultimo_texto = 0.0

    while time.time() < t_fin:
        try:
            data = audio_q.get(timeout=0.1)
        except _stdlib_queue.Empty:
            if hablando and (time.time() - t_ultimo_texto) >= SILENCIO_CORTE:
                break
            continue
        chunks.append(data)
        if rec.AcceptWaveform(data):
            result_text = json.loads(rec.Result()).get("text", "").strip()
            if result_text:
                hablando = True
                t_ultimo_texto = time.time()
        else:
            partial = json.loads(rec.PartialResult()).get("partial", "").strip()
            if partial:
                hablando = True
                t_ultimo_texto = time.time()
        if hablando and (time.time() - t_ultimo_texto) >= SILENCIO_CORTE:
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
        if _cancelado():
            rec.Reset()
            return "cancelar"
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
        palabras = set(_normalizar_respuesta(texto).split())
        if palabras & _PALABRAS_NO:
            rec.Reset()
            return "cancelar"
        if palabras & _PALABRAS_SI:
            rec.Reset()
            return "si"
    print(f"{_ts()}[agente] timeout sin confirmación → cancelar")
    rec.Reset()
    return "cancelar"


def _escuchar_confirmacion_debug() -> str:
    try:
        resp = input("[DEBUG] ¿Procedo? (s/n/esc): ").strip().lower()
        if resp in ("s", "si", "sí", ""):
            return "si"
        if resp == "esc":
            _cancelar.set()
            return "cancelar"
        return "no"
    except (EOFError, KeyboardInterrupt):
        _cancelar.set()
        return "cancelar"


def _capturar_y_transcribir(audio_q: _stdlib_queue.Queue, rec: object) -> str:
    """Captura audio y transcribe con Faster-Whisper. Devuelve '' en silencio/error."""
    from voz import _capturar_audio_ia  # noqa: PLC0415
    audio_bytes = _capturar_audio_ia(audio_q, rec)
    if not audio_bytes:
        return ""
    try:
        print(f"{_ts()}[diag] audio entregado a Whisper ({len(audio_bytes)} bytes)")
        resultado = transcribir_whisper(audio_bytes)
        print(f"{_ts()}[diag] Whisper devolvió: '{resultado}'")
        return resultado
    except Exception:
        return ""


def _transcribir_respuesta(audio_q: _stdlib_queue.Queue, rec: object) -> str:
    """Captura audio libre y lo transcribe con Whisper. Devuelve texto vacío en silencio/error."""
    _drenar_audio(audio_q)
    audio_bytes = _capturar_audio(audio_q, rec, timeout=8.0)
    if not audio_bytes:
        return ""
    try:
        return transcribir_whisper(audio_bytes)
    except Exception:
        return ""


def _normalizar_respuesta(texto: str) -> str:
    """NFKD + elimina puntuación + lowercase. Permite comparar sin acentos ni signos."""
    import unicodedata
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w\s]", "", sin_acentos).lower().strip()


def _es_confirmacion(texto: str) -> bool:
    palabras = set(_normalizar_respuesta(texto).split())
    return bool(palabras & _PALABRAS_SI) and not bool(palabras & _PALABRAS_NO)


def _es_cancelacion(texto: str) -> bool:
    palabras = set(_normalizar_respuesta(texto).split())
    return bool(palabras & _PALABRAS_NO)
