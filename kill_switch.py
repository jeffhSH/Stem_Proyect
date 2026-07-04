"""Kill switch global: 'apágate' corta la sesión activa desde cualquier punto,
sin pasar por GPT ni por el Orchestrator. Reutiliza el mismo threading.Event que
ya usa ESC (_cancelar en ia_state.py), así que toda la plomería existente de
_cancelado() (revisada entre rondas, en confirmar_con_usuario, etc.) propaga el
corte automáticamente una vez activado."""
from rapidfuzz import fuzz

from ia_state import _cancelar, _ts

_APAGATE_THRESHOLD = 85
_FRASE_APAGATE = "apagate"


def _es_apagate(texto: str, threshold: int = _APAGATE_THRESHOLD) -> bool:
    """True si texto matchea 'apágate' con fuzzy ratio >= threshold.
    Normaliza acentos/mayúsculas con stt._normalizar_respuesta (import local para
    evitar ciclo de imports con stt.py) para que 'apágate' y 'apagate' matcheen igual."""
    if not texto:
        return False
    from stt import _normalizar_respuesta  # noqa: PLC0415
    return fuzz.ratio(_normalizar_respuesta(texto), _FRASE_APAGATE) >= threshold


def _activar_kill_switch(origen: str) -> None:
    """Corte duro: loggea y activa _cancelar. No reproduce nada por sí sola —
    cada punto de llamada decide qué decir y cómo retornar en su contexto."""
    print(f"{_ts()}[kill_switch] 'apágate' detectado en {origen} — cortando sesión")
    _cancelar.set()
