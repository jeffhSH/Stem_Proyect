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


def _cancelado() -> bool:
    return _cancelar.is_set()


def _ts() -> str:
    now = datetime.now()
    return f"[{now.strftime('%H:%M:%S')}.{now.microsecond // 1000:03d}]"
