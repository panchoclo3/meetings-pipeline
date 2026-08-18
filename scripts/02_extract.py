#!/usr/bin/env python3
"""
Paso 2 — Extracción estructurada (Claude API)

Uso:
    python scripts/02_extract.py data/transcripts/20260807_120000_reunion-mim.json

Qué hace:
1. Lee la transcripción resuelta (con nombres reales) del paso 1.
2. Llama a Claude con un prompt que incluye el vocabulario controlado
   (proyectos, tags) desde config.yaml.
3. Parsea y valida la respuesta contra el schema (scripts/schema.py).
4. Si la validación falla, reintenta una vez con el error incluido en el
   prompt (a veces el modelo corrige solo con ver el mensaje de jsonschema).
5. Guarda el JSON validado en data/staging/ — listo para el paso 3 (revisión).

Nota de diseño: esto corre como script, NO en el chat de Claude.ai, para que
sea reproducible, versionable y no dependa de que tú estés presente en una
conversación. Usa la API directa (paquete `anthropic`).
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import time
import yaml
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPT_PATH = ROOT / "prompts" / "extraction_prompt.txt"

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import validate_extraction  # noqa: E402
from progress import Stage, logged_run, format_duration  # noqa: E402


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_prompt(transcripcion: str, cfg: dict) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        proyectos_lista="\n".join(f"- {p}" for p in cfg["proyectos"]),
        tags_lista="\n".join(f"- {t}" for t in cfg["tags_permitidos"]),
        tipos_lista="\n".join(f"- {t}" for t in cfg["tipos_reunion"]),
        personas_lista="\n".join(f"- {p}" for p in cfg["personas_permitidas"]),
        transcripcion=transcripcion,
    )


def call_claude(prompt: str, cfg: dict, extra_context: str = "") -> str:
    import anthropic

    client = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY del entorno
    full_prompt = prompt if not extra_context else f"{prompt}\n\n{extra_context}"

    response = client.messages.create(
        model=cfg["claude"]["model"],
        max_tokens=cfg["claude"]["max_tokens"],
        temperature=cfg["claude"]["temperature"],
        messages=[{"role": "user", "content": full_prompt}],
    )
    # Concatenar todos los bloques de texto de la respuesta
    return "".join(block.text for block in response.content if block.type == "text")


def parse_json_response(raw_text: str) -> dict:
    """
    Limpia posibles fences de markdown (```json ... ```) y parsea.
    Claude a veces los agrega aunque se le pida que no lo haga.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


def extract_with_retry(prompt: str, cfg: dict, max_retries: int = 1) -> dict:
    attempt = 0
    extra_context = ""
    while attempt <= max_retries:
        with Stage(f"Llamando a Claude para extracción estructurada (intento {attempt + 1}/{max_retries + 1})"):
            raw = call_claude(prompt, cfg, extra_context)
        try:
            data = parse_json_response(raw)
        except json.JSONDecodeError as e:
            print(f"  ⚠️  Respuesta no es JSON válido (intento {attempt + 1}): {e}")
            extra_context = (
                f"Tu respuesta anterior no era JSON válido: {e}\n"
                "Responde ÚNICAMENTE con el JSON, sin texto adicional ni backticks."
            )
            attempt += 1
            continue

        errors = validate_extraction(data)
        if not errors:
            return data

        print(f"  ⚠️  JSON no cumple el esquema (intento {attempt + 1}):")
        for err in errors:
            print(f"     - {err}")
        extra_context = (
            "Tu respuesta anterior no cumplía el esquema requerido. Errores:\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\nCorrige y responde ÚNICAMENTE con el JSON corregido, sin texto adicional."
        )
        attempt += 1

    raise RuntimeError(
        f"No se logró obtener una extracción válida tras {max_retries + 1} intentos."
    )


def main():
    if len(sys.argv) != 2:
        print("Uso: python scripts/02_extract.py <ruta_al_transcript.json>")
        sys.exit(1)

    transcript_path = Path(sys.argv[1]).resolve()
    if not transcript_path.exists():
        print(f"Error: no existe el archivo {transcript_path}")
        sys.exit(1)

    with logged_run("02_extract", ROOT) as log_path:
        print(f"📄 Log de esta corrida: {log_path}")
        inicio = time.monotonic()

        cfg = load_config()

        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript_data = json.load(f)

        transcripcion = transcript_data["readable_transcript"]
        if not transcripcion.strip():
            print("Error: la transcripción está vacía.")
            sys.exit(1)

        prompt = build_prompt(transcripcion, cfg)
        extraction = extract_with_retry(prompt, cfg)

        # Adjuntamos metadata de trazabilidad que no viene del modelo
        extraction["_pipeline_meta"] = {
            "transcript_source": str(transcript_path),
            "extracted_at": datetime.now().isoformat(),
            "reunion_id": transcript_data["id"],
        }

        staging_dir = ROOT / cfg["paths"]["staging_dir"]
        staging_dir.mkdir(parents=True, exist_ok=True)
        out_path = staging_dir / f"{transcript_data['id']}.json"

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(extraction, f, ensure_ascii=False, indent=2)

        print(f"\n✅ Extracción validada y guardada en: {out_path}")
        print(f"   Tiempo total: {format_duration(time.monotonic() - inicio)}")
        print(f"   Siguiente paso: python scripts/03_staging_review.py {out_path}")


if __name__ == "__main__":
    main()
