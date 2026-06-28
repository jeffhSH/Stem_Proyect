"""
Detección y caché de adaptadores de red.
Se auto-inicializa al importarse: genera config.json en el primer inicio,
carga el existente en los siguientes.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.json"

WIFI_ADAPTER:      str | None = None
BT_ADAPTER:        str | None = None
TECLA_ACTIVACION:  str       = "|"
STEM_MEDIOS_ACTIVO: bool     = True
MIC_NOMBRE:        str | None = "WH-CH520"


# ── Detección ──────────────────────────────────────────────────────────────────

def _ps(cmd: str, timeout: int = 10) -> str:
    """Ejecuta un comando PowerShell y retorna la salida en UTF-8."""
    r = subprocess.run(
        ["powershell", "-Command",
         f"[Console]::OutputEncoding = [Text.Encoding]::UTF8; {cmd}"],
        capture_output=True, timeout=timeout,
    )
    # PowerShell puede emitir BOM (EF BB BF) — decodificar con utf-8-sig lo elimina
    return r.stdout.decode("utf-8-sig", errors="replace").strip()


def _detectar_wifi() -> str | None:
    """Obtiene el nombre del adaptador WiFi via netsh (con fallback a PowerShell)."""
    try:
        r = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True, timeout=6,
        )
        # netsh usa la codificación OEM del sistema; intentamos utf-8, luego cp850, luego cp1252
        for enc in ("utf-8", "cp850", "cp1252"):
            try:
                texto = r.stdout.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            texto = r.stdout.decode("utf-8", errors="replace")

        for line in texto.splitlines():
            if re.search(r"wi.?fi|wlan|wireless|inalámbr", line, re.IGNORECASE):
                # Columnas separadas por 2+ espacios; la última es el nombre
                partes = re.split(r"\s{2,}", line.strip())
                if partes:
                    return partes[-1]
    except Exception as e:
        print(f"[config] netsh error: {e}")

    # Fallback: PowerShell Get-NetAdapter
    try:
        nombre = _ps(
            "Get-NetAdapter | Where-Object { $_.InterfaceDescription -match "
            "'Wi-Fi|Wireless|802\\.11' } | "
            "Select-Object -ExpandProperty Name -First 1"
        )
        if nombre:
            return nombre
    except Exception:
        pass

    return None


def _detectar_bluetooth() -> str | None:
    """Obtiene el nombre del adaptador Bluetooth para usar con Enable/Disable-NetAdapter."""
    # Primero buscamos en Get-NetAdapter (nombre exacto para Enable/Disable-NetAdapter)
    try:
        nombre = _ps(
            "Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'Bluetooth' } | "
            "Select-Object -ExpandProperty Name -First 1"
        )
        if nombre:
            return nombre
    except Exception:
        pass

    # Fallback: Get-PnpDevice (como especificado por el usuario)
    try:
        nombre = _ps(
            "Get-PnpDevice -Class Bluetooth | Where-Object { $_.Status -eq 'OK' } | "
            "Select-Object -ExpandProperty FriendlyName -First 1",
            timeout=12,
        )
        if nombre:
            return nombre
    except Exception as e:
        print(f"[config] PnpDevice error: {e}")

    return None


# ── Persistencia ───────────────────────────────────────────────────────────────

def _generar() -> dict:
    """Detecta adaptadores, guarda config.json y retorna el dict."""
    print("[config] primer inicio — detectando adaptadores de red...")

    wifi = _detectar_wifi()
    bt   = _detectar_bluetooth()

    if wifi:
        print(f"[config] WiFi detectado: '{wifi}'")
    else:
        print("[stem] WiFi no disponible en este equipo")

    if bt:
        print(f"[config] Bluetooth detectado: '{bt}'")
    else:
        print("[stem] Bluetooth no disponible en este equipo")

    data = {"wifi_adapter": wifi, "bluetooth_adapter": bt, "tecla_activacion": "|"}
    _CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return data


def inicializar() -> None:
    """Carga o genera config.json y actualiza WIFI_ADAPTER / BT_ADAPTER / TECLA_ACTIVACION."""
    global WIFI_ADAPTER, BT_ADAPTER, TECLA_ACTIVACION

    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = _generar()
    else:
        data = _generar()

    WIFI_ADAPTER     = data.get("wifi_adapter")
    BT_ADAPTER       = data.get("bluetooth_adapter")
    TECLA_ACTIVACION = data.get("tecla_activacion", "|")

    # Retrocompatibilidad: añade tecla_activacion si falta en un config.json previo
    if "tecla_activacion" not in data:
        data["tecla_activacion"] = "|"
        _CONFIG_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


# ── Auto-inicializar al importar ───────────────────────────────────────────────
inicializar()
