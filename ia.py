import asyncio
import base64
import io
import json
import os
import re
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

try:
    from hud_control import set_estado as _hud_set_estado, set_transcripcion as _hud_set_tx
except ImportError:
    def _hud_set_estado(*a, **kw): pass   # noqa: E704
    def _hud_set_tx(*a): pass             # noqa: E704

_client             = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_modelo_whisper: WhisperModel | None = None
_whisper_lock       = threading.Lock()
_SENTENCE_END       = frozenset(".?!")
_tts_ya_reproducido = threading.Event()

# ── Cartesia TTS ───────────────────────────────────────────────────────────────
_CARTESIA_VOICE_ID = "2fc4f1ec-bfd0-46f1-8e6d-d4279eaaf838"
_CARTESIA_MODEL    = "sonic-3.5"
try:
    from cartesia import Cartesia as _CartesiaClient
    _cartesia_client = (
        _CartesiaClient(api_key=os.getenv("CARTESIA_API_KEY"))
        if os.getenv("CARTESIA_API_KEY")
        else None
    )
except ImportError:
    _cartesia_client = None

VOICE = "es-MX-JorgeNeural"

DEBUG_TEXTO = os.getenv("STEM_DEBUG_TEXTO", "1") == "1"  # default True (modo texto)


def toggle_modo_entrada() -> None:
    global DEBUG_TEXTO
    DEBUG_TEXTO = not DEBUG_TEXTO
    modo = "TEXTO (debug)" if DEBUG_TEXTO else "VOZ"
    print(f"[stem] modo de entrada → {modo}")
_cancelar   = threading.Event()

_MAX_INTENTOS_AGENTE = 3

