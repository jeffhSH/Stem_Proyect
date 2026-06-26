VARIANTES: dict[str, set[str]] = {
    "youtube":   {"youtube"},
    "minimizar": {"minimizar"},
    "skip":      {"skip"},
    "whatsapp":  {"watsap", "whatsapp", "guatsap"},
    "minecraft": {"maincraft", "minecraft", "maincreft", "modrinth", "modrins", "modrint"},
    "spotify":   {"spotify", "espotifai", "spotifai"},
    "vscode":    {"vscode", "viscode", "vs code", "visual studio"},
    "claude":    {"claude", "clawd", "clod"},
    "salir":     {"salir", "exit", "cerrar", "apagar"},
}


def texto_a_comando(text: str) -> str | None:
    """Retorna el nombre canónico del primer comando detectado, o None."""
    for cmd, variantes in VARIANTES.items():
        if any(v in text for v in variantes):
            return cmd
    return None
