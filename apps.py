import ctypes
import json
import os
import queue as _queue
import subprocess
import time
import webbrowser
from pathlib import Path
from threading import Lock

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ── Directorios del menú inicio ────────────────────────────────────────────────
_HOME = Path.home()
STARTMENU_DIRS = [
    Path(r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs"),
    _HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    _HOME / "AppData" / "Local" / "Microsoft" / "WindowsApps",
    _HOME / "Desktop",
    Path(r"C:\Program Files"),
    Path(r"C:\Program Files (x86)"),
]
CACHE_PATH = Path(__file__).parent / "apps_cache.json"

# ── Brave ──────────────────────────────────────────────────────────────────────
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

# ── Apps que se abren como URL (no tienen .lnk en el menú inicio) ──────────────
URLS_WEB = {
    "youtube": "https://www.youtube.com",
}

# ── Apps de Microsoft Store (AUMID) ───────────────────────────────────────────
STORE_APPS = {
    "whatsapp": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
}

# ── Fallback web si la app no está instalada ───────────────────────────────────
URL_FALLBACK = {
    "whatsapp": "https://web.whatsapp.com",
}

# ── Términos de búsqueda en .lnk del menú inicio → nombre canónico ────────────
APP_BUSQUEDA = {
    "whatsapp":  ["whatsapp"],
    "spotify":   ["spotify"],
    "claude":    ["claude"],
    "vscode":    ["visual studio code", "vscode"],
    "minecraft": ["modrinth", "minecraft launcher"],
}

_VOSK_MODEL_PATH = "vosk-model-small-es-0.42"

MENSAJES = {
    "youtube":         "Abriendo YouTube...",
    "minimizar":       "Minimizando ventana...",
    "cambiar_ventana": "Cambiando ventana (Alt+Tab)...",
    "whatsapp":        "Abriendo WhatsApp...",
    "minecraft":       "Abriendo Modrinth/Minecraft...",
    "spotify":         "Abriendo Spotify...",
    "vscode":          "Abriendo VS Code...",
    "claude":          "Abriendo Claude...",
    "apagar_pantalla": "Apagando pantalla...",
    "bloquear":        "Bloqueando equipo...",
    "reiniciar":       "Preparando reinicio...",
    "apagar_sistema":  "Preparando apagado del sistema...",
    "salir":           "Cerrando programa.",
}

_URL_PREFIX = "url:"

# ── Caché en memoria ───────────────────────────────────────────────────────────
_cache: dict[str, str] = {}
_cache_lock = Lock()


# ── Resolución de .lnk ────────────────────────────────────────────────────────

def _resolver_lnk(lnk: Path) -> str:
    """
    Devuelve 'url:https://...' si el destino del .lnk es una URL,
    o la ruta del .lnk si apunta a un ejecutable.
    Requiere pywin32; si no está disponible devuelve la ruta sin resolver.
    """
    try:
        import pythoncom          # noqa: PLC0415
        import win32com.client    # noqa: PLC0415
        pythoncom.CoInitialize()
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortCut(str(lnk))
        target = (sc.Targetpath or "").strip()
        args   = (sc.Arguments  or "").strip()

        if target.startswith(("http://", "https://")):
            return _URL_PREFIX + target

        for token in args.split():
            if token.startswith(("http://", "https://")):
                return _URL_PREFIX + token
    except Exception:
        pass

    return str(lnk)


def _nombre_canonico(stem: str) -> str | None:
    """Devuelve el nombre canónico si el stem coincide con algún término de APP_BUSQUEDA."""
    stem_lower = stem.lower()
    for nombre, terminos in APP_BUSQUEDA.items():
        if any(t in stem_lower for t in terminos):
            return nombre
    return None


# ── Acciones del sistema ───────────────────────────────────────────────────────

def _abrir_url(url: str) -> None:
    brave = Path(BRAVE_PATH)
    if brave.exists():
        subprocess.Popen([str(brave), url])
    else:
        webbrowser.open(url)


def _minimizar() -> None:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE


def _skip() -> None:
    VK_MENU = 0x12; VK_TAB = 0x09; KEYEVENTF_KEYUP = 0x0002
    kb = ctypes.windll.user32.keybd_event
    kb(VK_MENU, 0, 0, 0); kb(VK_TAB, 0, 0, 0)
    kb(VK_TAB, 0, KEYEVENTF_KEYUP, 0); kb(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def _apagar_pantalla() -> None:
    ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)


def _bloquear() -> None:
    ctypes.windll.user32.LockWorkStation()


def _esperar_confirmacion_voz() -> bool:
    """Escucha 6 s con Vosk esperando 'confirmar'. Retorna True si se confirma."""
    import json as _json
    import vosk
    import sounddevice as sd
    import voz as _voz

    try:
        vosk.SetLogLevel(-1)
        model = vosk.Model(_VOSK_MODEL_PATH)
        rec   = vosk.KaldiRecognizer(model, 16000)
    except Exception as e:
        print(f"[confirmación] error cargando modelo: {e}")
        return False

    print("[sistema] di 'confirmar' en los próximos 6 segundos...")
    t_fin = time.time() + 6.0
    q: _queue.Queue = _queue.Queue()

    def _cb(indata, frames, time_info, status):
        q.put(bytes(indata))

    with sd.RawInputStream(samplerate=_voz.SAMPLE_RATE, blocksize=8000, latency="high",
                           dtype="int16", channels=1, device=_voz._WASAPI_DEVICE, callback=_cb):
        while time.time() < t_fin:
            try:
                data = q.get(timeout=max(0.1, t_fin - time.time()))
            except _queue.Empty:
                break
            if rec.AcceptWaveform(data):
                texto = _json.loads(rec.Result()).get("text", "").lower()
            else:
                texto = _json.loads(rec.PartialResult()).get("partial", "").lower()
            if "confirmar" in texto:
                return True

    print("[sistema] confirmación no recibida, cancelando.")
    return False


def _reiniciar() -> None:
    if _esperar_confirmacion_voz():
        subprocess.run(["shutdown", "/r", "/t", "0"])


def _apagar_sistema() -> None:
    if _esperar_confirmacion_voz():
        subprocess.run(["shutdown", "/s", "/t", "0"])


# ── Foco de ventana existente ─────────────────────────────────────────────────

def _proceso_corriendo_y_enfocar(nombre: str, ruta: str) -> bool:
    """
    Verifica si la app ya está corriendo y enfoca su ventana.
    Retorna True si encontró y enfocó la ventana; False para que el llamador la abra.
    """
    try:
        import psutil           # noqa: PLC0415
        import pygetwindow as gw  # noqa: PLC0415

        exe_stem = Path(ruta).stem.lower() if ruta else nombre.lower()
        candidatos = {exe_stem, nombre.lower()}

        corriendo = False
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"].lower().split(".")[0] in candidatos:
                    corriendo = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if not corriendo:
            return False

        nombre_lower = nombre.lower()
        ventanas = [
            v for v in gw.getAllWindows()
            if v.title and (nombre_lower in v.title.lower() or exe_stem in v.title.lower())
        ]
        if not ventanas:
            return False

        v = ventanas[0]
        if v.isMinimized:
            v.restore()
        v.activate()
        print(f"[apps] '{nombre}' ya abierto — enfocando ventana")
        return True

    except ImportError as e:
        print(f"[focus] módulo no disponible: {e}")
    except Exception as e:
        print(f"[focus] error al enfocar: {e}")
    return False


_ACCIONES_SISTEMA = {
    "minimizar":       _minimizar,
    "cambiar_ventana": _skip,
    "apagar_pantalla": _apagar_pantalla,
    "bloquear":        _bloquear,
    "reiniciar":       _reiniciar,
    "apagar_sistema":  _apagar_sistema,
}


# ── Escaneo y caché ────────────────────────────────────────────────────────────

def escanear(debug: bool = False) -> dict[str, str]:
    """Recorre los .lnk y .exe de los directorios configurados y devuelve {stem_lower: ruta}."""
    candidatos: list[Path] = []
    for d in STARTMENU_DIRS:
        if d.exists():
            candidatos.extend(d.rglob("*.lnk"))
            candidatos.extend(d.rglob("*.exe"))

    if debug:
        print(f"\n[debug] {len(candidatos)} archivos encontrados:")
        for f in sorted(candidatos):
            print(f"  {f}")
        print()

    resultado: dict[str, str] = {}
    for f in candidatos:
        clave = f.stem.lower()
        if clave not in resultado:
            valor = _resolver_lnk(f) if f.suffix.lower() == ".lnk" else str(f)
            resultado[clave] = valor
            if debug:
                print(f"[debug] '{clave}' → {valor}")

    if debug:
        print()
    return resultado


def _guardar_cache(datos: dict[str, str]) -> None:
    CACHE_PATH.write_text(
        json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _cargar_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _agregar_a_cache(path: Path) -> None:
    """Añade o actualiza un solo archivo en el caché sin reescanear todo."""
    nombre = _nombre_canonico(path.stem)
    if not nombre:
        return

    valor = _resolver_lnk(path) if path.suffix.lower() == ".lnk" else str(path)

    with _cache_lock:
        _cache[nombre.lower()] = valor
        _guardar_cache(_cache)

    print(f"[apps] +{nombre} → {valor}")


def _eliminar_de_cache(path: Path) -> None:
    """Elimina del caché la entrada cuyo stem coincide con el archivo borrado."""
    nombre = _nombre_canonico(path.stem)
    if not nombre:
        return
    with _cache_lock:
        if nombre in _cache:
            del _cache[nombre]
            _guardar_cache(_cache)
            print(f"[apps] -{nombre} eliminado del caché")


# ── Watcher ────────────────────────────────────────────────────────────────────

class _CacheHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            path = Path(event.src_path)
            if path.suffix.lower() in (".lnk", ".exe"):
                _agregar_a_cache(path)

    def on_deleted(self, event):
        if not event.is_directory:
            path = Path(event.src_path)
            if path.suffix.lower() in (".lnk", ".exe"):
                _eliminar_de_cache(path)


def iniciar_watcher() -> None:
    """Carga la caché desde disco (o escanea si no existe) y arranca el watcher."""
    global _cache
    cached = _cargar_cache()
    if cached:
        _cache = cached
    else:
        _cache = escanear()
        _guardar_cache(_cache)
    print(f"[apps] {len(_cache)} apps en caché")
    for nombre, valor in sorted(_cache.items()):
        print(f"  {nombre}: {valor}")

    handler  = _CacheHandler()
    observer = Observer()
    for d in STARTMENU_DIRS:
        if d.exists():
            observer.schedule(handler, str(d), recursive=True)
    observer.daemon = True
    observer.start()


# ── Lanzador principal ─────────────────────────────────────────────────────────

def launch(nombre: str) -> bool:
    """Ejecuta el comando. Retorna True si el programa debe cerrarse."""
    forzar_nueva = nombre.startswith("nueva:")
    if forzar_nueva:
        nombre = nombre[len("nueva:"):]
    nombre = nombre.lower()
    print(f"\n{MENSAJES.get(nombre, f'Ejecutando {nombre}...')}\n")

    if nombre == "salir":
        return True

    if nombre in _ACCIONES_SISTEMA:
        _ACCIONES_SISTEMA[nombre]()
        return False

    if nombre in URLS_WEB:
        _abrir_url(URLS_WEB[nombre])
        return False

    valor = _cache.get(nombre)
    if valor:
        if valor.startswith(_URL_PREFIX):
            webbrowser.open(valor[len(_URL_PREFIX):])
            return False
        if Path(valor).exists():
            if not forzar_nueva and _proceso_corriendo_y_enfocar(nombre, valor):
                return False
            os.startfile(valor)
            return False
        # El path en caché ya no existe; cae al fallback

    # Fallback directo para Brave si no está en caché
    if nombre == "brave":
        brave = Path(BRAVE_PATH)
        if brave.exists():
            if not forzar_nueva and _proceso_corriendo_y_enfocar("brave", str(brave)):
                return False
            subprocess.Popen([str(brave)])
            return False

    if nombre in STORE_APPS:
        os.startfile(f"shell:AppsFolder\\{STORE_APPS[nombre]}")
        return False

    if nombre in URL_FALLBACK:
        _abrir_url(URL_FALLBACK[nombre])
        return False

    print(f"[apps] '{nombre}' no encontrado.")
    return False