_TOOLS_SYSTEM = (
    "Eres Stem, asistente de escritorio por voz en Windows 11. "
    "Usa responder_en_voz para preguntas o conversación. "
    "Usa ejecutar_accion para acciones en el PC. "
    "Usa explorar_carpeta para listar archivos antes de copiar/mover/abrir. "
    "Usa buscar_y_abrir_youtube para música o videos. "
    "Si el usuario menciona un archivo por nombre sin dar la ruta exacta, "
    "SIEMPRE usa explorar_carpeta primero para encontrarlo antes de ejecutar_accion. "
    "Cuando uses rutas de archivos en el código, usa siempre las rutas exactas "
    "que te devolvió explorar_carpeta. Usa barras dobles \\\\ o r-strings r'...' "
    "para rutas de Windows. "
    "Para envíos por WhatsApp, hacé una sola llamada a la tool correspondiente por petición. "
    "REGLA ANTI-COMBINACIONES: en enviar_archivo_whatsapp, cada item de 'envios' representa "
    "UN ÚNICO par (contacto, archivo) explícitamente pedido por el usuario. "
    "La cantidad de items debe ser IGUAL a la cantidad de asignaciones explícitas pedidas. "
    "Ejemplo CORRECTO: si el usuario dice 'envíale A.txt a Juan y B.csv a María', generá "
    "EXACTAMENTE [{contacto: Juan, archivo: A.txt}, {contacto: María, archivo: B.csv}]. "
    "NUNCA generes {contacto: Juan, archivo: B.csv} ni {contacto: María, archivo: A.txt} — "
    "eso no fue pedido. "
    "Si el usuario dice 'mándale A.txt y B.csv a Diana', SÍ generá dos items para Diana: "
    "[{contacto: Diana, archivo: A.txt}, {contacto: Diana, archivo: B.csv}] — "
    "eso SÍ fue pedido explícitamente. "
    "REGLA DE DEPENDENCIAS: antes de ejecutar una acción verificá si depende "
    "de otra aún no realizada (enviar depende de haber creado, leer depende de "
    "haber descomprimido). Si hay dependencia pendiente, resuélvela primero. "
    "REGLA DE TAREAS MÚLTIPLES: si el usuario pidió varias acciones, completar "
    "una (envío, creación, búsqueda, o cualquier otra tool) NO es señal de "
    "cierre — identificá TODAS las acciones pedidas y ejecutalas antes de "
    "responder_en_voz. Si el pedido era una sola acción, terminá al completarla. "
    "MODO CONVERSACIÓN: cuando el plan es solo responder_en_voz (sin acciones "
    "pendientes), sé proactivo, cálido y con iniciativa — mostrá interés genuino, "
    "hacé preguntas de seguimiento naturales y mantenné la conversación viva. No "
    "des respuestas cortas y neutras — eso suena a contestador automático. Si el "
    "usuario dice 'q tal' o 'cómo vas', respondé con energía y preguntá algo de "
    "vuelta. Si el usuario menciona una tarea concreta (crear, enviar, buscar algo), "
    "cambiá de modo naturalmente declarando un plan con acciones reales. "
    "CIERRE DE SESIÓN: cuando el usuario se despida ('chau', 'hasta luego', "
    "'gracias eso era todo', 'nos vemos', etc.), respondé con una despedida breve "
    "y natural usando responder_en_voz con cerrar_sesion=true. Nunca cierres "
    "abruptamente sin contestar. "
    "Siempre usa una tool, nunca respondas texto directo."
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "declarar_plan",
            "description": (
                "SIEMPRE llama esta tool PRIMERO, antes de cualquier otra acción. "
                "Declara el plan completo de acciones que vas a ejecutar para cumplir "
                "la petición del usuario. No ejecutes nada todavía — solo declara el plan "
                "para que el usuario lo confirme."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pasos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "paso": {
                                    "type": "integer",
                                    "description": "Número de paso, empezando en 1",
                                },
                                "accion": {
                                    "type": "string",
                                    "description": (
                                        "Nombre de la tool que se usará: "
                                        "ejecutar_accion / enviar_whatsapp / "
                                        "enviar_archivo_whatsapp / buscar_y_abrir_youtube / "
                                        "explorar_carpeta / responder_en_voz"
                                    ),
                                },
                                "descripcion": {
                                    "type": "string",
                                    "description": (
                                        "Descripción breve en español de qué hace este paso "
                                        "(ej: 'Crear ensayo_luna.txt en Documentos', "
                                        "'Enviar ensayo_luna.txt a mamá por WhatsApp')"
                                    ),
                                },
                            },
                            "required": ["paso", "accion", "descripcion"],
                        },
                    },
                },
                "required": ["pasos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "responder_en_voz",
            "description": "Responde una pregunta o conversación en voz al usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "texto": {
                        "type": "string",
                        "description": "Respuesta en español, máximo 2 oraciones.",
                    },
                    "cerrar_sesion": {
                        "type": "boolean",
                        "description": "true solo cuando el usuario se despide y la sesión debe cerrarse.",
                    },
                },
                "required": ["texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ejecutar_accion",
            "description": (
                "Ejecuta una acción en el PC del usuario vía Python. "
                "Si el usuario no especifica formato de archivo ni destino, elegí el más natural "
                "para el contenido (texto/ensayo → .txt, tabla/datos → .csv) y mencionalo "
                "explícitamente en la descripción del paso del plan para que el usuario pueda "
                "corregirlo antes de confirmar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "descripcion": {
                        "type": "string",
                        "description": "Descripción corta en español de lo que va a hacer.",
                    },
                    "codigo": {
                        "type": "string",
                        "description": (
                            "Código Python ejecutable en una sola línea. "
                            "Puedes usar: subprocess, os, webbrowser, pyautogui. "
                            "Para crear archivos de texto (TXT, MD, CSV, JSON, HTML) en el escritorio usar: "
                            "open(os.path.join(_get_escritorio(), 'nombre.ext'), 'w', encoding='utf-8').write('contenido'). "
                            "Para PDF o XLSX NO intentar crearlos. "
                            "No uses librerías externas."
                        ),
                    },
                },
                "required": ["descripcion", "codigo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_y_abrir_youtube",
            "description": "Busca un video en YouTube y abre el más relevante en Brave. Usar cuando el usuario pida reproducir música, ver un video o buscar algo en YouTube.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Término de búsqueda, ej: 'Hungry Eyes', 'lofi hip hop', 'Luis Miguel'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explorar_carpeta",
            "description": "Lista los archivos de una carpeta del sistema para encontrar un archivo específico. Usar antes de copiar, mover o abrir un archivo cuando el usuario no da la ruta exacta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "carpeta": {
                        "type": "string",
                        "enum": ["descargas", "documentos", "escritorio", "musica", "videos"],
                        "description": "Carpeta a explorar",
                    }
                },
                "required": ["carpeta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_whatsapp",
            "description": "Envía mensajes de WhatsApp a uno o varios contactos en una sola llamada. Cada item de 'envios' tiene su propio contacto y mensaje (pueden ser iguales o distintos por persona).",
            "parameters": {
                "type": "object",
                "properties": {
                    "envios": {
                        "type": "array",
                        "description": "Lista de envíos. Un item por destinatario.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "contacto": {
                                    "type": "string",
                                    "description": "Nombre del contacto tal como aparece en la agenda.",
                                },
                                "mensaje": {
                                    "type": "string",
                                    "description": "Texto del mensaje para este contacto.",
                                },
                            },
                            "required": ["contacto", "mensaje"],
                        },
                    },
                },
                "required": ["envios"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_archivo_whatsapp",
            "description": (
                "Envía archivos por WhatsApp. Un ítem por par exacto (contacto, archivo). "
                "SIEMPRE usa explorar_carpeta primero para obtener rutas absolutas. "
                "Cada item representa UNA asignación explícita del usuario: un archivo para un contacto. "
                "Si el usuario dijo 'A para Juan y B para María': "
                "[{contacto: Juan, archivo: A}, {contacto: María, archivo: B}] — 2 items, no 4. "
                "Si el usuario dijo 'A y B para Diana': "
                "[{contacto: Diana, archivo: A}, {contacto: Diana, archivo: B}] — 2 items, correcto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "envios": {
                        "type": "array",
                        "description": "Lista de pares exactos (contacto, archivo). Un item por envío explícito.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "contacto": {
                                    "type": "string",
                                    "description": "Nombre del contacto tal como aparece en la agenda.",
                                },
                                "archivo": {
                                    "type": "string",
                                    "description": "Ruta absoluta exacta del archivo, obtenida de explorar_carpeta.",
                                },
                            },
                            "required": ["contacto", "archivo"],
                        },
                    },
                },
                "required": ["envios"],
            },
        },
    },
]


