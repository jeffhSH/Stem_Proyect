from rapidfuzz import fuzz, process

VARIANTES: dict[str, set[str]] = {
    "youtube":         {"youtube"},
    "minimizar":       {"minimizar"},
    "cambiar_ventana": {"cambiar", "cambiar ventana"},
    "whatsapp":        {"watsap", "whatsapp", "guatsap"},
    "modrinth app":    {"maincraft", "minecraft", "maincreft", "modrinth", "modrins", "modrint", "mine craft"},
    "spotify":         {"spotify", "espotifai", "spotifai"},
    "vscode":          {"vscode", "viscode", "vs code", "visual studio"},
    "claude":          {"claude", "clawd", "clod"},
    # Sistema — apagar_sistema ANTES de apagar_pantalla para que el substring
    # "apagar" no gane sobre la frase más específica "apagar sistema"
    "apagar_sistema":  {"apagar sistema", "apaga el sistema", "apagar equipo"},
    "apagar_pantalla": {"apagar pantalla", "apaga pantalla", "pantalla off",
                        "apagar", "apaga", "paga"},
    "bloquear":        {"bloquear", "bloquea", "bloqueo"},
    "reiniciar":       {"reiniciar", "reinicia", "restart"},
    # Stem solo se cierra con "cerrar stem" o Ctrl+C
    "salir":           {"salir", "exit", "cerrar stem"},
}

# Mapa plano variante → comando canónico para fuzzy matching
_VARIANTES_PLANO: dict[str, str] = {
    v: cmd
    for cmd, variantes in VARIANTES.items()
    for v in variantes
}

# Comandos peligrosos: requieren mayor similitud para activarse por fuzzy
_COMANDOS_SISTEMA = {"apagar_pantalla", "apagar_sistema", "bloquear", "reiniciar"}
_UMBRAL_SISTEMA   = 75   # mínimo para comandos del sistema
_UMBRAL_NORMAL    = 60   # mínimo para el resto


def texto_a_comando(text: str) -> str | None:
    """Retorna el nombre canónico del primer comando detectado, o None."""
    # 1. Coincidencia exacta por substring (orden del dict importa)
    for cmd, variantes in VARIANTES.items():
        if any(v in text for v in variantes):
            return cmd

    # 2. Fuzzy matching contra todas las variantes conocidas
    resultado = process.extractOne(text, _VARIANTES_PLANO.keys(), scorer=fuzz.WRatio)
    if resultado is None:
        return None

    variante, similitud, _ = resultado
    cmd = _VARIANTES_PLANO[variante]

    umbral = _UMBRAL_SISTEMA if cmd in _COMANDOS_SISTEMA else _UMBRAL_NORMAL

    if similitud >= 80:
        return cmd
    if similitud >= umbral:
        print(f"[fuzzy] entendí: '{text}' → ejecutando: '{cmd}'")
        return cmd
    return None
