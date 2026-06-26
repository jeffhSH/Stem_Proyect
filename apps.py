import ctypes
import json
import os
import subprocess
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

MENSAJES = {
    "youtube":   "Abriendo YouTube...",
    "minimizar": "Minimizando ventana...",
    "skip":      "Cambiando ventana (Alt+Tab)...",
    "whatsapp":  "Abriendo WhatsApp...",
    "minecraft": "Abriendo Modrinth/Minecraft...",
    "spotify":   "Abriendo Spotify...",
    "vscode":    "Abriendo VS Code...",
    "claude":    "Abriendo Claude...",
    "salir":     "Cerrando programa.",
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


_ACCIONES_SISTEMA = {
    "minimizar": _minimizar,
    "skip":      _skip,
}


# ── Escaneo y caché ────────────────────────────────────────────────────────────

def escanear(debug: bool = False) -> dict[str, str]:
    """Recorre los .lnk y .exe de los directorios configurados y devuelve {nombre_canonico: valor}."""
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
    for nombre, terminos in APP_BUSQUEDA.items():
        matched = False
        for f in candidatos:
            if any(t in f.stem.lower() for t in terminos):
                valor = _resolver_lnk(f) if f.suffix.lower() == ".lnk" else str(f)
                resultado[nombre.lower()] = valor
                if debug:
                    print(f"[debug] '{nombre}' → {f.name}  =>  {valor}")
                matched = True
                break
        if debug and not matched:
            print(f"[debug] '{nombre}' — sin coincidencia (terminos: {terminos})")

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
            os.startfile(valor)
            return False
        # El path en caché ya no existe; cae al fallback

    if nombre in STORE_APPS:
        os.startfile(f"shell:AppsFolder\\{STORE_APPS[nombre]}")
        return False

    if nombre in URL_FALLBACK:
        _abrir_url(URL_FALLBACK[nombre])
        return False

    print(f"[apps] '{nombre}' no encontrado.")
    return False