def _cancelado() -> bool:
    return _cancelar.is_set()


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
    """Captura audio hasta que Vosk detecta silencio final o timeout."""
    chunks: list[bytes] = []
    rec.Reset()
    t_inicio = time.time()
    t_fin    = t_inicio + timeout
    while time.time() < t_fin:
        try:
            data = audio_q.get(timeout=0.5)
        except _stdlib_queue.Empty:
            continue
        chunks.append(data)
        if rec.AcceptWaveform(data):
            result_text = json.loads(rec.Result()).get("text", "").strip()
            elapsed     = time.time() - t_inicio
            if result_text and (len(result_text.split()) > 3 or elapsed > 4.0):
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
        sd.play(samples, decoded.sample_rate)
        sd.wait()
        print(f"{_ts()}[ia] fin TTS Edge")
    except Exception as exc:
        print(f"{_ts()}[ia] TTS Edge error: {exc}")


def _hablar_cartesia(texto: str) -> None:
    """Sintetiza y reproduce texto con Cartesia (voz Mateo). Fallback a Edge TTS si falla."""
    print(f"{_ts()}[ia] inicio TTS Cartesia (sintetizando...)")
    try:
        audio_bytes = _cartesia_client.tts.bytes(
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
        decoded = miniaudio.decode(
            audio_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=44100,
        )
        samples = np.frombuffer(bytes(decoded.samples), dtype=np.int16)
        print(f"{_ts()}[ia] TTS Cartesia reproduciendo...")
        sd.play(samples, decoded.sample_rate)
        sd.wait()
        print(f"{_ts()}[ia] fin TTS Cartesia")
    except Exception as exc:
        print(f"{_ts()}[ia] error TTS Cartesia: {exc} — fallback a Edge TTS")
        _hablar_edge_original(texto)


def hablar_edge(texto: str) -> None:
    """
    Punto de entrada TTS principal. Usa Cartesia (Mateo) si CARTESIA_API_KEY está en .env,
    Edge TTS (Jorge) como fallback. No-op si el audio ya fue reproducido en este turno.
    """
    if _tts_ya_reproducido.is_set():
        _tts_ya_reproducido.clear()
        return

    if _cartesia_client is not None:
        _hablar_cartesia(texto)
    else:
        _hablar_edge_original(texto)


# ── Modo agente ────────────────────────────────────────────────────────────────

def _get_escritorio() -> str:
    """Retorna la ruta real del escritorio leyendo el registro de Windows.
    Compatible con OneDrive, rutas personalizadas y cualquier configuración."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        path, _ = winreg.QueryValueEx(key, "Desktop")
        return path
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def _get_descargas() -> str:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        path, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
        return path
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Downloads")


def _get_documentos() -> str:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        path, _ = winreg.QueryValueEx(key, "Personal")
        return path
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Documents")


def _listar_carpeta(carpeta: str) -> list[str]:
    """Lista archivos de una carpeta del sistema. Retorna lista de nombres."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        carpetas_map = {
            "descargas":  winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0],
            "downloads":  winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0],
            "documentos": winreg.QueryValueEx(key, "Personal")[0],
            "documents":  winreg.QueryValueEx(key, "Personal")[0],
            "escritorio": _get_escritorio(),
            "desktop":    _get_escritorio(),
            "musica":     winreg.QueryValueEx(key, "My Music")[0],
            "music":      winreg.QueryValueEx(key, "My Music")[0],
            "videos":     winreg.QueryValueEx(key, "My Video")[0],
        }
    except Exception:
        carpetas_map = {
            "descargas":  os.path.join(os.path.expanduser("~"), "Downloads"),
            "downloads":  os.path.join(os.path.expanduser("~"), "Downloads"),
            "documentos": os.path.join(os.path.expanduser("~"), "Documents"),
            "documents":  os.path.join(os.path.expanduser("~"), "Documents"),
            "musica":     os.path.join(os.path.expanduser("~"), "Music"),
            "music":      os.path.join(os.path.expanduser("~"), "Music"),
            "videos":     os.path.join(os.path.expanduser("~"), "Videos"),
            "escritorio": _get_escritorio(),
            "desktop":    _get_escritorio(),
        }

    ruta = carpetas_map.get(carpeta.lower())
    if not ruta or not os.path.exists(ruta):
        return []

    try:
        return [os.path.join(ruta, f) for f in os.listdir(ruta) if os.path.isfile(os.path.join(ruta, f))]
    except Exception as exc:
        print(f"{_ts()}[explorar] error: {exc}")
        return []


