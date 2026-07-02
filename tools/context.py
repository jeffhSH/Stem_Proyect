from dataclasses import dataclass
from typing import Callable

import queue as _stdlib_queue

from ia_state import _hud_set_estado, _hud_set_tx


@dataclass
class ToolContext:
    """Contexto compartido pasado a cada handler de tools/.

    Evita que los handlers dependan de `messages` completo o de ia.py:
    solo reciben lo que necesitan (audio, orchestrator, hud setters).
    """
    audio_q: _stdlib_queue.Queue | None = None
    rec: object = None
    orchestrator: object = None
    hud_set_estado: Callable = _hud_set_estado
    hud_set_tx: Callable = _hud_set_tx
