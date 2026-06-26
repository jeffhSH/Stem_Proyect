import json
import sys
from pathlib import Path
from typing import Callable

import sounddevice as sd
import vosk

MODEL_PATH  = "vosk-model-small-es-0.42"
SAMPLE_RATE = 16000
CHUNK       = 4000


def _cargar_modelo(model_path: str) -> vosk.Model:
    path = Path(model_path)
    if not path.exists():
        print(f"Error: modelo no encontrado en '{model_path}'")
        print("Descarga desde:  https://alphacephei.com/vosk/models")
        print("Recomendado:     vosk-model-small-es-0.42  (~40 MB)")
        sys.exit(1)
    vosk.SetLogLevel(-1)
    print(f"Cargando modelo '{path.name}'...")
    return vosk.Model(str(path))


def escuchar(
    on_texto:   Callable[[str], bool],
    on_parcial: Callable[[str], tuple[bool, bool]],
    model_path: str = MODEL_PATH,
) -> None:
    """
    Escucha el micrófono continuamente con pyaudio + vosk.

    on_texto(text)
        Llamado con el resultado final de cada fragmento.
        Retorna True para detener el bucle.

    on_parcial(text)
        Llamado con el resultado parcial mientras se habla.
        Retorna (hubo_coincidencia, debe_salir).
        Si hubo_coincidencia=True el reconocedor se resetea.
    """
    model  = _cargar_modelo(model_path)
    rec    = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    print("Escuchando... Ctrl+C para salir.\n")

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK,
            dtype="int16",
            channels=1,
        ) as stream:
            while True:
                data, _overflow = stream.read(CHUNK)

                if rec.AcceptWaveform(bytes(data)):
                    texto = json.loads(rec.Result()).get("text", "").strip()
                    if texto:
                        print(f"[oído]: {texto}")
                        if on_texto(texto):
                            break
                else:
                    texto_p = json.loads(rec.PartialResult()).get("partial", "").strip()
                    if texto_p:
                        coincidio, debe_salir = on_parcial(texto_p)
                        if coincidio:
                            rec.Reset()
                        if debe_salir:
                            break

    except KeyboardInterrupt:
        print("\nDeteniendo detector.")
