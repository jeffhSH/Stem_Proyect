import sys

from apps import iniciar_watcher, launch
from comandos import VARIANTES, texto_a_comando
from voz import MODEL_PATH, escuchar


def _on_texto(text: str) -> bool:
    cmd = texto_a_comando(text)
    return launch(cmd) if cmd else False


def _on_parcial(text: str) -> tuple[bool, bool]:
    cmd = texto_a_comando(text)
    if cmd:
        return True, launch(cmd)
    return False, False


def main() -> None:
    model_path = sys.argv[1] if len(sys.argv) > 1 else MODEL_PATH

    print("Comandos disponibles:")
    for cmd, variantes in VARIANTES.items():
        print(f"  {', '.join(sorted(variantes)):<45} → {cmd}")
    print()

    iniciar_watcher()
    escuchar(_on_texto, _on_parcial, model_path)


if __name__ == "__main__":
    main()
