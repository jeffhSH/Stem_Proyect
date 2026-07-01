import socket, subprocess, time, urllib.parse
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

BRAVE_PATH      = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
BRAVE_USER_DATA = r"C:\Users\jeffh\AppData\Local\BraveSoftware\Brave-Browser\Stem_WA"
BRAVE_PROFILE   = "Default"
CHROMEDRIVER    = Path(__file__).parent / "chromedriver.exe"
DEBUG_PORT      = 9222

_driver = None


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


def enviar_whatsapp(contacto: str, numero: str, mensaje: str) -> bool:
    try:
        driver = _get_driver()

        # Si WhatsApp Web ya está cargado, usar buscador interno sin recargar
        if "web.whatsapp.com" in driver.current_url:
            print(f"[wa] Usando buscador interno → {contacto}")
            try:
                buscador = WebDriverWait(driver, 8).until(
                    EC.element_to_be_clickable((By.XPATH, '//input[@data-tab="3"]'))
                )
                buscador.click()
                buscador.clear()
                buscador.send_keys(numero)
                time.sleep(1.5)  # esperar resultados
                buscador.send_keys(Keys.ENTER)
                time.sleep(1)

                # Input del chat (panel principal)
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

        # Fallback: navegar con URL completa
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

    except Exception as e:
        print(f"[wa] error: {e}")
        return False


if __name__ == "__main__":
    print("iniciando...")
    try:
        resultado = enviar_whatsapp("prueba", "50360261040", "prueba-1")
        print(f"resultado: {resultado}")

        input("Presiona Enter para enviar segundo mensaje...")
        resultado = enviar_whatsapp("prueba", "50360261040", "prueba-2")
        print(f"resultado: {resultado}")

    except Exception as e:
        import traceback
        traceback.print_exc()

    input("Presiona Enter para cerrar...")