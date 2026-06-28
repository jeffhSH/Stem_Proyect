import re
import threading as _threading
import time as _time

from rapidfuzz import fuzz, process

# ── Constantes ─────────────────────────────────────────────────────────────────
_MODEL_PATH     = "vosk-model-small-es-0.42"
_SAMPLE_RATE    = 16000
_CHUNK          = 4000
_TIMEOUT_AJUSTE  = 10.0
_TIMEOUT_NAVEGAR = 15.0
_PASO            = 10      # porcentaje por paso

_UMBRAL_SISTEMA = 75
_UMBRAL_NORMAL  = 60
_UMBRAL_APPS    = 65      # apps: threshold fuzzy para VARIANTES y búsqueda directa en caché
_UMBRAL_SUB     = 50      # fuzzy para sub-comandos del modo ajuste

# ── Comandos y variantes ───────────────────────────────────────────────────────
VARIANTES: dict[str, set[str]] = {
    # Apps / web
    "youtube":           {"youtube"},
    "minimizar":         {"minimizar"},
    "cambiar_ventana":   {"cambiar", "cambiar ventana"},
    "whatsapp":          {"watsap", "whatsapp", "guatsap"},
    "modrinth app":      {"maincraft", "minecraft", "maincreft", "modrinth", "modrint", "mine craft"},
    "spotify":           {"spotify", "espotifai", "spotifai"},
    "vscode":            {"vscode", "viscode", "vs code", "visual studio"},
    "claude":            {"claude", "clawd", "clod"},
    "obs studio":        {"obs", "obs studio", "stream"},
    "vlc":               {"vlc", "reproductor"},
    "discord":           {"discord"},
    "libreoffice":       {"libreoffice", "libre office", "writer"},
    "google play juegos":      {"google play", "play games"},
    "postman":                 {"postman"},
    "pgadmin 4":               {"pgadmin", "pg admin", "postgres admin"},
    "proteus 8 professional":  {"proteus"},
    "mplab x ide v6.00":       {"mplab", "microchip"},
    "chrome":                  {"chrome", "google chrome"},
    "notepad++":               {"notepad", "bloc de notas"},
    "wt":                      {"terminal", "windows terminal"},
    "mspaint":                 {"paint", "pintura"},
    "snippingtool":            {"recortes", "snipping", "captura herramienta"},
    "calculator":              {"calculadora", "calculator"},
    "zoom":                    {"zoom"},
    "codeblocks":              {"codeblocks", "code blocks"},
    "postgres":                {"postgresql", "postgres"},
    "powerpoint":              {"powerpoint", "presentacion"},
    "microsoftstore":          {"tienda", "microsoft store", "store"},
    "piano 10":                {"piano"},
    "file explorer":           {"archivos", "file explorer", "explorador", "microsoft explorer"},
    # Sistema — orden importa: específico antes de genérico para evitar que
    # "apaga" (substring de apagar_pantalla) intercepte "apaga wifi" / "apaga bluetooth"
    "apagar_sistema":       {"apagar sistema", "apaga el sistema", "apagar equipo"},
    # WiFi — debe ir ANTES de apagar_pantalla ("apaga" interceptaría "apaga wifi")
    "wifi_apagar":          {"apaga wifi", "apagar wifi", "desactiva wifi",
                             "quita wifi", "wifi off"},
    "wifi_encender":        {"enciende wifi", "encender wifi", "activa wifi",
                             "prende wifi", "wifi on"},
    # Bluetooth — apagar ANTES de encender para que "apaga blutuz" no
    # colisione con los alias fonológicos en bluetooth_encender
    "bluetooth_apagar":     {"apaga bluetooth", "apagar bluetooth", "desactiva bluetooth",
                             "apaga blutuz", "apaga blutut", "apaga bluetuz",
                             "apaga bluetut", "apaga blue tu", "apaga glu tu",
                             "apaga glu glu", "bluetooth off"},
    "bluetooth_encender":   {"enciende bluetooth", "encender bluetooth", "activa bluetooth",
                             "enciende blutuz", "enciende blutut", "enciende bluetuz",
                             "activa blutuz", "bluetooth on",
                             # variantes fonológicas standalone → activa bluetooth
                             "glu glu", "blutuz", "blutut", "bluetuz", "bluetut",
                             "blue tu", "glu tu"},
    "apagar_pantalla":      {"apagar pantalla", "apaga pantalla", "pantalla off",
                             "apagar", "apaga", "paga"},
    "bloquear":        {"bloquear", "bloquea", "bloqueo"},
    "reiniciar":       {"reiniciar", "reinicia", "restart"},
    # Stem solo se cierra con "cerrar stem" o Ctrl+C
    "salir":           {"salir", "exit", "cerrar stem"},
    # ── Navegación de ventanas ────────────────────────────────────────────────
    "navegar":         {"navegar", "navega"},
    # ── Continuos — brillo ────────────────────────────────────────────────────
    "brillo_subir":    {"subir brillo", "sube brillo", "más brillo", "mas brillo",
                        "sube el brillo", "aumenta brillo"},
    "brillo_bajar":    {"bajar brillo", "baja brillo", "menos brillo",
                        "baja el brillo", "reduce brillo"},
    # ── Continuos — volumen ───────────────────────────────────────────────────
    "volumen_subir":   {"subir volumen", "sube volumen", "más volumen", "mas volumen",
                        "sube el volumen", "aumenta volumen"},
    "volumen_bajar":   {"bajar volumen", "baja volumen", "menos volumen",
                        "baja el volumen", "reduce volumen"},
    "volumen_mute":    {"mutear", "mute", "silencio", "muy tea", "sin sonido", "quitar sonido"},
    # ── Apps con fallback directo ─────────────────────────────────────────────────
    "brave":           {"brave", "bravo", "brave browser"},
}

