import msvcrt
import sys
import threading
import time

from apps import iniciar_watcher, launch
from comandos import cargar_variantes_usuario
from macros import cargar_variantes_usuario_macros
from voz import MODEL_PATH, escuchar_wake_word, pausar, reanudar, activar_ia
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

    threading.Thread(target=_listener_entrenamiento, daemon=True).start()

    escuchar_wake_word(launch, model_path)


if __name__ == "__main__":
    main()
