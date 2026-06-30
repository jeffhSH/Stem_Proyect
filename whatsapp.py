import json
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path

import pyautogui

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BRAVE_PATH      = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
BRAVE_USER_DATA = r"C:\Users\jeffh\AppData\Local\BraveSoftware\Brave-Browser\Stem_WA"
BRAVE_PROFILE   = "Default"
CHROMEDRIVER    = Path(__file__).parent / "chromedriver.exe"
CONTACTOS_PATH  = Path(__file__).parent / "contactos.json"
DEBUG_PORT      = 9222

_driver = None


def _buscar_numero(contacto: str, contactos: dict) -> str | None:
    norm = {k.lower(): v for k, v in contactos.items()}
    clave = contacto.lower()
    if clave in norm:
        return norm[clave]
    return next((v for k, v in norm.items() if k in clave or clave in k), None)


def _puerto_activo() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", DEBUG_PORT), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _lanzar_brave():
    if _puerto_activo():
        print("[wa] Brave ya activo")
        return
    print("[wa] Lanzando Brave...")
    subprocess.Popen([
        BRAVE_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={BRAVE_USER_DATA}",
        f"--profile-directory={BRAVE_PROFILE}",
        "--no-first-run", "--no-default-browser-check",
    ])
    for i in range(20):
        if _puerto_activo():
            print(f"[wa] Puerto activo tras {i*0.5:.1f}s")
            return
        time.sleep(0.5)
    raise RuntimeError("[wa] Brave no levantó el puerto en 10s")


def _get_driver() -> webdriver.Chrome:
    global _driver
    if _driver is not None:
        try:
            _ = _driver.current_url
            return _driver
        except Exception:
            _driver = None

    _lanzar_brave()

    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    _driver = webdriver.Chrome(service=Service(str(CHROMEDRIVER)), options=opts)
    print(f"[wa] Selenium conectado. Ventanas: {len(_driver.window_handles)}")
    return _driver


def _enviar_uno(contacto: str, numero: str, mensaje: str, driver) -> bool:
    if "web.whatsapp.com" in driver.current_url:
        print(f"[wa] Usando buscador interno → {contacto}")
        try:
            buscador = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, '//input[@data-tab="3"]'))
            )
            buscador.click()
            buscador.clear()
            buscador.send_keys(numero)
            time.sleep(1.5)
            buscador.send_keys(Keys.ENTER)
            time.sleep(1)

            cuadro = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//div[@id="main"]//div[@contenteditable="true" and @role="textbox"]')
                )
            )
            cuadro.send_keys(mensaje)
            time.sleep(0.5)
            cuadro.send_keys(Keys.ENTER)
            time.sleep(1)
            print(f"[wa] ✓ enviado a '{contacto}' (sin recarga)")
            return True

        except Exception as e:
            print(f"[wa] buscador interno falló ({e}) → fallback URL")

    print(f"[wa] Cargando WhatsApp Web → {contacto}")
    url = f"https://web.whatsapp.com/send?phone={numero}&text={urllib.parse.quote(mensaje)}"
    driver.get(url)
    cuadro = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located(
            (By.XPATH, '//div[@contenteditable="true" and @role="textbox"]')
        )
    )
    time.sleep(1.5)
    cuadro.send_keys(Keys.ENTER)
    time.sleep(1)
    print(f"[wa] ✓ enviado a '{contacto}'")
    return True


def enviar_whatsapp(envios: list[dict]) -> bool:
    try:
        contactos = json.loads(CONTACTOS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[wa] error leyendo contactos.json: {e}")
        return False

    try:
        driver = _get_driver()
    except Exception as e:
        print(f"[wa] error obteniendo driver: {e}")
        return False

    exitosos: list[str] = []
    fallidos: list[str] = []
    for item in envios:
        nombre  = item.get("contacto", "")
        mensaje = item.get("mensaje", "")
        numero  = _buscar_numero(nombre, contactos)
        if not numero:
            print(f"[wa] contacto '{nombre}' no encontrado")
            fallidos.append(nombre)
            continue
        try:
            if _enviar_uno(nombre, numero, mensaje, driver):
                exitosos.append(nombre)
            else:
                fallidos.append(nombre)
        except Exception as e:
            print(f"[wa] error enviando a '{nombre}': {e}")
            fallidos.append(nombre)

    if fallidos:
        print(f"[wa] enviados: {exitosos} | fallidos: {fallidos}")
    return len(fallidos) == 0


def enviar_archivo_whatsapp(contacto: str, ruta_archivo: str) -> bool:
    try:
        contactos = json.loads(CONTACTOS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[wa] error leyendo contactos.json: {e}")
        return False

    numero = _buscar_numero(contacto, contactos)
    if not numero:
        print(f"[wa] contacto '{contacto}' no encontrado")
        return False

    ruta = Path(ruta_archivo).resolve()
    if not ruta.exists():
        print(f"[wa] archivo no encontrado: {ruta_archivo}")
        return False

    try:
        driver = _get_driver()

        if "web.whatsapp.com" not in driver.current_url:
            print(f"[wa] abriendo chat de '{contacto}'...")
            driver.get(f"https://web.whatsapp.com/send?phone={numero}")
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//div[@id="main"]//div[@contenteditable="true" and @role="textbox"]')
                )
            )
        else:
            print(f"[wa] usando buscador interno → {contacto}")
            buscador = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, '//input[@data-tab="3"]'))
            )
            buscador.click()
            buscador.clear()
            buscador.send_keys(numero)
            time.sleep(1.5)
            buscador.send_keys(Keys.ENTER)
            time.sleep(1)

        try:
            boton_adjuntar = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, '//button[@aria-label="Attach"]'))
            )
            boton_adjuntar.click()
        except Exception as e:
            print(f"[wa] no se encontró el botón de adjuntar: {e}")
            return False

        time.sleep(0.5)

        try:
            opcion_documento = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//button[@aria-label="Document" and @role="menuitem"]')
                )
            )
            opcion_documento.click()
        except Exception as e:
            print(f"[wa] no se encontró la opción Document: {e}")
            return False

        time.sleep(0.5)

        try:
            input_file = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//input[@type="file" and @accept="*"]')
                )
            )
        except Exception as e:
            print(f"[wa] no se encontró el input de archivo: {e}")
            return False

        input_file.send_keys(str(ruta))

        time.sleep(2)
        try:
            pyautogui.press('esc')
        except Exception:
            pass
        time.sleep(1)

        boton_enviar = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//span[@data-icon="wds-ic-send-filled"]')
            )
        )
        boton_enviar.click()
        time.sleep(1.5)

        print(f"[wa] ✓ archivo enviado a '{contacto}': {ruta_archivo}")
        return True

    except Exception as e:
        print(f"[wa] error enviando archivo: {e}")
        return False