# Mapa plano variante → comando canónico para fuzzy matching
_VARIANTES_PLANO: dict[str, str] = {
    v: cmd
    for cmd, variantes in VARIANTES.items()
    for v in variantes
}

_COMANDOS_SISTEMA   = {"apagar_pantalla", "apagar_sistema", "bloquear", "reiniciar"}
_COMANDOS_CONTINUOS = {"brillo_subir", "brillo_bajar", "volumen_subir", "volumen_bajar"}
_COMANDOS_NAVEGAR   = {"navegar"}
_COMANDOS_WIFI      = {"wifi_apagar", "wifi_encender"}
_COMANDOS_BLUETOOTH     = {"bluetooth_apagar", "bluetooth_encender"}
_COMANDOS_VENTANA       = {"cambiar_ventana", "minimizar"}
_COMANDOS_SISTEMA_AUDIO = {"volumen_mute"}
_COMANDOS_APPS = (
    set(VARIANTES.keys())
    - _COMANDOS_SISTEMA
    - _COMANDOS_CONTINUOS
    - _COMANDOS_NAVEGAR
    - _COMANDOS_WIFI
    - _COMANDOS_BLUETOOTH
    - _COMANDOS_VENTANA
    - _COMANDOS_SISTEMA_AUDIO
    - {"salir"}
)

# ── Sub-comandos del modo ajuste ───────────────────────────────────────────────
_SUBCMDS: dict[str, set[str]] = {
    # "subir" → misma dirección que el comando inicial
    "subir": {"más", "mas", "sube", "arriba", "súbele", "subele"},
    # "bajar" → dirección opuesta al comando inicial
    "bajar": {"menos", "baja", "abajo", "bájale", "bajale"},
    "salir": {"ok", "bien", "okay", "listo", "ya", "gracias", "cerrar", "salir", "terminar"},
}
_SUBCMDS_PLANO: dict[str, str] = {
    v: k for k, vs in _SUBCMDS.items() for v in vs
}

