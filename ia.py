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

import ia_state
from ia_state import (
    DEBUG_TEXTO,
    _barge_in,
    _cancelado,
    _cancelar,
    _client,
    _hud_set_estado,
    _hud_set_tx,
    _interrumpir_tts,
    _ts,
    _tts_reproduciendo,
    _turno_id,
    toggle_modo_entrada,
)

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
                                        "explorar_carpeta / responder_en_voz / "
                                        "esperar_archivo_y_confirmar"
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
            "name": "esperar_archivo_y_confirmar",
            "description": (
                "Espera a que aparezca un archivo en una carpeta (ej. un instalador "
                "descargándose) y, al encontrarlo, pide confirmación al usuario vía HUD "
                "antes de ejecutarlo. Usar cuando el usuario pide una acción condicionada "
                "a que algo termine de descargarse. Lanza el monitoreo en segundo plano "
                "y devuelve de inmediato."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre_archivo": {
                        "type": "string",
                        "description": "Nombre o patrón del archivo a buscar, ej. 'OllamaSetup.exe'",
                    },
                    "carpeta": {
                        "type": "string",
                        "enum": ["descargas", "escritorio", "documentos"],
                        "description": "Carpeta donde buscar el archivo",
                    },
                    "comando_al_confirmar": {
                        "type": "string",
                        "description": (
                            "Código Python a ejecutar si el usuario confirma, "
                            "ej. \"subprocess.Popen([ruta_archivo])\". "
                            "Usar {ruta} como placeholder para la ruta real del archivo encontrado."
                        ),
                    },
                },
                "required": ["nombre_archivo", "carpeta", "comando_al_confirmar"],
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


from tts import hablar_edge, _reproducir_oracion  # noqa: E402
from stt import (  # noqa: E402
    _capturar_audio,
    _capturar_y_transcribir,
    _drenar_audio,
    _escuchar_confirmacion,
    _escuchar_confirmacion_debug,
    precargar_whisper,
    transcribir_whisper,
)
from orchestrator import Orchestrator  # noqa: E402


# ── esperar_archivo_y_confirmar worker ────────────────────────────────────────

def _esperar_archivo_worker(
    nombre_archivo: str,
    carpeta: str,
    comando: str,
    intervalo: float = 7.0,
    umbral: int = 60,
) -> None:
    """Corre en hilo daemon. Busca el archivo, pide confirmación HUD, ejecuta si acepta."""
    from rapidfuzz import fuzz  # noqa: PLC0415
    from hud_control import preguntar_hud, esperar_respuesta_hud  # noqa: PLC0415

    carpeta_map = {
        "descargas":  _get_descargas,
        "escritorio": _get_escritorio,
        "documentos": _get_documentos,
    }
    resolver = carpeta_map.get(carpeta, _get_descargas)

    print(f"{_ts()}[archivo-watcher] iniciando — buscando '{nombre_archivo}' en {carpeta}")
    while True:
        try:
            carpeta_path = resolver()
            try:
                entradas = list(os.scandir(carpeta_path))
            except OSError:
                entradas = []

            mejor_score = 0
            mejor_ruta  = ""
            for e in entradas:
                if not e.is_file():
                    continue
                score = fuzz.partial_ratio(nombre_archivo.lower(), e.name.lower())
                if score > mejor_score:
                    mejor_score = score
                    mejor_ruta  = e.path

            if mejor_score >= umbral:
                print(f"{_ts()}[archivo-watcher] encontrado '{Path(mejor_ruta).name}' (score={mejor_score})")
                pregunta_id = preguntar_hud(
                    f"Encontré {Path(mejor_ruta).name}. ¿Lo ejecuto?",
                    {"1": "Sí", "2": "No"},
                )
                print(f"{_ts()}[archivo-watcher] pregunta enviada al HUD (id={pregunta_id})")
                respuesta = esperar_respuesta_hud(pregunta_id)
                print(f"{_ts()}[archivo-watcher] respuesta HUD: {respuesta!r}")
                if respuesta == "1":
                    codigo_real = comando.replace("{ruta}", repr(mejor_ruta))
                    print(f"{_ts()}[archivo-watcher] ejecutando: {codigo_real}")
                    try:
                        exec(codigo_real, _build_exec_ns())  # noqa: S102
                        print(f"{_ts()}[archivo-watcher] ejecución completada")
                    except Exception as exc:
                        print(f"{_ts()}[archivo-watcher] error al ejecutar: {exc}")
                else:
                    print(f"{_ts()}[archivo-watcher] usuario rechazó la ejecución")
                return
        except Exception as exc:
            print(f"{_ts()}[archivo-watcher] error en ciclo: {exc}")

        time.sleep(intervalo)


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

    if ia_state.DEBUG_TEXTO:
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