def _build_exec_ns() -> dict:
    """Namespace para exec() con módulos seguros disponibles al código generado."""
    import shutil
    ns: dict = {
        "subprocess":      subprocess,
        "os":              os,
        "webbrowser":      webbrowser,
        "shutil":          shutil,
        "_get_escritorio": _get_escritorio,
        "_get_descargas":  _get_descargas,
        "_get_documentos": _get_documentos,
    }
    try:
        import pyautogui
        ns["pyautogui"] = pyautogui
    except ImportError:
        pass
    return ns


def _pedir_accion_gpt(texto: str) -> dict | None:
    """
    Llama GPT-4o-mini forzando tool ejecutar_accion.
    Retorna dict con 'descripcion' y 'codigo', o None si falla.
    """
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _TOOLS_SYSTEM},
                {"role": "user",   "content": texto},
            ],
            tools=_TOOLS,
            tool_choice={"type": "function", "function": {"name": "ejecutar_accion"}},
            max_tokens=200,
        )
        return json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
    except Exception as exc:
        print(f"{_ts()}[agente] error GPT: {exc}")
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


def _buscar_youtube(query: str) -> str | None:
    import urllib.request
    import urllib.parse
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return None
    try:
        params = urllib.parse.urlencode({
            "part": "snippet",
            "q": query,
            "maxResults": 1,
            "type": "video",
            "key": api_key,
        })
        with urllib.request.urlopen(
            f"https://www.googleapis.com/youtube/v3/search?{params}", timeout=5
        ) as r:
            data = json.loads(r.read())
        video_id = data["items"][0]["id"]["videoId"]
        return f"https://www.youtube.com/watch?v={video_id}"
    except Exception as exc:
        print(f"{_ts()}[youtube] error: {exc}")
        return None


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


_LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "agente_errores.log")


def _log_exec_error(exc: Exception) -> None:
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        ts  = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        msg = f"{ts} {type(exc).__name__}: {exc}\n"
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass


def _ejecutar_con_verificacion(
    descripcion: str,
    codigo: str,
    audio_q: _stdlib_queue.Queue,
    rec: object,
    youtube_query: str | None = None,
) -> bool:
    if _cancelado():
        return False

    _resultado_yt: list[str | None] = [None]
    _hilo_yt: threading.Thread | None = None

    if youtube_query:
        def _fetch():
            _resultado_yt[0] = _buscar_youtube(youtube_query)
        _hilo_yt = threading.Thread(target=_fetch, daemon=True)
        _hilo_yt.start()
        mensaje = f"Buscando {youtube_query} en YouTube"
    else:
        mensaje = descripcion

    if _cancelado():
        return False

    _reproducir_oracion(f"{mensaje}. ¿Procedo?")

    if _cancelado():
        return False

    if DEBUG_TEXTO:
        confirmacion = _escuchar_confirmacion_debug()
    else:
        confirmacion = _escuchar_confirmacion(audio_q, rec)

    if _cancelado() or confirmacion in ("no", "cancelar"):
        _reproducir_oracion("Cancelado.")
        return False

    if youtube_query:
        if _hilo_yt:
            _hilo_yt.join(timeout=4)
        url = _resultado_yt[0]
        brave = r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe'
        if url:
            print(f"{_ts()}[youtube] abriendo: {url}")
            subprocess.Popen([brave, url])
        else:
            import urllib.parse
            fallback = f"https://www.youtube.com/results?search_query={urllib.parse.quote(youtube_query)}"
            subprocess.Popen([brave, fallback])
            _reproducir_oracion("No encontré el video exacto, abriendo búsqueda.")
        return True
    else:
        print(f"{_ts()}[agente] ejecutando: {codigo}")
        try:
            exec(codigo, _build_exec_ns())  # noqa: S102
            return True
        except NameError as exc:
            print(f"{_ts()}[agente] GPT intentó usar un helper inexistente: {exc}")
            _log_exec_error(exc)
            _reproducir_oracion("Hubo un error al ejecutar.")
            return False
        except Exception as exc:
            print(f"{_ts()}[agente] exec error: {exc}")
            _log_exec_error(exc)
            _reproducir_oracion("Hubo un error al ejecutar.")
            return False


def _ejecutar_silencioso(
    descripcion: str,
    codigo: str,
    youtube_query: str | None = None,
) -> bool:
    """Ejecuta sin pedir confirmación por acción (el Orchestrator ya la obtuvo al inicio)."""
    if _cancelado():
        return False

    if youtube_query:
        url = _buscar_youtube(youtube_query)
        brave = r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe'
        if url:
            print(f"{_ts()}[youtube] abriendo: {url}")
            subprocess.Popen([brave, url])
        else:
            import urllib.parse  # noqa: PLC0415
            fallback = f"https://www.youtube.com/results?search_query={urllib.parse.quote(youtube_query)}"
            subprocess.Popen([brave, fallback])
            _reproducir_oracion("No encontré el video exacto, abriendo búsqueda.")
        return True

    print(f"{_ts()}[agente] ejecutando: {codigo}")
    try:
        exec(codigo, _build_exec_ns())  # noqa: S102
        return True
    except NameError as exc:
        print(f"{_ts()}[agente] GPT intentó usar un helper inexistente: {exc}")
        _log_exec_error(exc)
        _reproducir_oracion("Hubo un error al ejecutar.")
        return False
    except Exception as exc:
        print(f"{_ts()}[agente] exec error: {exc}")
        _log_exec_error(exc)
        _reproducir_oracion("Hubo un error al ejecutar.")
        return False