# ── Sub-comandos del modo navegación ──────────────────────────────────────────
_SUBCMDS_NAVEGAR: dict[str, set[str]] = {
    "siguiente": {"navega", "navegar", "muévete", "muevete", "siguiente", "adelante"},
    "anterior":  {"vuelve", "atrás", "atras", "regresa", "anterior"},
    "abrir":     {"allí", "alli", "para", "listo", "aquí", "aqui", "abrir"},
}
_SUBCMDS_NAVEGAR_PLANO: dict[str, str] = {
    v: k for k, vs in _SUBCMDS_NAVEGAR.items() for v in vs
}

# ── Estado del modo ajuste ─────────────────────────────────────────────────────
# "direccion": +1 (subir) o -1 (bajar) según el comando inicial
_modo: dict = {"tipo": None, "t_fin": 0.0, "direccion": 0}
_ajuste_hilo:  _threading.Thread | None = None
_navegar_hilo: _threading.Thread | None = None

# Caché del modelo Vosk (carga lazy en primer uso del modo ajuste)
_vosk_model_cache = None
_vosk_model_lock  = _threading.Lock()


# ── Vosk model (lazy, thread-safe) ────────────────────────────────────────────

def _get_vosk_model():
    global _vosk_model_cache
    if _vosk_model_cache is None:
        with _vosk_model_lock:
            if _vosk_model_cache is None:
                import vosk  # noqa: PLC0415
                vosk.SetLogLevel(-1)
                print("[ajuste] cargando modelo Vosk para modo continuo...")
                _vosk_model_cache = vosk.Model(_MODEL_PATH)
    return _vosk_model_cache


# ── Control de brillo (WMI) ────────────────────────────────────────────────────

def ajustar_brillo(delta: int) -> int:
    """Ajusta el brillo del panel integrado en delta%. Retorna nuevo valor o -1."""
    try:
        import wmi  # noqa: PLC0415
        c       = wmi.WMI(namespace="wmi")
        metodos = c.WmiMonitorBrightnessMethods()
        estado  = c.WmiMonitorBrightness()
        if not metodos or not estado:
            print("[brillo] control no disponible (monitor externo o driver sin soporte)")
            return -1
        actual = estado[0].CurrentBrightness
        nuevo  = max(0, min(100, actual + delta))
        metodos[0].WmiSetBrightness(nuevo, 0)
        print(f"[brillo] {actual}% → {nuevo}%")
        return nuevo
    except Exception as e:
        print(f"[brillo] error: {e}")
        return -1


# ── Control de volumen (pycaw) ─────────────────────────────────────────────────

def ajustar_volumen(delta: float) -> int:
    """Ajusta el volumen maestro en delta (escalar 0.0-1.0). Retorna nuevo % o -1."""
    try:
        from pycaw.pycaw import AudioUtilities  # noqa: PLC0415
        device  = AudioUtilities.GetSpeakers()
        volume  = device.EndpointVolume
        current = volume.GetMasterVolumeLevelScalar()
        nuevo   = max(0.0, min(1.0, current + delta))
        volume.SetMasterVolumeLevelScalar(nuevo, None)
        print(f"[volumen] {round(current * 100)}% → {round(nuevo * 100)}%")
        return round(nuevo * 100)
    except Exception as e:
        print(f"[volumen] error: {e}")
        return -1


# ── Ajuste directo por porcentaje ─────────────────────────────────────────────

NUMEROS_ES: dict[str, int] = {
    "cero": 0, "diez": 10, "veinte": 20, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
    "noventa": 90, "cien": 100, "cinco": 5, "quince": 15, "veinticinco": 25,
}


def extraer_porcentaje(texto: str) -> int | None:
    """Extrae un porcentaje (0-100) del texto. Dígitos tienen prioridad sobre palabras."""
    match = re.search(r'\b(\d+)\b', texto)
    if match:
        return min(100, max(0, int(match.group(1))))
    for palabra, valor in NUMEROS_ES.items():
        if palabra in texto:
            return valor
    return None


