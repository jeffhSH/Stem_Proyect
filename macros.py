import pyautogui
from pathlib import Path

MACROS_PATH = Path(__file__).parent / "macros.json"

# Macros de Spotify
MACROS = {
    "spotify": {
        "pausa":          lambda: pyautogui.press('space'),
        "siguiente":      lambda: pyautogui.hotkey('ctrl', 'right'),
        "anterior":       lambda: pyautogui.hotkey('ctrl', 'left'),
        "volumen subir":  lambda: pyautogui.hotkey('ctrl', 'up'),
        "volumen bajar":  lambda: pyautogui.hotkey('ctrl', 'down'),
    }
}

# Variantes fonológicas de macros
VARIANTES_MACROS = {
    "pausa":         {"pausa", "pausar", "stop", "detener"},
    "siguiente":     {"siguiente", "next", "cambia", "salta"},
    "anterior":      {"anterior", "vuelve", "regresa", "atrás"},
    "volumen subir": {"sube volumen", "más volumen"},
    "volumen bajar": {"baja volumen", "menos volumen"},
}


def ejecutar_macro(app_activa: str, comando: str) -> bool:
    """Ejecuta una macro si existe para la app activa. Retorna True si ejecutó."""
    if app_activa not in MACROS:
        return False
    for macro, variantes in VARIANTES_MACROS.items():
        if comando in variantes and macro in MACROS[app_activa]:
            MACROS[app_activa][macro]()
            print(f"[macro] {app_activa} -> {macro}")
            return True
    return False
