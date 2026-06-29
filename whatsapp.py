import ctypes
import json
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
BRAVE_USER_DATA = r"C:\Users\jeffh\AppData\Local\BraveSoftware\Brave-Browser\User Data"
BRAVE_PROFILE = "Profile 1"
CHROMEDRIVER = Path(__file__).parent / "chromedriver.exe"
CONTACTOS_PATH = Path(__file__).parent / "contactos.json"
DEBUG_PORT = 9222


def iniciar_brave_debug() -> bool:
    """Lanza Brave con remote debugging si no está ya corriendo en el puerto."""
    try:
        s = socket.create_connection(("127.0.0.1", DEBUG_PORT), timeout=1)
        s.close()
        print("[whatsapp] Brave debug ya activo")
        return True
    except OSError:
        pass

    print("[whatsapp] lanzando Brave con remote debugging...")
    subprocess.Popen([
        BRAVE_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={BRAVE_USER_DATA}",
        f"--profile-directory={BRAVE_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
    ])
    time.sleep(3)
    return True


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

    iniciar_brave_debug()

    texto_encoded = urllib.parse.quote(mensaje)
    url = f"https://web.whatsapp.com/send?phone={numero}&text={texto_encoded}"

    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")

    driver = None
    try:
        service = Service(executable_path=str(CHROMEDRIVER))
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(url)

        cuadro = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//div[@contenteditable="true"][@data-tab="10"]')
            )
        )
        time.sleep(1)
        cuadro.send_keys(Keys.ENTER)
        time.sleep(1)

        hwnd = ctypes.windll.user32.FindWindowW(None, "WhatsApp")
        if not hwnd:
            hwnd = ctypes.windll.user32.FindWindowW(None, "Brave")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)

        print(f"[whatsapp] enviado ✓ a '{contacto}'")
        return True

    except Exception as e:
        print(f"[whatsapp] error: {e}")
        return False

    finally:
        if driver:
            try:
                driver.close()
            except Exception:
                pass
