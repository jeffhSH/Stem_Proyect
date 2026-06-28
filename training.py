"""Modo entrenamiento de Stem: graba variantes fonológicas y las persiste."""
import json
import threading
from pathlib import Path

import sounddevice as sd
import vosk
from pynput import keyboard as _kb

from comandos import VARIANTES, _VARIANTES_PLANO

USER_VARIANTS_PATH = Path(__file__).parent / "user_variants.json"
_CHUNK = 4000   # igual que voz.CHUNK


def iniciar() -> None:
    """Muestra el menú de entrenamiento y graba variantes para el comando elegido."""
    import voz

    model = voz._modelo_activo
    if model is None:
        print("[entrenamiento] modelo Vosk no disponible todavía")
        return

    cmds = list(VARIANTES.keys())

    print("\n" + "═" * 56)
    print("  MODO ENTRENAMIENTO — Stem")
    print("═" * 56)
    for i, cmd in enumerate(cmds, 1):
        variantes_str = ", ".join(sorted(VARIANTES[cmd]))
        if len(variantes_str) > 42:
            variantes_str = variantes_str[:39] + "..."
        print(f"  {i:3}. {cmd:<26} {variantes_str}")
    print()

    try:
        sel = input("Número de comando (0 para cancelar): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return

    if not sel.isdigit() or int(sel) == 0:
        print("Cancelado.")
        return

    idx = int(sel) - 1
    if not (0 <= idx < len(cmds)):
        print("Número fuera de rango.")
        return

    cmd = cmds[idx]

    while True:
        try:
            reps_str = input(f"Repeticiones para '{cmd}' (2-10): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            return
        if reps_str.isdigit() and 2 <= int(reps_str) <= 10:
            repeticiones = int(reps_str)
            break
        print("Ingresá un número entre 2 y 10.")

    print(f"\n  Variantes actuales: {sorted(VARIANTES[cmd])}")
    print(f"  Mantené Enter presionado mientras hablás, soltá para guardar")
    print(f"  ESC para salir del entrenamiento\n")

    # ── Estado compartido entre callbacks ────────────────────────────────────
    frames: list       = []
    _grabando          = threading.Event()
    _rep_done          = threading.Event()
    _esc               = threading.Event()

    def on_press(key):
        if key == _kb.Key.esc:
            _esc.set()
            _rep_done.set()
        elif key == _kb.Key.enter and not _grabando.is_set() and not _esc.is_set():
            frames.clear()
            _grabando.set()
            print("  Grabando...", end=" ", flush=True)

    def on_release(key):
        if key == _kb.Key.enter and _grabando.is_set():
            _grabando.clear()
            _rep_done.set()

    def _audio_cb(indata, frame_count, time_info, status):
        if _grabando.is_set():
            frames.append(bytes(indata))

    # ── Bucle de repeticiones ────────────────────────────────────────────────
    nuevas: set[str] = set()

    try:
        with sd.RawInputStream(
            samplerate=voz.SAMPLE_RATE,
            blocksize=_CHUNK,
            dtype="int16",
            channels=1,
            callback=_audio_cb,
        ):
            with _kb.Listener(on_press=on_press, on_release=on_release):
                for i in range(1, repeticiones + 1):
                    if _esc.is_set():
                        break

                    _rep_done.clear()
                    print(f"  [{i}/{repeticiones}] Mantén Enter para grabar...")
                    _rep_done.wait()

                    if _esc.is_set():
                        break

                    if not frames:
                        print("(sin audio capturado)")
                        continue

                    rec = vosk.KaldiRecognizer(model, voz.SAMPLE_RATE)
                    for chunk in frames:
                        rec.AcceptWaveform(chunk)
                    texto = json.loads(rec.FinalResult()).get("text", "").lower().strip()

                    if texto:
                        print(f"'{texto}'")
                        if texto in VARIANTES[cmd]:
                            print("  (ya existe, se omite)")
                        else:
                            nuevas.add(texto)
                            print("  + variante nueva")
                    else:
                        print("(sin texto detectado)")

    except Exception as e:
        print(f"[entrenamiento] error de audio: {e}")

    if nuevas:
        _persistir(cmd, nuevas)
        VARIANTES[cmd].update(nuevas)
        for v in nuevas:
            _VARIANTES_PLANO[v] = cmd
        print(f"\n  Guardadas {len(nuevas)} variante(s): {sorted(nuevas)}")
    else:
        print("\n  Sin variantes nuevas.")

    print("═" * 56 + "\n")


def _persistir(cmd: str, variantes: set[str]) -> None:
    """Añade `variantes` de `cmd` en user_variants.json."""
    datos: dict[str, list[str]] = {}
    if USER_VARIANTS_PATH.exists():
        try:
            datos = json.loads(USER_VARIANTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    existentes = set(datos.get(cmd, []))
    existentes.update(variantes)
    datos[cmd] = sorted(existentes)

    USER_VARIANTS_PATH.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
