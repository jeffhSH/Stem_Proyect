import sys

from apps import iniciar_watcher, launch
from voz import MODEL_PATH, escuchar_wake_word


def main() -> None:
    model_path = sys.argv[1] if len(sys.argv) > 1 else MODEL_PATH

    iniciar_watcher()
    escuchar_wake_word(launch, model_path)


if __name__ == "__main__":
    main()
