import os
import threading
import time
from pathlib import Path

from ia_state import _ts

from .filesystem import _build_exec_ns, _get_descargas, _get_documentos, _get_escritorio

SCHEMA = {
    "type": "function",
    "function": {
        "name": "esperar_archivo_y_confirmar",
        "description": (
            "Espera a que aparezca un archivo en una carpeta (ej. un instalador "
            "descargándose) y, al encontrarlo, pide confirmación al usuario vía HUD "
            "antes de ejecutarlo. Usar cuando el usuario pide una acción condicionada "
            "a que algo termine de descargarse. Lanza el monitoreo en segundo plano "
            "y devuelve de inmediato."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nombre_archivo": {
                    "type": "string",
                    "description": "Nombre o patrón del archivo a buscar, ej. 'OllamaSetup.exe'",
                },
                "carpeta": {
                    "type": "string",
                    "enum": ["descargas", "escritorio", "documentos"],
                    "description": "Carpeta donde buscar el archivo",
                },
                "comando_al_confirmar": {
                    "type": "string",
                    "description": (
                        "Código Python a ejecutar si el usuario confirma, "
                        "ej. \"subprocess.Popen([ruta_archivo])\". "
                        "Usar {ruta} como placeholder para la ruta real del archivo encontrado."
                    ),
                },
            },
            "required": ["nombre_archivo", "carpeta", "comando_al_confirmar"],
        },
    },
}


def _esperar_archivo_worker(
    nombre_archivo: str,
    carpeta: str,
    comando: str,
    intervalo: float = 7.0,
    umbral: int = 60,
) -> None:
    """Corre en hilo daemon. Busca el archivo, pide confirmación HUD, ejecuta si acepta."""
    from rapidfuzz import fuzz  # noqa: PLC0415
    from hud_control import preguntar_hud, esperar_respuesta_hud  # noqa: PLC0415

    carpeta_map = {
        "descargas":  _get_descargas,
        "escritorio": _get_escritorio,
        "documentos": _get_documentos,
    }
    resolver = carpeta_map.get(carpeta, _get_descargas)

    print(f"{_ts()}[archivo-watcher] iniciando — buscando '{nombre_archivo}' en {carpeta}")
    while True:
        try:
            carpeta_path = resolver()
            try:
                entradas = list(os.scandir(carpeta_path))
            except OSError:
                entradas = []

            mejor_score = 0
            mejor_ruta  = ""
            for e in entradas:
                if not e.is_file():
                    continue
                score = fuzz.partial_ratio(nombre_archivo.lower(), e.name.lower())
                if score > mejor_score:
                    mejor_score = score
                    mejor_ruta  = e.path

            if mejor_score >= umbral:
                print(f"{_ts()}[archivo-watcher] encontrado '{Path(mejor_ruta).name}' (score={mejor_score})")
                pregunta_id = preguntar_hud(
                    f"Encontré {Path(mejor_ruta).name}. ¿Lo ejecuto?",
                    {"1": "Sí", "2": "No"},
                )
                print(f"{_ts()}[archivo-watcher] pregunta enviada al HUD (id={pregunta_id})")
                respuesta = esperar_respuesta_hud(pregunta_id)
                print(f"{_ts()}[archivo-watcher] respuesta HUD: {respuesta!r}")
                if respuesta == "1":
                    codigo_real = comando.replace("{ruta}", repr(mejor_ruta))
                    print(f"{_ts()}[archivo-watcher] ejecutando: {codigo_real}")
                    try:
                        exec(codigo_real, _build_exec_ns())  # noqa: S102
                        print(f"{_ts()}[archivo-watcher] ejecución completada")
                    except Exception as exc:
                        print(f"{_ts()}[archivo-watcher] error al ejecutar: {exc}")
                else:
                    print(f"{_ts()}[archivo-watcher] usuario rechazó la ejecución")
                return
        except Exception as exc:
            print(f"{_ts()}[archivo-watcher] error en ciclo: {exc}")

        time.sleep(intervalo)


def handle(args: dict, ctx) -> dict:
    nombre  = args.get("nombre_archivo", "")
    carpeta = args.get("carpeta", "descargas")
    comando = args.get("comando_al_confirmar", "")
    print(f"{_ts()}[ia] esperar_archivo: '{nombre}' en {carpeta}")
    threading.Thread(
        target=_esperar_archivo_worker,
        args=(nombre, carpeta, comando),
        daemon=True,
    ).start()
    return {"exito": True, "mensaje": "Monitoreo iniciado en segundo plano"}
