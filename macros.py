import json
import pyautogui
from pathlib import Path
from config import STEM_MEDIOS_ACTIVO

USER_MACROS_PATH = Path(__file__).parent / "user_macros.json"

VARIANTES_MEDIOS = {
    "media_anterior":   {"anterior", "canción anterior", "regresa", "regresar"},
    "media_pausa":      {"pausa", "pausar", "dar play", "reproducir", "continuar"},
    "media_siguiente":  {"siguiente canción", "siguiente pista", "skip", "saltar"},
}

_ACCIONES = {
    "media_anterior":   lambda: pyautogui.press('prevtrack'),
    "media_pausa":      lambda: pyautogui.press('playpause'),
    "media_siguiente":  lambda: pyautogui.press('nexttrack'),
}


def ejecutar_macro_medios(texto: str) -> bool:
    if not STEM_MEDIOS_ACTIVO:
        return False
    for cmd, variantes in VARIANTES_MEDIOS.items():
        if any(v in texto for v in variantes):
            _ACCIONES[cmd]()
            print(f"[macro] {cmd}")
            return True
    return False


def cargar_variantes_usuario_macros() -> None:
    """Lee user_macros.json y fusiona sus entradas en VARIANTES_MEDIOS."""
    if not USER_MACROS_PATH.exists():
        return
    try:
        datos: dict[str, list[str]] = json.loads(
            USER_MACROS_PATH.read_text(encoding="utf-8")
        )
    except Exception as e:
        print(f"[macros] no se pudo cargar user_macros.json: {e}")
        return

    añadidas = 0
    for cmd, variantes in datos.items():
        if cmd not in VARIANTES_MEDIOS:
            continue
        for v in variantes:
            if v not in VARIANTES_MEDIOS[cmd]:
                VARIANTES_MEDIOS[cmd].add(v)
                añadidas += 1

    if añadidas:
        print(f"[macros] {añadidas} variante(s) de usuario cargadas")


def ejecutar_macro_ventana(texto: str) -> bool:
    """Macros de ventanas: minimizar, cambiar, bloquear."""
    if any(v in texto for v in {"minimizar"}):
        pyautogui.hotkey('win', 'd')
        print("[macro] minimizar")
        return True
    if any(v in texto for v in {"cambiar", "cambiar ventana"}):
        pyautogui.hotkey('alt', 'tab')
        print("[macro] cambiar_ventana")
        return True
    if any(v in texto for v in {"bloquear", "bloquea", "bloqueo"}):
        pyautogui.hotkey('win', 'l')
        print("[macro] bloquear")
        return True
    return False


def ejecutar_macro_audio(texto: str) -> bool:
    """Macros de audio: mute."""
    if any(v in texto for v in {"mutear", "mute", "silencio", "muy tea", "sin sonido", "quitar sonido"}):
        pyautogui.press('volumemute')
        print("[macro] mute")
        return True
    return False