def _fijar_brillo(pct: int) -> None:
    """Fija el brillo del panel integrado directamente al porcentaje dado."""
    try:
        import wmi  # noqa: PLC0415
        c       = wmi.WMI(namespace="wmi")
        metodos = c.WmiMonitorBrightnessMethods()
        if not metodos:
            print("[brillo] control no disponible")
            return
        metodos[0].WmiSetBrightness(pct, 0)
        print(f"[brillo] fijado a {pct}%")
    except Exception as e:
        print(f"[brillo] error: {e}")


def _fijar_volumen(pct: int) -> None:
    """Fija el volumen maestro directamente al porcentaje dado."""
    try:
        from pycaw.pycaw import AudioUtilities  # noqa: PLC0415
        device  = AudioUtilities.GetSpeakers()
        volume  = device.EndpointVolume
        volume.SetMasterVolumeLevelScalar(pct / 100, None)
        print(f"[volumen] fijado a {pct}%")
    except Exception as e:
        print(f"[volumen] error: {e}")


def _aplicar_porcentaje_directo(cmd: str, pct: int) -> None:
    """Fija brillo o volumen a un porcentaje exacto sin entrar al modo iterativo."""
    tipo = "brillo" if "brillo" in cmd else "volumen"
    print(f"[{tipo}] ajuste directo → {pct}%")
    if tipo == "brillo":
        _fijar_brillo(pct)
    else:
        _fijar_volumen(pct)


# ── Detección de sub-comandos ──────────────────────────────────────────────────

def _subcmd_ajuste(text: str) -> str | None:
    """Detecta sub-comando del modo ajuste. Exacto primero, luego fuzzy al 50%."""
    for variantes in _SUBCMDS.values():
        for v in variantes:
            if v in text:
                return _SUBCMDS_PLANO[v]
    resultado = process.extractOne(text, _SUBCMDS_PLANO.keys(), scorer=fuzz.WRatio)
    if resultado and resultado[1] >= _UMBRAL_SUB:
        return _SUBCMDS_PLANO[resultado[0]]
    return None


# ── Hilo del modo ajuste ───────────────────────────────────────────────────────

def _hilo_ajuste(tipo: str) -> None:
    """Escucha sub-comandos durante _TIMEOUT_AJUSTE segundos con su propio stream."""
    import json as _json
    import queue as _queue
    import sounddevice as _sd
    import vosk as _vosk
    from voz import _INPUT_STREAM_KWARGS as _stream_kw  # noqa: PLC0415

    try:
        model = _get_vosk_model()
        rec   = _vosk.KaldiRecognizer(model, _SAMPLE_RATE)
    except Exception as e:
        print(f"[ajuste] error con modelo Vosk: {e}")
        if _modo["tipo"] == tipo:
            _modo["tipo"] = None
        return

    q: _queue.Queue = _queue.Queue()

    def _cb(indata, frames, time_info, status):
        q.put(bytes(indata))

    print(f"[{tipo}] modo ajuste — di 'más'/'menos' o 'listo' ({int(_TIMEOUT_AJUSTE)} s)")

    try:
        with _sd.RawInputStream(**_stream_kw, callback=_cb):
            while _time.time() < _modo["t_fin"]:
                try:
                    data = q.get(timeout=0.5)
                except _queue.Empty:
                    continue

                if not rec.AcceptWaveform(data):
                    continue  # ignorar parciales; solo ejecutar sub-comandos en resultado final

                texto = _json.loads(rec.Result()).get("text", "").lower().strip()
                if not texto:
                    continue

                subcmd = _subcmd_ajuste(texto)
                if not subcmd:
                    continue

                if subcmd == "salir":
                    print(f"[{tipo}] saliendo del modo ajuste")
                    break

                # "subir" → misma dirección inicial; "bajar" → dirección opuesta
                d = _modo["direccion"] if subcmd == "subir" else -_modo["direccion"]

                if tipo == "brillo":
                    ajustar_brillo(d * _PASO)
                else:
                    ajustar_volumen(d * _PASO / 100)
                _modo["t_fin"] = _time.time() + _TIMEOUT_AJUSTE  # reinicia temporizador

    except Exception as e:
        print(f"[ajuste] error en stream: {e}")
    finally:
        if _modo["tipo"] == tipo:
            _modo["tipo"] = None
        print(f"[{tipo}] modo ajuste finalizado")