MAX_BLOQUEOS_GATEKEEPER = 2


def _quedan_pendientes(peticion_original: str, messages: list) -> bool:
    """Pregunta a GPT si la petición original quedó completamente satisfecha.
    Devuelve True si falta algo. Falla de forma segura (False en cualquier excepción)."""
    try:
        check_messages = [
            {
                "role": "system",
                "content": (
                    "Eres un verificador estricto. El usuario hizo esta petición:\n"
                    f"'{peticion_original}'\n\n"
                    "Basándote ÚNICAMENTE en las acciones ya ejecutadas en el historial, "
                    "¿quedó la petición COMPLETAMENTE satisfecha? "
                    "Responde SOLO 'SÍ' o 'NO'."
                ),
            }
        ] + [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=check_messages,
            max_tokens=5,
            temperature=0,
        )
        answer = resp.choices[0].message.content.strip().upper()
        return answer.startswith("NO")
    except Exception:
        return False


TOOLS_CONVERSACIONALES: frozenset[str] = frozenset({"responder_en_voz"})

_PALABRAS_SI = {"sí", "si", "dale", "ok", "procede", "adelante", "perfecto", "correcto", "listo", "va", "claro"}
_PALABRAS_NO = {"no", "cancela", "cancelar", "para", "detén", "detente", "stop", "olvídalo", "olvida"}


def _requiere_confirmacion(plan: list[dict]) -> bool:
    """Devuelve False si todos los pasos son solo conversacionales (no necesitan confirmación)."""
    return not all(p.get("accion") in TOOLS_CONVERSACIONALES for p in plan)


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


def _replannear(peticion_actualizada: str) -> list[dict]:
    """Genera un plan revisado con GPT a partir de petición original + corrección del usuario."""
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres Stem. El usuario quiere modificar el plan de acciones. "
                        "Genera un plan revisado en formato JSON con esta estructura exacta: "
                        '[{"paso": 1, "accion": "nombre_tool", "descripcion": "descripción breve en español"}, ...]. '
                        "Las tools disponibles son: ejecutar_accion, enviar_whatsapp, "
                        "enviar_archivo_whatsapp, buscar_y_abrir_youtube, explorar_carpeta, responder_en_voz. "
                        "Respondé SOLO con el JSON, sin texto extra ni backticks."
                    ),
                },
                {"role": "user", "content": peticion_actualizada},
            ],
            max_tokens=300,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        # quitar posibles backticks de markdown si GPT los incluye
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        print(f"{_ts()}[orchestrator] _replannear error: {exc}")
        return []


def _humanizar_plan(pasos: list[dict]) -> str:
    """Convierte la lista de pasos del plan en una frase natural en español vía GPT.
    Fallback: lista numerada clásica si GPT falla."""
    fallback_lineas = [f"{p.get('paso', i+1)}. {p.get('descripcion', '')}" for i, p in enumerate(pasos)]
    fallback = "Haré lo siguiente: " + ", ".join(fallback_lineas) + ". ¿Procedo?"
    try:
        pasos_texto = "\n".join(
            f"{p.get('paso', i+1)}. [{p.get('accion', '')}] {p.get('descripcion', '')}"
            for i, p in enumerate(pasos)
        )
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres Stem, un asistente de voz personal. El usuario acaba de hacer una petición "
                        "y vas a ejecutar un plan de acciones. Convierte la siguiente lista de pasos técnicos "
                        "en UNA sola frase natural en español, en primera persona, que explique lo que vas a "
                        "hacer de forma conversacional y fluida — como si se lo explicaras a un amigo, no como "
                        "si leyeras un menú. No uses números, no uses corchetes, no menciones nombres técnicos "
                        "de herramientas (ejecutar_accion, enviar_whatsapp, etc.). Si hay varios pasos similares, "
                        "agrúpalos naturalmente. Termina siempre con '¿Procedo?'. Máximo 2-3 oraciones."
                    ),
                },
                {"role": "user", "content": pasos_texto},
            ],
            max_tokens=120,
            temperature=0.4,
        )
        resultado = resp.choices[0].message.content.strip()
        if resultado:
            return resultado
    except Exception as exc:
        print(f"{_ts()}[orchestrator] _humanizar_plan error: {exc}")
    return fallback


def _correccion_tiene_sentido(plan_actual: list[dict], correccion: str) -> bool:
    """Pregunta a GPT (max_tokens=5) si la corrección del usuario tiene sentido para ajustar el plan."""
    try:
        pasos_txt = "; ".join(
            f"{p.get('paso', i+1)}. {p.get('descripcion', '')}"
            for i, p in enumerate(plan_actual)
        )
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un validador. El usuario tiene este plan: "
                        f"[{pasos_txt}]. "
                        "¿La corrección del usuario tiene sentido como modificación al plan? "
                        "Responde SOLO 'SÍ' o 'NO'."
                    ),
                },
                {"role": "user", "content": correccion},
            ],
            max_tokens=5,
            temperature=0,
        )
        answer = resp.choices[0].message.content.strip().upper()
        return answer.startswith("SÍ") or answer.startswith("SI")
    except Exception:
        return True  # en caso de error, asumir que tiene sentido y dejar pasar


