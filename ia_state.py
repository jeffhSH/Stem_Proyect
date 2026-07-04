import os
import threading
from datetime import datetime
from pathlib import Path

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
_interrumpir_tts    = threading.Event()
_tts_reproduciendo  = threading.Event()
_barge_in           = threading.Event()
_turno_id           = [0]
_cancelar           = threading.Event()

# ── Cartesia TTS ───────────────────────────────────────────────────────────────
_CARTESIA_VOICE_ID = "2fc4f1ec-bfd0-46f1-8e6d-d4279eaaf838"
_CARTESIA_MODEL    = "sonic-3.5"

# CARTESIA_API_KEY es la key primaria; CARTESIA_API_KEYS_BACKUP es una lista separada
# por comas que se usa por rotación cuando la key activa se agota (cuota/rate limit).
_CARTESIA_KEYS = [
    k.strip()
    for k in (
        [os.getenv("CARTESIA_API_KEY", "")]
        + os.getenv("CARTESIA_API_KEYS_BACKUP", "").split(",")
    )
    if k.strip()
]
_cartesia_key_index = 0

try:
    from cartesia import Cartesia as _CartesiaClient
except ImportError:
    _CartesiaClient = None


def _construir_cartesia_client():
    """Instancia el cliente Cartesia con la API key en _cartesia_key_index."""
    global _cartesia_client
    if _CartesiaClient is None or not _CARTESIA_KEYS:
        _cartesia_client = None
        return None
    _cartesia_client = _CartesiaClient(api_key=_CARTESIA_KEYS[_cartesia_key_index])
    return _cartesia_client


def _rotar_cartesia_client():
    """Avanza a la siguiente API key de Cartesia (round-robin) y reconstruye el cliente.
    Se llama cuando la key activa falla (agotada/rate limit). Retorna None si no hay
    más de una key configurada."""
    global _cartesia_key_index
    if len(_CARTESIA_KEYS) <= 1:
        return None
    _cartesia_key_index = (_cartesia_key_index + 1) % len(_CARTESIA_KEYS)
    return _construir_cartesia_client()


_cartesia_client = _construir_cartesia_client()

VOICE = "es-MX-JorgeNeural"

DEBUG_TEXTO = os.getenv("STEM_DEBUG_TEXTO", "1") == "1"  # default True (modo texto)


def toggle_modo_entrada() -> None:
    global DEBUG_TEXTO
    DEBUG_TEXTO = not DEBUG_TEXTO
    modo = "TEXTO (debug)" if DEBUG_TEXTO else "VOZ"
    print(f"[stem] modo de entrada → {modo}")


def _cancelado() -> bool:
    return _cancelar.is_set()


def _ts() -> str:
    now = datetime.now()
    return f"[{now.strftime('%H:%M:%S')}.{now.microsecond // 1000:03d}]"
