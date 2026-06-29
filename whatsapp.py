import ctypes
import json
import os
import time
from pathlib import Path

import pyautogui

CONTACTOS_PATH = Path(__file__).parent / "contactos.json"
ASSETS = Path(__file__).parent / "assets"


def enviar_whatsapp(contacto: str, mensaje: str) -> bool:
    try:
        contactos = json.loads(CONTACTOS_PATH.read_text(encoding="utf-8"))
    except Exception:
        print("[whatsapp] error leyendo contactos.json")
        return False

    numero = contactos.get(contacto.lower())
    if not numero:
        print(f"[whatsapp] contacto '{contacto}' no encontrado")
        return False

    os.startfile(f"whatsapp://send?phone={numero}&text={mensaje}")
    time.sleep(3)

    try:
        boton = pyautogui.locateOnScreen(
            str(ASSETS / "boton_enviar_wa.png"),
            confidence=0.7,
        )
        if boton:
            pyautogui.click(pyautogui.center(boton))
            print("[whatsapp] enviado ✓")
        else:
            print("[whatsapp] botón no encontrado")
            return False
    except Exception as e:
        print(f"[whatsapp] error: {e}")
        return False

    time.sleep(0.5)

    hwnd = ctypes.windll.user32.FindWindowW(None, "WhatsApp")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE

    return True
