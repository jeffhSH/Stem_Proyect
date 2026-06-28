# Stem — Deploy Checklist

Configuración manual requerida una vez por máquina antes de usar Stem.

## Requerido antes del primer uso

- [ ] `pip install -r requirements.txt`
- [ ] Descargar modelo Vosk español y colocarlo en la raíz del proyecto:
      `vosk-model-small-es-0.42/` (https://alphacephei.com/vosk/models)

## Configuración de audio en Windows

- [ ] `mmsys.cpl` → Grabación → dispositivo de entrada → Propiedades →
      Opciones avanzadas → formato: **48000 Hz**
      (Stem corre en 16000 Hz internamente; `WasapiSettings(auto_convert=True)`
      maneja la conversión, pero el dispositivo debe estar en 48000 Hz)

- [ ] `mmsys.cpl` → Comunicaciones → seleccionar **"No hacer nada"**
      (evita que Windows baje el volumen de reproducción al detectar voz)

## Pendientes antes del release

- [ ] Verificar que auriculares/dispositivo de audio estén configurados
      en 48000 Hz en mmsys.cpl → Propiedades → Opciones avanzadas
      (Stem corre en 16000 Hz internamente, auto_convert maneja la
      conversión pero el dispositivo debe estar en 48000 Hz)
