"""Modo entrenamiento de Stem: graba variantes fonológicas y las persiste."""
import json
import sys
import threading
from pathlib import Path

import sounddevice as sd
import vosk
from pynput import keyboard as _kb

from comandos import VARIANTES, _VARIANTES_PLANO
from macros import VARIANTES_MEDIOS, USER_MACROS_PATH

USER_VARIANTS_PATH = Path(__file__).parent / "user_variants.json"
_CHUNK  = 4000   # igual que voz.CHUNK
_WASAPI = sd.WasapiSettings(auto_convert=True) if sys.platform == "win32" and hasattr(sd, "WasapiSettings") else None


def iniciar() -> None:
    """Muestra el menú de entrenamiento y graba variantes para el comando elegido."""
    cmds        = list(VARIANTES.keys())
    cmds_medios = list(VARIANTES_MEDIOS.keys())
    n_cmd = len(cmds)

    print("\n" + "═" * 56)
    print("  MODO ENTRENAMIENTO — Stem")
    print("═" * 56)
    for i, cmd in enumerate(cmds, 1):
        variantes_str = ", ".join(sorted(VARIANTES[cmd]))
        if len(variantes_str) > 42:
            variantes_str = variantes_str[:39] + "..."
        print(f"  {i:3}. {cmd:<26} {variantes_str}")
    print()
    print("  --- MEDIOS ---")
    for i, cmd in enumerate(cmds_medios, n_cmd + 1):
        variantes_str = ", ".join(sorted(VARIANTES_MEDIOS[cmd]))
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
    if not (0 <= idx < n_cmd + len(cmds_medios)):
        print("Número fuera de rango.")
        return

    es_macro = idx >= n_cmd
    if es_macro:
        cmd                = cmds_medios[idx - n_cmd]
        variantes_actuales = VARIANTES_MEDIOS[cmd]
    else:
        cmd                = cmds[idx]
        variantes_actuales = VARIANTES[cmd]

    try:
        accion = input(f"  [{cmd}] A(gregar) / E(liminar) / 0 cancelar: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return

    if accion == "0":
        print("Cancelado.")
        return

    if accion == "e":
        _eliminar_variante_de(cmd, es_macro, variantes_actuales)
        return

    import voz
    model = voz._modelo_activo
    if model is None:
        print("[entrenamiento] modelo Vosk no disponible todavía")
        return

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

    print(f"\n  Variantes actuales: {sorted(variantes_actuales)}")
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
            blocksize=_CHUNK * 2,
            latency="high",
            dtype="int16",
            channels=1,
            extra_settings=_WASAPI,
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
                        if texto in variantes_actuales:
                            print("  (ya existe, se omite)")
                        else:
                            nuevas.add(texto)
                            print("  + variante nueva")
                    else:
                        print("(sin texto detectado)")

    except Exception as e:
        print(f"[entrenamiento] error de audio: {e}")

    if nuevas:
        if es_macro:
            _persistir_macro(cmd, nuevas)
            VARIANTES_MEDIOS[cmd].update(nuevas)
        else:
            _persistir(cmd, nuevas)
            VARIANTES[cmd].update(nuevas)
            for v in nuevas:
                _VARIANTES_PLANO[v] = cmd
        print(f"\n  Guardadas {len(nuevas)} variante(s): {sorted(nuevas)}")
    else:
        print("\n  Sin variantes nuevas.")

    print("═" * 56 + "\n")


def _eliminar_variante_de(cmd: str, es_macro: bool, variantes_actuales: set) -> None:
    """Flujo para eliminar una variante de usuario del comando ya seleccionado."""
    json_path = USER_MACROS_PATH if es_macro else USER_VARIANTS_PATH

    # Determinar qué variantes son de usuario (están en el JSON)
    variantes_usuario: set[str] = set()
    if json_path.exists():
        try:
            datos = json.loads(json_path.read_text(encoding="utf-8"))
            variantes_usuario = set(datos.get(cmd, []))
        except Exception:
            pass

    variantes_lista = sorted(variantes_actuales)
    print(f"\n  Variantes de '{cmd}':")
    for i, v in enumerate(variantes_lista, 1):
        if v in variantes_usuario:
            print(f"  {i:3}. {v}")
        else:
            print(f"  {i:3}. {v}  (base — no eliminable)")
    print()

    try:
        sel2 = input("Número de variante a eliminar (0 para cancelar): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return

    if not sel2.isdigit() or int(sel2) == 0:
        print("Cancelado.")
        return

    idx2 = int(sel2) - 1
    if not (0 <= idx2 < len(variantes_lista)):
        print("Número fuera de rango.")
        return

    variante = variantes_lista[idx2]
    if variante not in variantes_usuario:
        print(f"  '{variante}' es una variante base — no se puede eliminar.")
        return

    try:
        conf = input(f"  ¿Eliminar '{variante}' de '{cmd}'? (s/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return

    if conf != "s":
        print("Cancelado.")
        return

    # Eliminar de memoria
    variantes_actuales.discard(variante)
    if not es_macro and variante in _VARIANTES_PLANO:
        del _VARIANTES_PLANO[variante]

    # Eliminar del JSON
    datos: dict[str, list[str]] = {}
    if json_path.exists():
        try:
            datos = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if cmd in datos:
        datos[cmd] = [v for v in datos[cmd] if v != variante]
        if not datos[cmd]:
            del datos[cmd]
        json_path.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"\n  Variante '{variante}' eliminada de '{cmd}'.")
    print("═" * 56 + "\n")


def _persistir_macro(cmd: str, variantes: set[str]) -> None:
    """Añade `variantes` de `cmd` en user_macros.json."""
    datos: dict[str, list[str]] = {}
    if USER_MACROS_PATH.exists():
        try:
            datos = json.loads(USER_MACROS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    existentes = set(datos.get(cmd, []))
    existentes.update(variantes)
    datos[cmd] = sorted(existentes)

    USER_MACROS_PATH.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