def _escuchar_interrupcion(audio_q: _stdlib_queue.Queue, rec: object, mi_turno_id: int) -> None:
    """Escucha Vosk mientras TTS reproduce. Activa barge-in al detectar 2+ palabras."""
    while _turno_id[0] == mi_turno_id and not _barge_in.is_set():
        if not _tts_reproduciendo.is_set():
            time.sleep(0.05)
            continue
        try:
            data = audio_q.get(timeout=0.1)
        except _stdlib_queue.Empty:
            continue
        if rec.AcceptWaveform(data):
            texto_oido = json.loads(rec.Result()).get("text", "").strip()
        else:
            texto_oido = json.loads(rec.PartialResult()).get("partial", "").strip()
        if len(texto_oido.split()) >= 2:
            print(f"{_ts()}[ia] [INTERRUPCIÓN] barge-in detectado: '{texto_oido}'")
            _interrumpir_tts.set()
            _barge_in.set()
            return


def _ejecutar_turno(
    messages: list,
    texto: str,
    audio_q: _stdlib_queue.Queue,
    rec: object,
) -> str:
    """Loop interno de rondas GPT para un turno de la sesión.
    Modifica messages in-place. Devuelve 'continuar', 'cerrar', 'barge_in' o 'error'."""
    orchestrator: Orchestrator | None = None
    _bloqueos_gk = 0
    _tool_choice: object = {"type": "function", "function": {"name": "declarar_plan"}}
    MAX_RONDAS = 12

    _barge_in.clear()
    _turno_id[0] += 1
    threading.Thread(
        target=_escuchar_interrupcion,
        args=(audio_q, rec, _turno_id[0]),
        daemon=True,
    ).start()

    for ronda in range(MAX_RONDAS):
        if _cancelado():
            if orchestrator:
                orchestrator.reporte_final()
            return "error"

        if _barge_in.is_set():
            print(f"{_ts()}[ia] [INTERRUPCIÓN] turno abortado por barge-in")
            if orchestrator:
                orchestrator.reporte_final()
            return "barge_in"

        _hud_set_estado("procesando", ronda=ronda + 1, max_rondas=MAX_RONDAS)
        print(f"{_ts()}[ia] GPT-4o-mini ronda {ronda + 1}...")
        def _safe_msg_preview(m) -> dict:
            role    = m.get("role")    if isinstance(m, dict) else getattr(m, "role",    "?")
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            return {"role": role, "content": str(content or "")[:80]}
        print(f"{_ts()}[diag] messages[-3:] = {[_safe_msg_preview(m) for m in messages[-3:]]}")
        try:
            resp = _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=_TOOLS,
                tool_choice=_tool_choice,
                max_tokens=1000,
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
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            print(f"{_ts()}[ia] JSON truncado en tool_call ({tool_name}): {e}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"error": "respuesta truncada, reintentá con menos contenido"}, ensure_ascii=False),
            })
            continue
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

        if tool_name == "esperar_archivo_y_confirmar":
            nombre  = args.get("nombre_archivo", "")
            carpeta = args.get("carpeta", "descargas")
            comando = args.get("comando_al_confirmar", "")
            print(f"{_ts()}[ia] esperar_archivo: '{nombre}' en {carpeta}")
            threading.Thread(
                target=_esperar_archivo_worker,
                args=(nombre, carpeta, comando),
                daemon=True,
            ).start()
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(
                    {"exito": True, "mensaje": "Monitoreo iniciado en segundo plano"},
                    ensure_ascii=False,
                ),
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

        if ia_state.DEBUG_TEXTO:
            print(f"{_ts()}[ia] di tu pregunta (debug)...")
            try:
                texto = input("[DEBUG] escribe tu pregunta: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not texto:
                break
            _hud_set_tx(texto)
        else:
            print(f"{_ts()}[diag] inicio captura input turno {turno + 1}")
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