# ── Activar comando continuo ───────────────────────────────────────────────────

def _activar_continuo(cmd: str) -> None:
    """Ejecuta el primer ajuste y lanza el hilo de modo continuo."""
    global _ajuste_hilo

    tipo      = "brillo" if "brillo" in cmd else "volumen"
    direccion = +1 if cmd.endswith("subir") else -1   # +1 sube, -1 baja
    print(f"[continuo] {tipo} — dirección={'⬆ subir' if direccion > 0 else '⬇ bajar'}")

    if tipo == "brillo":
        ajustar_brillo(direccion * _PASO)
    else:
        ajustar_volumen(direccion * _PASO / 100)

    # Señalizar al hilo anterior que termine y esperar (máx 1.5 s)
    if _ajuste_hilo and _ajuste_hilo.is_alive():
        _modo["t_fin"] = 0.0
        _ajuste_hilo.join(timeout=1.5)

    _modo["tipo"]      = tipo
    _modo["t_fin"]     = _time.time() + _TIMEOUT_AJUSTE
    _modo["direccion"] = direccion   # "más" continúa esta dirección; "menos" la invierte

    _ajuste_hilo = _threading.Thread(target=_hilo_ajuste, args=(tipo,), daemon=True)
    _ajuste_hilo.start()


# ── Navegación de ventanas ────────────────────────────────────────────────────

GRAMMAR_NAVEGAR = '["siguiente", "anterior", "abrir", "vuelve", "atrás", "atras", "[unk]"]'

# Conjunto de palabras válidas del grammar (para filtrar parciales)
_GRAMMAR_NAVEGAR_PALABRAS: frozenset[str] = frozenset({
    "siguiente", "anterior", "abrir", "vuelve", "atrás", "atras",
})


def _subcmd_navegar(text: str) -> str | None:
    """
    Detecta sub-comando de navegación por substring directo contra el grammar.
    Sin fuzzy — Vosk ya filtra por gramática restringida.
    """
    if "siguiente" in text:
        return "siguiente"
    if any(p in text for p in ("anterior", "vuelve", "atrás", "atras")):
        return "anterior"
    if "abrir" in text:
        return "abrir"
    return None


