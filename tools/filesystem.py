import base64
import io
import json
import os
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import ia_state
from ia_state import _cancelado, _client, _ts
from stt import _escuchar_confirmacion, _escuchar_confirmacion_debug
from tts import _reproducir_oracion

from .youtube import _abrir_en_brave, _buscar_youtube

SCHEMA_EJECUTAR_ACCION = {
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
}

SCHEMA_EXPLORAR_CARPETA = {
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
}


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


def _pedir_accion_gpt(texto: str, tools_system: str, tools_schema: list) -> dict | None:
    """
    Llama GPT-4o-mini forzando tool ejecutar_accion.
    Retorna dict con 'descripcion' y 'codigo', o None si falla.
    """
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": tools_system},
                {"role": "user",   "content": texto},
            ],
            tools=tools_schema,
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


_LOG_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
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
    audio_q,
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
        return _abrir_en_brave(youtube_query)

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


def handle_ejecutar_accion(args: dict, ctx) -> dict:
    codigo = args.get("codigo", "").strip()
    print(f"{_ts()}[ia] acción: {codigo}")
    exito = _ejecutar_silencioso(
        descripcion=args.get("descripcion", "Ejecutando acción"),
        codigo=codigo,
    )
    return {"exito": exito}


def handle_explorar_carpeta(args: dict, ctx) -> dict:
    carpeta  = args.get("carpeta", "")
    archivos = _listar_carpeta(carpeta)
    total = len(archivos)
    truncado = total > 40
    if truncado:
        archivos = archivos[:40]
    print(f"{_ts()}[explorar] {carpeta}: {len(archivos)} archivos" + (f" (truncado de {total})" if truncado else ""))
    return {"archivos": archivos, "total_archivos": total, "truncado": truncado}
