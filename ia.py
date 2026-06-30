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

DEBUG_TEXTO = os.getenv("STEM_DEBUG_TEXTO", "0") == "1"
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
    "Usa enviar_archivo_whatsapp para enviar archivos por WhatsApp, siempre "
    "después de explorar_carpeta para obtener la ruta exacta. "
    "Siempre usa una tool, nunca respondas texto directo."
)

_TOOLS = [
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
                    }
                },
                "required": ["texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ejecutar_accion",
            "description": "Ejecuta una acción en el PC del usuario vía Python.",
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
            "description": "Envía un mensaje de WhatsApp a un contacto guardado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contacto": {
                        "type": "string",
                        "description": "Nombre del contacto tal como aparece en la agenda (ej: 'mama', 'juan').",
                    },
                    "mensaje": {
                        "type": "string",
                        "description": "Texto del mensaje a enviar.",
                    },
                },
                "required": ["contacto", "mensaje"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_archivo_whatsapp",
            "description": "Envía un archivo (documento, imagen, video, etc) por WhatsApp a un contacto. SIEMPRE usa explorar_carpeta primero para obtener la ruta absoluta exacta del archivo antes de llamar esta tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contacto": {
                        "type": "string",
                        "description": "Nombre del contacto tal como aparece en la agenda.",
                    },
                    "ruta_archivo": {
                        "type": "string",
                        "description": "Ruta absoluta exacta del archivo, obtenida de explorar_carpeta. Nunca inventar la ruta.",
                    },
                },
                "required": ["contacto", "ruta_archivo"],
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


def decidir_y_actuar(texto: str, audio_q: _stdlib_queue.Queue, rec: object) -> str:
    """
    Loop multi-turn con GPT-4o-mini (máx 4 rondas).
    - explorar_carpeta devuelve resultado a GPT y continúa
    - responder_en_voz / ejecutar_accion / buscar_y_abrir_youtube finalizan
    Retorna 'conversacion' o 'accion' para que el caller decida si ofrece followup.
    """
    if _cancelado():
        return "conversacion"

    try:
        import winreg
        _key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        _descargas = winreg.QueryValueEx(_key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
        _documentos = winreg.QueryValueEx(_key, "Personal")[0]
    except Exception:
        _descargas  = os.path.join(os.path.expanduser("~"), "Downloads")
        _documentos = os.path.join(os.path.expanduser("~"), "Documents")

    rutas_contexto = (
        f"Rutas reales del sistema:\n"
        f"- Escritorio: {_get_escritorio()}\n"
        f"- Descargas: {_descargas}\n"
        f"- Documentos: {_documentos}\n"
        "Usa SIEMPRE estas rutas exactas como destino, nunca las inventes."
    )

    messages: list = [
        {"role": "system", "content": f"{_TOOLS_SYSTEM}\n\n{rutas_contexto}"},
        {"role": "user",   "content": texto},
    ]

    MAX_RONDAS = 6
    for ronda in range(MAX_RONDAS):
        if _cancelado():
            return "conversacion"

        print(f"{_ts()}[ia] GPT-4o-mini ronda {ronda + 1}...")
        try:
            resp = _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=_TOOLS,
                tool_choice="required",
                max_tokens=300,
                parallel_tool_calls=False,
            )
        except Exception as exc:
            print(f"{_ts()}[ia] error GPT: {exc}")
            hablar_edge("Hubo un error al procesar tu solicitud.")
            return "conversacion"

        if _cancelado():
            return "conversacion"

        tool_call = resp.choices[0].message.tool_calls[0]
        tool_name = tool_call.function.name
        args      = json.loads(tool_call.function.arguments)
        print(f"{_ts()}[ia] tool: {tool_name}")

        messages.append(resp.choices[0].message)

        if tool_name == "explorar_carpeta":
            carpeta  = args.get("carpeta", "")
            archivos = _listar_carpeta(carpeta)
            print(f"{_ts()}[explorar] {carpeta}: {len(archivos)} archivos")
            messages.append({
                "role":        "tool",
                "tool_call_id": tool_call.id,
                "content":     json.dumps(archivos, ensure_ascii=False),
            })
            continue

        if tool_name == "responder_en_voz":
            respuesta = args.get("texto", "")
            print(f"{_ts()}[ia] respuesta: '{respuesta}'")
            hablar_edge(respuesta)
            return "conversacion"

        if tool_name == "ejecutar_accion":
            codigo = args.get("codigo", "").strip()
            print(f"{_ts()}[ia] acción: {codigo}")
            exito = _ejecutar_con_verificacion(
                descripcion=args.get("descripcion", "Ejecutando acción"),
                codigo=codigo,
                audio_q=audio_q,
                rec=rec,
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
            _ejecutar_con_verificacion(
                descripcion=f"Buscando {query} en YouTube",
                codigo="",
                audio_q=audio_q,
                rec=rec,
                youtube_query=query,
            )
            return "accion"

        if tool_name == "enviar_whatsapp":
            from whatsapp import enviar_whatsapp  # noqa: PLC0415
            contacto   = args.get("contacto", "")
            mensaje_wa = args.get("mensaje", "")
            print(f"{_ts()}[ia] whatsapp → {contacto}: {mensaje_wa}")
            ok = enviar_whatsapp(contacto, mensaje_wa)
            if not ok:
                hablar_edge("No encontré ese contacto.")
            return "accion"

        if tool_name == "enviar_archivo_whatsapp":
            from whatsapp import enviar_archivo_whatsapp  # noqa: PLC0415
            contacto     = args.get("contacto", "")
            ruta_archivo = args.get("ruta_archivo", "")
            print(f"{_ts()}[ia] whatsapp archivo → {contacto}: {ruta_archivo}")
            ok = enviar_archivo_whatsapp(contacto, ruta_archivo)
            if not ok:
                hablar_edge("No pude enviar el archivo.")
            return "accion"

    print(f"{_ts()}[ia] agotó rondas sin acción final")
    hablar_edge("No pude completar la acción.")
    return "conversacion"


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