def _hilo_navegar() -> None:
    """Mantiene Alt presionado y navega ventanas con sub-comandos de voz por grammar."""
    import json as _json
    import queue as _queue
    import sounddevice as _sd
    import vosk as _vosk
    import pyautogui as _pyautogui
    from voz import _INPUT_STREAM_KWARGS as _stream_kw  # noqa: PLC0415

    try:
        model = _get_vosk_model()
        rec   = _vosk.KaldiRecognizer(model, _SAMPLE_RATE, GRAMMAR_NAVEGAR)
    except Exception as e:
        print(f"[navegar] error con modelo Vosk: {e}")
        if _modo["tipo"] == "navegar":
            _modo["tipo"] = None
        return

    q: _queue.Queue = _queue.Queue()

    def _cb(indata, frames, time_info, status):
        q.put(bytes(indata))

    _saltos = 0  # contador de teclas Tab enviadas

    def _ejecutar(cmd: str, fuente: str) -> bool:
        """Ejecuta el sub-comando. Retorna True si el hilo debe terminar."""
        nonlocal _saltos
        if cmd == "siguiente":
            _saltos += 1
            print(f"[navegar] {fuente} → SIGUIENTE (Tab #{_saltos})")
            _pyautogui.press('tab')
            _modo["t_fin"] = _time.time() + _TIMEOUT_NAVEGAR
        elif cmd == "anterior":
            _saltos += 1
            print(f"[navegar] {fuente} → ANTERIOR (Shift+Tab #{_saltos})")
            _pyautogui.keyDown('shift')
            _pyautogui.press('tab')
            _pyautogui.keyUp('shift')
            _modo["t_fin"] = _time.time() + _TIMEOUT_NAVEGAR
        elif cmd == "abrir":
            print(f"[navegar] {fuente} → ABRIR (suelta Alt)")
            return True
        return False

    _pyautogui.keyDown('alt')
    _pyautogui.press('tab')
    print(f"[navegar] Alt sostenido — di 'siguiente', 'anterior' o 'abrir' ({int(_TIMEOUT_NAVEGAR)} s)")

    try:
        with _sd.RawInputStream(**_stream_kw, callback=_cb):
            _parcial_actuado = ""
            while _time.time() < _modo["t_fin"]:
                try:
                    data = q.get(timeout=0.5)
                except _queue.Empty:
                    continue

                if rec.AcceptWaveform(data):
                    texto = _json.loads(rec.Result()).get("text", "").strip()
                    print(f"[navegar] FINAL raw='{texto}' parcial_actuado='{_parcial_actuado}'")
                    if texto and not _parcial_actuado:
                        subcmd = _subcmd_navegar(texto)
                        if subcmd and _ejecutar(subcmd, f"final('{texto}')"):
                            break
                    elif texto and _parcial_actuado:
                        print(f"[navegar] final IGNORADO (parcial '{_parcial_actuado}' ya actuó)")
                    _parcial_actuado = ""
                else:
                    parcial = _json.loads(rec.PartialResult()).get("partial", "").strip()
                    if not parcial:
                        continue
                    en_grammar = parcial in _GRAMMAR_NAVEGAR_PALABRAS
                    ya_actuado = parcial == _parcial_actuado
                    print(f"[navegar] PARCIAL raw='{parcial}' en_grammar={en_grammar} ya_actuado={ya_actuado}")
                    if en_grammar and not ya_actuado:
                        subcmd = _subcmd_navegar(parcial)
                        if subcmd:
                            _parcial_actuado = parcial
                            if _ejecutar(subcmd, f"parcial('{parcial}')"):
                                break

    except Exception as e:
        print(f"[navegar] error en stream: {e}")
    finally:
        _pyautogui.keyUp('alt')
        if _modo["tipo"] == "navegar":
            _modo["tipo"] = None
        print(f"[navegar] modo navegación finalizado — {_saltos} salto(s) enviado(s)")


def _activar_navegar() -> None:
    """Lanza el hilo de navegación de ventanas."""
    global _navegar_hilo

    # Señalizar modo ajuste activo (si lo hubiera) que termine
    if _ajuste_hilo and _ajuste_hilo.is_alive():
        _modo["t_fin"] = 0.0
        _ajuste_hilo.join(timeout=1.5)

    _modo["tipo"]  = "navegar"
    _modo["t_fin"] = _time.time() + _TIMEOUT_NAVEGAR

    _navegar_hilo = _threading.Thread(target=_hilo_navegar, daemon=True)
    _navegar_hilo.start()


# ── WiFi y Bluetooth (pendiente de implementación) ───────────────────────────

def _cmd_wifi(encender: bool) -> None:
    pass


def _cmd_bluetooth(encender: bool) -> None:
    pass


# ── Mute de volumen maestro ───────────────────────────────────────────────────

def _cmd_mute() -> None:
    try:
        from pycaw.pycaw import AudioUtilities  # noqa: PLC0415
        device = AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume
        actual = volume.GetMute()
        volume.SetMute(not actual, None)
        print(f"[mute] {'silenciado' if not actual else 'con sonido'}")
    except Exception as e:
        print(f"[mute] error: {e}")


