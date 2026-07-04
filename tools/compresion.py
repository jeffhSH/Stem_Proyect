import os
import zipfile

from ia_state import _ts

from .filesystem import _get_descargas, _get_documentos, _get_escritorio

_DESTINOS = {
    "escritorio": _get_escritorio,
    "documentos": _get_documentos,
    "descargas":  _get_descargas,
}

SCHEMA_COMPRIMIR_ARCHIVOS = {
    "type": "function",
    "function": {
        "name": "comprimir_archivos",
        "description": (
            "Comprime uno o varios archivos en un .zip. Usa explorar_carpeta primero "
            "para obtener rutas absolutas si el usuario no las da."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "archivos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Rutas absolutas de los archivos a comprimir.",
                },
                "nombre_zip": {
                    "type": "string",
                    "description": (
                        "Nombre del .zip resultante, sin ruta (ej: 'backup.zip'). "
                        "Si no se especifica, usar 'archivos_comprimidos.zip'."
                    ),
                },
                "destino": {
                    "type": "string",
                    "enum": ["escritorio", "documentos", "descargas"],
                    "description": "Carpeta donde guardar el .zip. Default: escritorio.",
                },
            },
            "required": ["archivos"],
        },
    },
}

SCHEMA_DESCOMPRIMIR_ARCHIVO = {
    "type": "function",
    "function": {
        "name": "descomprimir_archivo",
        "description": "Descomprime un archivo .zip en una carpeta destino.",
        "parameters": {
            "type": "object",
            "properties": {
                "archivo_zip": {
                    "type": "string",
                    "description": "Ruta absoluta del .zip a descomprimir.",
                },
                "destino": {
                    "type": "string",
                    "enum": ["escritorio", "documentos", "descargas"],
                    "description": "Carpeta donde extraer. Default: misma carpeta del zip.",
                },
            },
            "required": ["archivo_zip"],
        },
    },
}


def _resolver_destino(nombre: str | None, default_getter) -> str:
    if nombre and nombre in _DESTINOS:
        return _DESTINOS[nombre]()
    return default_getter()


def _es_entrada_segura(nombre: str) -> bool:
    """Rechaza entradas de zip con rutas absolutas o '..' (protección zip slip)."""
    normalizado = nombre.replace("\\", "/")
    return not (normalizado.startswith("/") or ".." in normalizado.split("/"))


def handle_comprimir_archivos(args: dict, ctx) -> dict:
    archivos = args.get("archivos", [])
    nombre_zip = (args.get("nombre_zip") or "archivos_comprimidos.zip").strip()
    if not nombre_zip.lower().endswith(".zip"):
        nombre_zip += ".zip"

    carpeta_destino = _resolver_destino(args.get("destino"), _get_escritorio)
    ruta_zip = os.path.join(carpeta_destino, nombre_zip)

    incluidos: list[str] = []
    faltantes: list[str] = []

    with zipfile.ZipFile(ruta_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for archivo in archivos:
            if not os.path.isfile(archivo):
                print(f"{_ts()}[compresion] archivo no encontrado, se excluye: {archivo}")
                faltantes.append(archivo)
                continue
            zf.write(archivo, arcname=os.path.basename(archivo))
            print(f"{_ts()}[compresion] agregado al zip: {archivo}")
            incluidos.append(archivo)

    print(f"{_ts()}[compresion] zip creado: {ruta_zip} ({len(incluidos)} archivo(s))")
    return {
        "exito": len(incluidos) > 0,
        "ruta_zip": ruta_zip,
        "archivos_incluidos": len(incluidos),
        "archivos_faltantes": faltantes,
    }


def handle_descomprimir_archivo(args: dict, ctx) -> dict:
    archivo_zip = args.get("archivo_zip", "")

    if not os.path.isfile(archivo_zip):
        print(f"{_ts()}[compresion] .zip no encontrado: {archivo_zip}")
        return {"exito": False, "error": f"no se encontró el archivo: {archivo_zip}"}

    default_getter = lambda: os.path.dirname(archivo_zip)  # noqa: E731
    carpeta_destino = _resolver_destino(args.get("destino"), default_getter)

    try:
        with zipfile.ZipFile(archivo_zip, "r") as zf:
            entradas = zf.namelist()
            inseguras = [n for n in entradas if not _es_entrada_segura(n)]
            if inseguras:
                print(f"{_ts()}[compresion] rechazado por rutas inseguras en el zip: {inseguras}")
                return {"exito": False, "error": "el .zip contiene rutas inseguras (zip slip)"}

            zf.extractall(carpeta_destino)
            for nombre in entradas:
                print(f"{_ts()}[compresion] extraído: {nombre}")
            total_extraidos = len(entradas)
    except zipfile.BadZipFile as exc:
        print(f"{_ts()}[compresion] error al leer .zip: {exc}")
        return {"exito": False, "error": f"archivo .zip inválido: {exc}"}

    print(f"{_ts()}[compresion] descompresión completa en: {carpeta_destino} ({total_extraidos} archivo(s))")
    return {
        "exito": True,
        "carpeta_destino": carpeta_destino,
        "archivos_extraidos": total_extraidos,
    }
