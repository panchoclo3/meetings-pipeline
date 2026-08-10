#!/usr/bin/env python3
"""
Orquestador del pipeline completo.

Uso:
    python scripts/pipeline.py data/audio/2026-08-07_reunion-mim.mp3

Qué hace:
    Ejecuta en secuencia: transcripción+diarización → extracción → staging.
    Se DETIENE después del staging, a propósito — el push a Notion (paso 4)
    lo ejecutas tú manualmente después de revisar el .md generado. Esto es
    el punto de control humano que decidimos mantener: todo lo demás es
    automático, pero nada se escribe en Notion sin que tú lo confirmes.

    Al final te imprime el comando exacto para el paso 4, para copiar/pegar
    después de revisar.
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run_step(cmd: list) -> str:
    """Ejecuta un paso y devuelve la última línea relevante de su stdout (la ruta de salida)."""
    result = subprocess.run(cmd, cwd=ROOT, text=True)
    if result.returncode != 0:
        print(f"\n❌ El paso falló: {' '.join(cmd)}")
        sys.exit(1)


def find_latest(directory: Path, pattern: str) -> Path:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError(f"No se encontró ningún archivo con patrón {pattern} en {directory}")
    return files[0]


def main():
    if len(sys.argv) != 2:
        print("Uso: python scripts/pipeline.py <ruta_al_audio>")
        sys.exit(1)

    audio_path = Path(sys.argv[1]).resolve()
    if not audio_path.exists():
        print(f"Error: no existe el archivo {audio_path}")
        sys.exit(1)

    print("=" * 60)
    print("PASO 1/3 — Transcripción y diarización")
    print("=" * 60)
    run_step([sys.executable, str(SCRIPTS / "01_transcribe.py"), str(audio_path)])
    transcript_path = find_latest(ROOT / "data" / "transcripts", "*.json")

    print("\n" + "=" * 60)
    print("PASO 2/3 — Extracción estructurada")
    print("=" * 60)
    run_step([sys.executable, str(SCRIPTS / "02_extract.py"), str(transcript_path)])
    staging_path = find_latest(ROOT / "data" / "staging", "*.json")

    print("\n" + "=" * 60)
    print("PASO 3/3 — Generando resumen para revisión")
    print("=" * 60)
    run_step([sys.executable, str(SCRIPTS / "03_staging_review.py"), str(staging_path)])

    print("\n" + "=" * 60)
    print("⏸  PIPELINE PAUSADO — revisión humana requerida")
    print("=" * 60)
    print(f"\n1. Revisa: {staging_path.with_suffix('.md')}")
    print(f"2. Si necesitas corregir algo, edita: {staging_path}")
    print(f"3. Cuando esté aprobado, ejecuta:\n")
    print(f"   python scripts/04_push_notion.py {staging_path}")


if __name__ == "__main__":
    main()