# ── Detección de intención de apertura ────────────────────────────────────────

# Ordenadas por longitud descendente para que "abre el" tenga prioridad sobre "abre"
_PALABRAS_APERTURA: list[str] = sorted(
    ["abre", "abrir", "abre el", "abre la", "pon", "lanza", "ejecuta", "abre los",
     "nueva pestaña"],
    key=len, reverse=True,
)
_PALABRAS_NUEVA_INSTANCIA = {"nueva pestaña"}


def _detectar_intencion_apertura(texto: str) -> tuple[str | None, bool]:
    """
    Si el texto empieza con una palabra de apertura, retorna (nombre_app, forzar_nueva).
    forzar_nueva=True indica abrir nueva instancia aunque la app ya esté abierta.
    """
    for palabra in _PALABRAS_APERTURA:
        if texto.startswith(palabra) and (
            len(texto) == len(palabra) or texto[len(palabra)] == " "
        ):
            nombre = texto[len(palabra):].strip() or None
            return nombre, palabra in _PALABRAS_NUEVA_INSTANCIA
    return None, False


def _buscar_en_cache_directo(nombre: str) -> str | None:
    """Fuzzy match del nombre directamente contra las claves del caché. Retorna la clave o None."""
    if not nombre:
        return None
    try:
        from apps import _cache as _apps_cache  # noqa: PLC0415
        resultado = process.extractOne(nombre, _apps_cache.keys(), scorer=fuzz.WRatio)
        if resultado and resultado[1] >= _UMBRAL_APPS:
            clave, similitud, _ = resultado
            print(f"[apertura] '{nombre}' → '{clave}' ({similitud:.0f}%)")
            return clave
    except ImportError:
        pass
    return None


# ── Diagnóstico de caché de apps ──────────────────────────────────────────────

def _diagnosticar_launch(cmd: str) -> None:
    """Imprime si el comando está en caché y sugiere claves similares si no."""
    try:
        from apps import _cache as _apps_cache  # noqa: PLC0415
        if cmd in _apps_cache:
            print(f"[launch] '{cmd}' ✓ en caché")
        else:
            print(f"[launch] '{cmd}' ✗ no está en caché")
            similares = sorted(k for k in _apps_cache if any(p in k for p in cmd.split()))[:5]
            if similares:
                print(f"[launch]   claves similares: {similares}")
    except ImportError:
        print(f"[launch] clave buscada: '{cmd}'")


# ── Texto → comando (interfaz pública) ────────────────────────────────────────

