import msvcrt
import sys
import threading
import time

import keyboard

from apps import iniciar_watcher, launch
from comandos import cargar_variantes_usuario
from macros import cargar_variantes_usuario_macros
from ia import precargar_whisper, _cancelar, toggle_modo_entrada, DEBUG_TEXTO
from voz import MODEL_PATH, escuchar_wake_word, pausar, reanudar, activar_ia
from whatsapp import _lanzar_brave
from hud_control import lanzar_hud, cerrar_hud
import training


def _listener_entrenamiento() -> None:
    """
    Hilo daemon: monitorea la consola y activa el modo entrenamiento (T) o IA (I).
    Usa msvcrt para no interferir con input() durante el entrenamiento.
    """
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in (b"t", b"T"):
                pausar()
                training.iniciar()
                reanudar()
            elif ch in (b"i", b"I"):
                activar_ia()
                print("[ia] modo inteligente activado — habla cuando estés listo...")
        time.sleep(0.05)


def main() -> None:
    model_path = sys.argv[1] if len(sys.argv) > 1 else MODEL_PATH

    cargar_variantes_usuario()
    cargar_variantes_usuario_macros()
    iniciar_watcher()
    precargar_whisper()

    def _watch_esc():
        while True:
            keyboard.wait('esc')
            _cancelar.set()
            print("[stem] ESC — cancelando...")

    def _watch_modo_entrada():
        while True:
            keyboard.wait('F2')
            toggle_modo_entrada()

    threading.Thread(target=_lanzar_brave, daemon=True).start()
    threading.Thread(target=_listener_entrenamiento, daemon=True).start()
    threading.Thread(target=_watch_esc, daemon=True).start()
    threading.Thread(target=_watch_modo_entrada, daemon=True).start()
    lanzar_hud()

    modo_inicial = "TEXTO (debug)" if DEBUG_TEXTO else "VOZ"
    print(f"[stem] modo de entrada: {modo_inicial} — F2 para cambiar")

    try:
        escuchar_wake_word(launch, model_path)
    finally:
        cerrar_hud()


if __name__ == "__main__":
    main()