class Orchestrator:
    def __init__(self, plan: list[dict], peticion_original: str) -> None:
        self.plan = plan
        self.peticion_original = peticion_original
        self.paso_actual = 0
        self.desviaciones: list[str] = []

    def confirmar_con_usuario(self, audio_q: _stdlib_queue.Queue, rec: object) -> bool:
        # BUG 1 — si el plan es solo conversacional, no hace falta confirmar
        if not _requiere_confirmacion(self.plan):
            return True

        MAX_REFINAMIENTOS = 3
        plan_context = self.peticion_original
        silencios_consecutivos = 0   # BUG 4
        confusiones_seguidas = 0     # MEJORA 6

        for intento in range(MAX_REFINAMIENTOS):
            if _cancelado():
                return False

            resumen = _humanizar_plan(self.plan)
            _reproducir_oracion(resumen)

            if _cancelado():
                return False

            if DEBUG_TEXTO:
                respuesta_texto = input("[DEBUG] respuesta (sí / no / corrección): ").strip()
            else:
                respuesta_texto = _transcribir_respuesta(audio_q, rec)

            print(f"{_ts()}[orchestrator] respuesta intento {intento + 1}: '{respuesta_texto}'")

            # BUG 4 — silencios consecutivos
            if not respuesta_texto:
                silencios_consecutivos += 1
                if silencios_consecutivos >= 2:
                    _reproducir_oracion("Cuando quieras me decís si procedo o si querés cambiar algo.")
                else:
                    _reproducir_oracion("No te escuché bien, ¿procedo o cambiás algo?")
                continue
            silencios_consecutivos = 0  # reset al recibir respuesta

            if _es_confirmacion(respuesta_texto):
                return True

            if _es_cancelacion(respuesta_texto):
                _reproducir_oracion("Entendido, cancelado.")
                return False

            # Corrección en lenguaje natural — BUG 3: validar antes de replannear
            print(f"{_ts()}[orchestrator] corrección recibida: '{respuesta_texto}'")
            if not _correccion_tiene_sentido(self.plan, respuesta_texto):
                confusiones_seguidas += 1
                print(f"{_ts()}[orchestrator] corrección no reconocida (confusión #{confusiones_seguidas})")
                # MEJORA 6 — escalar mensaje según confusiones acumuladas
                if confusiones_seguidas >= 3:
                    _reproducir_oracion("Está bien, avísame cuando quieras que lo haga.")
                    return False
                if confusiones_seguidas >= 2:
                    _reproducir_oracion(
                        "No estoy entendiendo bien qué querés cambiar. "
                        "Podés decirme de nuevo con otras palabras, o decime 'cancela' para empezar de nuevo."
                    )
                else:
                    _reproducir_oracion("Perdona, no entendí bien. ¿Querés que proceda con el plan o cambiás algo?")
                continue
            confusiones_seguidas = 0  # reset si la corrección tiene sentido

            plan_context = f"{plan_context}. Corrección del usuario: {respuesta_texto}"
            nuevo_plan = _replannear(plan_context)
            if nuevo_plan:
                self.plan = nuevo_plan
                print(f"{_ts()}[orchestrator] plan revisado: {len(self.plan)} paso(s)")
                for p in self.plan:
                    print(f"  {p.get('paso', '?')}. [{p.get('accion', '')}] {p.get('descripcion', '')}")
            else:
                _reproducir_oracion("No pude ajustar el plan, ¿procedemos con el original?")

        # MEJORA 5 — mensaje natural al agotar intentos
        _reproducir_oracion("Está bien, avísame cuando quieras que lo haga.")
        return False

    def autorizar(self, tool_name: str, descripcion_corta: str) -> bool:
        # explorar_carpeta es un paso de soporte transparente — nunca cuenta como desviación
        if tool_name == "explorar_carpeta":
            return True

        if self.paso_actual >= len(self.plan):
            self._registrar_desviacion(
                f"paso extra no declarado: {tool_name} — {descripcion_corta}"
            )
            self.paso_actual += 1
            return True  # permisivo en v1

        paso_esperado = self.plan[self.paso_actual]
        if tool_name != paso_esperado["accion"]:
            self._registrar_desviacion(
                f"paso {self.paso_actual + 1}: esperaba '{paso_esperado['accion']}' "
                f"pero GPT llama '{tool_name}' ({descripcion_corta})"
            )
        self.paso_actual += 1
        return True

    def _registrar_desviacion(self, msg: str) -> None:
        print(f"{_ts()}[orchestrator] ⚠ desviación: {msg}")
        self.desviaciones.append(msg)

    def reporte_final(self) -> None:
        completados = self.paso_actual
        total = len(self.plan)
        if self.desviaciones:
            print(
                f"{_ts()}[orchestrator] plan: {completados}/{total} pasos | "
                f"{len(self.desviaciones)} desviación(es):"
            )
            for d in self.desviaciones:
                print(f"  - {d}")
        else:
            print(
                f"{_ts()}[orchestrator] plan completado sin desviaciones "
                f"({completados}/{total} pasos)"
            )