def texto_a_comando(text: str) -> str | None:
    """Retorna el nombre canónico del primer comando detectado, o None."""

    # Modo ajuste activo: el hilo de fondo maneja sub-comandos directamente
    if _modo["tipo"] and _time.time() < _modo["t_fin"]:
        return None

    # 0. Detección de intención de apertura ("abre ...", "lanza ...", etc.)
    nombre_app, forzar_nueva = _detectar_intencion_apertura(text)
    if nombre_app:
        clave = _buscar_en_cache_directo(nombre_app)
        if clave:
            _diagnosticar_launch(clave)
            return f"nueva:{clave}" if forzar_nueva else clave
        # Sin coincidencia suficiente → continúa con lógica normal

    # 1. Coincidencia exacta por substring (orden del dict importa)
    for cmd, variantes in VARIANTES.items():
        if any(v in text for v in variantes):
            if cmd in _COMANDOS_CONTINUOS:
                print(f"[debug] texto='{text}' cmd='{cmd}'")
                pct = extraer_porcentaje(text)
                if pct is not None:
                    _aplicar_porcentaje_directo(cmd, pct)
                else:
                    _activar_continuo(cmd)
                return None
            if cmd in _COMANDOS_NAVEGAR:
                _activar_navegar()
                return None
            if cmd in _COMANDOS_WIFI:
                _cmd_wifi(cmd == "wifi_encender")
                return None
            if cmd in _COMANDOS_BLUETOOTH:
                _cmd_bluetooth(cmd == "bluetooth_encender")
                return None
            if cmd in _COMANDOS_SISTEMA_AUDIO:
                _cmd_mute()
                return None
            if cmd in _COMANDOS_APPS:
                _diagnosticar_launch(cmd)
            return cmd

    # 1.5 Macros de medios (antes del fuzzy para no competir con él)
    from macros import ejecutar_macro_medios  # noqa: PLC0415
    if ejecutar_macro_medios(text):
        return None

    # 2. Fuzzy matching contra todas las variantes conocidas
    resultado = process.extractOne(text, _VARIANTES_PLANO.keys(), scorer=fuzz.WRatio)
    if resultado is None:
        return None

    variante, similitud, _ = resultado
    cmd    = _VARIANTES_PLANO[variante]
    umbral = (_UMBRAL_SISTEMA if cmd in _COMANDOS_SISTEMA
              else _UMBRAL_APPS if cmd in _COMANDOS_APPS
              else _UMBRAL_NORMAL)

    if similitud >= 80:
        if cmd in _COMANDOS_CONTINUOS:
            print(f"[debug] texto='{text}' cmd='{cmd}'")
            pct = extraer_porcentaje(text)
            if pct is not None:
                _aplicar_porcentaje_directo(cmd, pct)
            else:
                _activar_continuo(cmd)
            return None
        if cmd in _COMANDOS_NAVEGAR:
            _activar_navegar()
            return None
        if cmd in _COMANDOS_WIFI:
            _cmd_wifi(cmd == "wifi_encender")
            return None
        if cmd in _COMANDOS_BLUETOOTH:
            _cmd_bluetooth(cmd == "bluetooth_encender")
            return None
        if cmd in _COMANDOS_SISTEMA_AUDIO:
            _cmd_mute()
            return None
        if cmd in _COMANDOS_APPS:
            _diagnosticar_launch(cmd)
        return cmd

    if similitud >= umbral:
        print(f"[fuzzy] entendí: '{text}' → ejecutando: '{cmd}'")
        if cmd in _COMANDOS_CONTINUOS:
            print(f"[debug] texto='{text}' cmd='{cmd}'")
            pct = extraer_porcentaje(text)
            if pct is not None:
                _aplicar_porcentaje_directo(cmd, pct)
            else:
                _activar_continuo(cmd)
            return None
        if cmd in _COMANDOS_NAVEGAR:
            _activar_navegar()
            return None
        if cmd in _COMANDOS_WIFI:
            _cmd_wifi(cmd == "wifi_encender")
            return None
        if cmd in _COMANDOS_BLUETOOTH:
            _cmd_bluetooth(cmd == "bluetooth_encender")
            return None
        if cmd in _COMANDOS_SISTEMA_AUDIO:
            _cmd_mute()
            return None
        if cmd in _COMANDOS_APPS:
            _diagnosticar_launch(cmd)
        return cmd

    return None


# ── Variantes de usuario ───────────────────────────────────────────────────────

def cargar_variantes_usuario() -> None:
    """
    Lee user_variants.json y fusiona sus entradas en VARIANTES y _VARIANTES_PLANO.
    Solo añade variantes para comandos que ya existen en VARIANTES.
    """
    import json as _json
    from pathlib import Path as _Path

    ruta = _Path(__file__).parent / "user_variants.json"
    if not ruta.exists():
        return

    try:
        datos: dict[str, list[str]] = _json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[variantes] no se pudo cargar user_variants.json: {e}")
        return

    añadidas = 0
    for cmd, variantes in datos.items():
        if cmd not in VARIANTES:
            continue
        for v in variantes:
            if v not in VARIANTES[cmd]:
                VARIANTES[cmd].add(v)
                _VARIANTES_PLANO[v] = cmd
                añadidas += 1

    if añadidas:
        print(f"[variantes] {añadidas} variante(s) de usuario cargadas")
