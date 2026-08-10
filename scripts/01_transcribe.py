#!/usr/bin/env python3
"""
Paso 1 — Transcripción + Diarización (WhisperX)

Uso:
    python scripts/01_transcribe.py data/audio/2026-08-07_reunion-mim.mp3

Qué hace:
1. Transcribe el audio con Whisper (modelo configurado en config.yaml).
2. Alinea la transcripción a nivel de palabra (forced alignment).
3. Diariza: asigna cada segmento a un hablante genérico (SPEAKER_00, SPEAKER_01...).
4. Te muestra una muestra de cada hablante detectado y te pide que le pongas
   nombre real por CLI (mapeo manual — decisión explícita: no automatizamos
   la identificación por voz porque con 2-4 personas es más rápido y más
   confiable que tú lo confirmes a mano).
5. Guarda un JSON con la transcripción ya resuelta a nombres reales, listo
   para el paso 2 (extracción).

Requisitos: ver README.md — WhisperX + pyannote requieren un token de
HuggingFace (variable de entorno HF_TOKEN) y aceptar las condiciones de los
modelos pyannote/speaker-diarization en huggingface.co la primera vez.
"""

import sys

# En Windows, la consola puede quedar en cp1252 (según la terminal desde la
# que se invoque) y los print() con emojis (✅, ⚠️, etc.) revientan con
# UnicodeEncodeError. Forzamos UTF-8 para que el script funcione igual desde
# PowerShell, cmd.exe o Git Bash.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import yaml
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"

load_dotenv(ROOT / ".env")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def transcribe_and_diarize(audio_path: Path, cfg: dict) -> dict:
    """
    Ejecuta WhisperX end-to-end: ASR + alineación + diarización.
    Devuelve una lista de segmentos con speaker, start, end, text.
    """
    import whisperx
    import whisperx.diarize
    import torch
    import os

    wx_cfg = cfg["whisperx"]
    device = wx_cfg["device"]
    compute_type = wx_cfg["compute_type"]

    print(f"[1/4] Cargando modelo Whisper ({wx_cfg['model']}) en {device}...")
    model = whisperx.load_model(
        wx_cfg["model"], device=device, compute_type=compute_type
    )

    print("[2/4] Transcribiendo audio...")
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(
        audio, batch_size=wx_cfg["batch_size"], language=wx_cfg["language"]
    )

    print("[3/4] Alineando transcripción a nivel de palabra...")
    model_a, metadata = whisperx.load_align_model(
        language_code=wx_cfg["language"], device=device
    )
    result = whisperx.align(
        result["segments"], model_a, metadata, audio, device, return_char_alignments=False
    )

    if wx_cfg["diarization"]:
        print("[4/4] Diarizando (identificando hablantes)...")
        hf_token = os.environ.get(wx_cfg["hf_token_env"])
        if not hf_token:
            raise RuntimeError(
                f"Falta la variable de entorno {wx_cfg['hf_token_env']} "
                "con tu token de HuggingFace. Ver README.md sección 'Configuración'."
            )
        diarize_model = whisperx.diarize.DiarizationPipeline(
            model_name="pyannote/speaker-diarization-3.1", token=hf_token, device=device
        )
        diarize_segments = diarize_model(str(audio_path))
        result = whisperx.assign_word_speakers(diarize_segments, result)
    else:
        print("[4/4] Diarización desactivada en config.yaml — se omite.")

    return result


def collect_speaker_samples(segments: list, max_samples: int = 2) -> dict:
    """Junta 1-2 frases de ejemplo por cada SPEAKER_XX para mostrarlas al usuario."""
    samples = {}
    for seg in segments:
        speaker = seg.get("speaker", "SPEAKER_DESCONOCIDO")
        samples.setdefault(speaker, [])
        if len(samples[speaker]) < max_samples:
            samples[speaker].append(seg.get("text", "").strip())
    return samples


def ask_speaker_mapping(samples: dict) -> dict:
    """
    Muestra ejemplos de cada hablante detectado y pide el nombre real.
    Devuelve dict {SPEAKER_00: "Alessio", ...}
    """
    print("\n--- Mapeo de hablantes ---")
    print("WhisperX detectó los siguientes hablantes. Escribe el nombre real de cada uno")
    print("(o presiona Enter para dejarlo como está si no estás seguro).\n")

    mapping = {}
    for speaker, texts in samples.items():
        print(f"\n{speaker}:")
        for t in texts:
            print(f'   "{t}"')
        name = input(f"  → Nombre real para {speaker}: ").strip()
        mapping[speaker] = name if name else speaker
    return mapping


def apply_mapping(segments: list, mapping: dict) -> list:
    for seg in segments:
        speaker = seg.get("speaker", "SPEAKER_DESCONOCIDO")
        seg["speaker_name"] = mapping.get(speaker, speaker)
    return segments


def build_readable_transcript(segments: list) -> str:
    """Genera el texto plano 'Nombre: frase' que se le pasará a Claude en el paso 2."""
    lines = []
    for seg in segments:
        name = seg.get("speaker_name", "Desconocido")
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"{name}: {text}")
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print("Uso: python scripts/01_transcribe.py <ruta_al_audio>")
        sys.exit(1)

    audio_path = Path(sys.argv[1]).resolve()
    if not audio_path.exists():
        print(f"Error: no existe el archivo {audio_path}")
        sys.exit(1)

    cfg = load_config()
    result = transcribe_and_diarize(audio_path, cfg)
    segments = result["segments"]

    samples = collect_speaker_samples(segments)
    mapping = ask_speaker_mapping(samples)
    segments = apply_mapping(segments, mapping)
    readable_transcript = build_readable_transcript(segments)

    transcripts_dir = ROOT / cfg["paths"]["transcripts_dir"]
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    stem = audio_path.stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_id = f"{timestamp}_{stem}"

    output = {
        "id": out_id,
        "audio_source": str(audio_path),
        "created_at": datetime.now().isoformat(),
        "speaker_mapping": mapping,
        "segments": segments,
        "readable_transcript": readable_transcript,
    }

    out_path = transcripts_dir / f"{out_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Transcripción guardada en: {out_path}")
    print(f"   Siguiente paso: python scripts/02_extract.py {out_path}")


if __name__ == "__main__":
    main()
