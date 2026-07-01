"""
API pública del HUD visual de Stem.

set_estado / set_transcripcion escriben un JSON de estado en el directorio
temp del sistema usando escritura atómica (tmp + rename) para que hud_window.py
pueda leerlo sin races. Sin dependencias de UI — seguro importar desde
cualquier módulo del pipeline de audio.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

_STATE_FILE    = Path(tempfile.gettempdir()) / "stem_hud_state.json"
_PID_FILE      = Path(tempfile.gettempdir()) / "stem_hud.pid"
_HUD_SCRIPT    = Path(__file__).parent / "hud_window.py"
_RESPONSE_FILE = Path(tempfile.gettempdir()) / "stem_hud_respuesta.json"

_DEFAULTS: dict = {
    "estado":        "idle",
    "transcripcion": "",
    "ronda":         0,
    "max_rondas":    6,
}


def _read() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULTS)


def _write(data: dict) -> None:
    """Escritura atómica: escribe en .tmp y reemplaza el destino. No fatal si falla."""
    try:
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STATE_FILE)
    except OSError:
        pass


def set_estado(estado: str, *, ronda: int = 0, max_rondas: int = 6) -> None:
    """Actualiza el estado del HUD. Valores válidos: 'idle', 'procesando', 'hablando'."""
    s = _read()
    s["estado"]     = estado
    s["ronda"]      = ronda
    s["max_rondas"] = max_rondas
    if estado == "idle":
        s["transcripcion"] = ""
    _write(s)


def set_transcripcion(texto: str) -> None:
    """Muestra el texto transcripto en el cuadro del HUD (estado procesando)."""
    s = _read()
    s["transcripcion"] = texto
    _write(s)


def lanzar_hud() -> None:
    """Lanza hud_window.py como proceso independiente. No bloquea."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc  = subprocess.Popen([sys.executable, str(_HUD_SCRIPT)], creationflags=flags)
    _PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    _write(dict(_DEFAULTS))


def cerrar_hud() -> None:
    """Termina el proceso del HUD si está corriendo."""
    try:
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _hud_corriendo() -> bool:
    try:
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def preguntar_hud(texto: str, opciones: dict[str, str]) -> str:
    """Publica una pregunta en el HUD y devuelve el pregunta_id (UUID str).
    Si el HUD no está corriendo, lo lanza primero."""
    pregunta_id = str(uuid.uuid4())
    if not _hud_corriendo():
        lanzar_hud()
    s = _read()
    s["pregunta"] = {"texto": texto, "opciones": opciones, "id": pregunta_id}
    _write(s)
    return pregunta_id


def esperar_respuesta_hud(pregunta_id: str, timeout: float = 120.0) -> str | None:
    """Polla _RESPONSE_FILE cada 300ms esperando la respuesta del usuario.
    Devuelve '1', '2' o None en timeout. Limpia estado al terminar."""
    t_fin = time.time() + timeout
    while time.time() < t_fin:
        try:
            data = json.loads(_RESPONSE_FILE.read_text(encoding="utf-8"))
            if data.get("id") == pregunta_id:
                s = _read()
                s.pop("pregunta", None)
                _write(s)
                try:
                    _RESPONSE_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
                return data.get("respuesta")
        except Exception:
            pass
        time.sleep(0.3)
    # timeout — limpiar pregunta del HUD
    s = _read()
    s.pop("pregunta", None)
    _write(s)
    return None


def responder_pregunta_hud(pregunta_id: str, respuesta: str) -> None:
    """Escritura atómica a _RESPONSE_FILE. Llamada desde hud_window.py al clickear botón."""
    try:
        tmp = _RESPONSE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"id": pregunta_id, "respuesta": respuesta}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(_RESPONSE_FILE)
    except OSError:
        pass
