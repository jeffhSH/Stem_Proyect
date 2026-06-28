import pyautogui
from config import STEM_MEDIOS_ACTIVO

VARIANTES_MEDIOS = {
    "media_anterior":   {"anterior", "canción anterior", "regresa", "regresar"},
    "media_pausa":      {"pausa", "pausar", "dar play", "reproducir", "continuar"},
    "media_siguiente":  {"siguiente canción", "siguiente pista", "skip", "saltar"},
}

_ACCIONES = {
    "media_anterior":   lambda: pyautogui.press('f9'),
    "media_pausa":      lambda: pyautogui.press('f10'),
    "media_siguiente":  lambda: pyautogui.press('f11'),
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