def _get_rutas_contexto() -> str:
    """Resuelve rutas reales del usuario via Registry (OneDrive-safe)."""
    try:
        import winreg  # noqa: PLC0415
        _key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        descargas = winreg.QueryValueEx(_key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
        documentos = winreg.QueryValueEx(_key, "Personal")[0]
    except Exception:
        descargas = os.path.join(os.path.expanduser("~"), "Downloads")
        documentos = os.path.join(os.path.expanduser("~"), "Documents")
    return (
        f"Rutas reales del sistema:\n"
        f"- Escritorio: {_get_escritorio()}\n"
        f"- Descargas: {descargas}\n"
        f"- Documentos: {documentos}\n"
        "Usa SIEMPRE estas rutas exactas como destino, nunca las inventes."
    )


def _capturar_y_transcribir(audio_q: _stdlib_queue.Queue, rec: object) -> str:
    """Captura audio y transcribe con Faster-Whisper. Devuelve '' en silencio/error."""
    from voz import _capturar_audio_ia  # noqa: PLC0415
    audio_bytes = _capturar_audio_ia(audio_q, rec)
    if not audio_bytes:
        return ""
    try:
        return transcribir_whisper(audio_bytes)
    except Exception:
        return ""


def _ejecutar_turno(
    messages: list,
    texto: str,
    audio_q: _stdlib_queue.Queue,
    rec: object,
) -> str:
    """Loop interno de rondas GPT para un turno de la sesión.
    Modifica messages in-place. Devuelve 'continuar', 'cerrar' o 'error'."""
    orchestrator: Orchestrator | None = None
    _bloqueos_gk = 0
    _tool_choice: object = {"type": "function", "function": {"name": "declarar_plan"}}
    MAX_RONDAS = 12

    for ronda in range(MAX_RONDAS):
        if _cancelado():
            if orchestrator:
                orchestrator.reporte_final()
            return "error"

        _hud_set_estado("procesando", ronda=ronda + 1, max_rondas=MAX_RONDAS)
        print(f"{_ts()}[ia] GPT-4o-mini ronda {ronda + 1}...")
        try:
            resp = _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=_TOOLS,
                tool_choice=_tool_choice,
                max_tokens=400,
                parallel_tool_calls=False,
            )
        except Exception as exc:
            print(f"{_ts()}[ia] error GPT: {exc}")
            _hud_set_estado("hablando")
            hablar_edge("Hubo un error al procesar tu solicitud.")
            return "error"

        if _cancelado():
            if orchestrator:
                orchestrator.reporte_final()
            return "error"

        tool_call = resp.choices[0].message.tool_calls[0]
        tool_name = tool_call.function.name
        args      = json.loads(tool_call.function.arguments)
        print(f"{_ts()}[ia] tool: {tool_name}")

        messages.append(resp.choices[0].message)

        # ── FASE 1: declaración del plan ────────────────────────────────────
        if tool_name == "declarar_plan":
            plan = args.get("pasos", [])
            print(f"{_ts()}[orchestrator] plan recibido: {len(plan)} paso(s)")
            for p in plan:
                print(f"  {p.get('paso','?')}. [{p.get('accion','')}] {p.get('descripcion','')}")

            orchestrator = Orchestrator(plan, texto)

            if not orchestrator.confirmar_con_usuario(audio_q, rec):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps({"confirmado": False}, ensure_ascii=False),
                })
                _reproducir_oracion("Entendido, cancelado.")
                return "error"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(
                    {"confirmado": True, "pasos": len(plan)}, ensure_ascii=False
                ),
            })
            _tool_choice = "required"
            continue

        # Si GPT saltó declarar_plan (no debería ocurrir), crear orchestrator vacío
        if orchestrator is None:
            print(f"{_ts()}[orchestrator] advertencia: GPT saltó declarar_plan")
            orchestrator = Orchestrator([], texto)
            _tool_choice = "required"

        # ── FASE 3: ejecución vigilada ──────────────────────────────────────
        descripcion_corta = args.get("descripcion", tool_name)
        orchestrator.autorizar(tool_name, descripcion_corta)

        if tool_name == "explorar_carpeta":
            carpeta  = args.get("carpeta", "")
            archivos = _listar_carpeta(carpeta)
            print(f"{_ts()}[explorar] {carpeta}: {len(archivos)} archivos")
            messages.append({
                "role":         "tool",
                "tool_call_id": tool_call.id,
                "content":      json.dumps(archivos, ensure_ascii=False),
            })
            continue

        if tool_name == "responder_en_voz":
            respuesta = args.get("texto", "")
            cerrar = bool(args.get("cerrar_sesion", False))
            if _bloqueos_gk < MAX_BLOQUEOS_GATEKEEPER and _quedan_pendientes(texto, messages):
                _bloqueos_gk += 1
                print(
                    f"{_ts()}[ia] gatekeeper: cierre prematuro detectado "
                    f"(bloqueo {_bloqueos_gk}/{MAX_BLOQUEOS_GATEKEEPER})"
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(
                        {"pendiente": True, "instruccion": "Aún faltan acciones. Completá TODAS las acciones pedidas antes de responder_en_voz."},
                        ensure_ascii=False,
                    ),
                })
                messages.append({
                    "role": "user",
                    "content": "Faltan acciones por completar. Revisá la petición original y ejecutá las que faltan.",
                })
                continue
            orchestrator.reporte_final()
            print(f"{_ts()}[ia] respuesta: '{respuesta}' | cerrar={cerrar}")
            _hud_set_estado("hablando")
            hablar_edge(respuesta)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"entregado": True}, ensure_ascii=False),
            })
            return "cerrar" if cerrar else "continuar"

        if tool_name == "ejecutar_accion":
            codigo = args.get("codigo", "").strip()
            print(f"{_ts()}[ia] acción: {codigo}")
            exito = _ejecutar_silencioso(
                descripcion=args.get("descripcion", "Ejecutando acción"),
                codigo=codigo,
            )
            messages.append({
                "role":         "tool",
                "tool_call_id": tool_call.id,
                "content":      json.dumps({"exito": exito}, ensure_ascii=False),
            })
            continue

        if tool_name == "buscar_y_abrir_youtube":
            query = args.get("query", "")
            print(f"{_ts()}[ia] youtube: '{query}'")
            exito = _ejecutar_silencioso(
                descripcion=f"Buscando {query} en YouTube",
                codigo="",
                youtube_query=query,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"exito": exito}, ensure_ascii=False),
            })
            continue

        if tool_name == "enviar_whatsapp":
            from whatsapp import enviar_whatsapp  # noqa: PLC0415
            envios = args.get("envios", [])
            for e in envios:
                print(f"{_ts()}[ia] whatsapp → {e.get('contacto')}: {e.get('mensaje')}")
            ok = enviar_whatsapp(envios)
            if not ok:
                _hud_set_estado("hablando")
                hablar_edge("No pude enviar uno o más mensajes.")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"enviado": ok}, ensure_ascii=False),
            })
            continue

        if tool_name == "enviar_archivo_whatsapp":
            from collections import Counter  # noqa: PLC0415
            from whatsapp import enviar_archivo_whatsapp  # noqa: PLC0415
            envios_arch = args.get("envios", [])
            _arch_counts = Counter(e.get("archivo", "") for e in envios_arch if e.get("archivo"))
            for _arch, _cnt in _arch_counts.items():
                if _cnt > 1:
                    print(f"{_ts()}[ia] posible over-sending: '{Path(_arch).name}' asignado a {_cnt} contactos")
            for e in envios_arch:
                print(f"{_ts()}[ia] whatsapp archivo → {e.get('contacto', '')}: {e.get('archivo', '')}")
            ok = enviar_archivo_whatsapp(envios_arch)
            if not ok:
                _hud_set_estado("hablando")
                hablar_edge("No pude enviar uno o más archivos.")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"enviado": ok}, ensure_ascii=False),
            })
            continue

    if orchestrator:
        orchestrator.reporte_final()
    print(f"{_ts()}[ia] agotó rondas sin acción final")
    _hud_set_estado("hablando")
    hablar_edge("No pude completar la acción.")
    return "error"


