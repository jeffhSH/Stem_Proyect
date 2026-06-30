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
from pathlib import Path

_STATE_FILE = Path(tempfile.gettempdir()) / "stem_hud_state.json"
_PID_FILE   = Path(tempfile.gettempdir()) / "stem_hud.pid"
_HUD_SCRIPT = Path(__file__).parent / "hud_window.py"

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
    """Escritura atómica: escribe en .tmp y reemplaza el destino."""
    tmp = _STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_STATE_FILE)


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