def sesion_inteligente(audio_q: _stdlib_queue.Queue, rec: object) -> None:
    """Sesión conversacional continua: un solo historial acumulado por activación de Stem.
    GPT recibe el contexto completo en cada turno y decide naturalmente cuándo cerrar."""
    if _cancelado():
        return

    messages: list = [
        {"role": "system", "content": f"{_TOOLS_SYSTEM}\n\n{_get_rutas_contexto()}"},
    ]
    MAX_TURNOS = 20

    for turno in range(MAX_TURNOS):
        if _cancelado():
            break

        _hud_set_estado("procesando")
        print(f"{_ts()}[ia] [turno {turno + 1}/{MAX_TURNOS}] esperando input...")

        if DEBUG_TEXTO:
            print(f"{_ts()}[ia] di tu pregunta (debug)...")
            try:
                texto = input("[DEBUG] escribe tu pregunta: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not texto:
                break
            _hud_set_tx(texto)
        else:
            print(f"{_ts()}[ia] di tu pregunta...")
            texto = _capturar_y_transcribir(audio_q, rec)
            if not texto:
                print(f"{_ts()}[ia] no se detectó texto — cerrando sesión.")
                break
            print(f"{_ts()}[ia] oído: '{texto}'")
            _hud_set_tx(texto)

        messages.append({"role": "user", "content": texto})

        resultado = _ejecutar_turno(messages, texto, audio_q, rec)

        if resultado in ("cerrar", "error"):
            break

    print(f"{_ts()}[ia] sesión finalizada.")


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
        _ejecutar_con_verificacion(descripcion, codigo, audio_q, rec)
        return
