#!/usr/bin/env python3
"""
Reconciliación semanal de Decisiones — SOLO PROPUESTA (la aplicación real
la hace scripts/07_aplicar_decisiones.py)

Uso independiente (para depurar sin correr el resumen semanal completo):
    python scripts/06_decisiones_reconciliacion.py

Uso normal: `05_weekly_digest.py` importa este módulo, llama a
`ejecutar_reconciliacion()` para obtener la propuesta, y se la pasa a
`07_aplicar_decisiones.py::aplicar_propuesta()` para escribirla en Notion.

Qué hace:
1. Junta las decisiones extraídas en la última semana, leyendo
   data/processed/*.json (reuniones que ya pasaron por el paso 4).
2. Consulta la base real "Decisiones" en Notion (solo lectura).
3. Le pide a Claude que compare ambas listas y proponga:
   - decisiones nuevas que no existen aún en la base (con su estado inicial).
   - actualizaciones de estado para decisiones existentes, con su razón.
4. Guarda la propuesta completa en
   data/staging/decisiones_propuesta_<fecha>.json — artefacto de auditoría
   de lo que se generó esta corrida, independiente de si luego se aplicó.

Este script sigue sin escribir nada en la base "Decisiones" — solo compara
y propone. La escritura real (crear páginas nuevas, actualizar estados)
vive en 07_aplicar_decisiones.py, a propósito separada en otro paso: así
la propuesta queda como un artefacto que se puede inspeccionar o volver a
aplicar sin tener que repetir la llamada a Claude.
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
from datetime import datetime, timedelta
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPT_PATH = ROOT / "prompts" / "decisiones_reconciliacion_prompt.txt"

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import NotionClient, get_database_id  # noqa: E402
from progress import Stage, logged_run, format_duration  # noqa: E402


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def gather_decisiones_recientes(processed_dir: Path, dias: int = 7) -> list:
    """
    Lee data/processed/*.json y extrae las decisiones de reuniones procesadas
    en los últimos `dias` días, según `_pipeline_meta.extracted_at`.
    """
    if not processed_dir.exists():
        return []

    limite = datetime.now() - timedelta(days=dias)
    recientes = []
    for path in processed_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        extracted_at = data.get("_pipeline_meta", {}).get("extracted_at")
        if not extracted_at:
            continue
        try:
            fecha = datetime.fromisoformat(extracted_at)
        except ValueError:
            continue
        if fecha < limite:
            continue

        titulo = data.get("metadata", {}).get("titulo_sugerido", "(sin título)")
        for d in data.get("decisiones", []):
            recientes.append(
                {
                    "decision": d["decision"],
                    "razon": d["razon"],
                    "estado": d["estado"],
                    "origen_reunion": titulo,
                }
            )
    return recientes


def get_decisiones_existentes(client: NotionClient, cfg: dict) -> list:
    """Trae todas las decisiones ya registradas en Notion, en formato plano."""
    p = cfg["notion"]["propiedades_decisiones"]
    decisiones_db = get_database_id(cfg["notion"], "decisiones")
    pages = client.query_database(decisiones_db)

    existentes = []
    for page in pages:
        props = page.get("properties", {})
        decision_title = "".join(
            rt.get("plain_text", "") for rt in props.get(p["decision"], {}).get("title", [])
        )
        estado = (props.get(p["estado"], {}).get("status") or {}).get("name")
        prototipo = (props.get(p["prototipo"], {}).get("select") or {}).get("name")
        existentes.append(
            {
                "id": page["id"],
                "decision": decision_title,
                "estado": estado,
                "prototipo": prototipo,
            }
        )
    return existentes


def build_prompt(nuevas_candidatas: list, existentes: list) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        decisiones_semana=json.dumps(nuevas_candidatas, ensure_ascii=False, indent=2),
        decisiones_existentes=json.dumps(existentes, ensure_ascii=False, indent=2),
    )


def parse_json_response(raw_text: str) -> dict:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


def call_claude(prompt: str, cfg: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY del entorno
    response = client.messages.create(
        model=cfg["claude"]["model"],
        max_tokens=cfg["claude"]["max_tokens"],
        temperature=cfg["claude"]["temperature"],
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    data = parse_json_response(raw)
    if "nuevas" not in data or "actualizaciones" not in data:
        raise ValueError(f"Respuesta de Claude no tiene el formato esperado: {data}")
    return data


def guardar_propuesta(propuesta: dict, staging_dir: Path) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().date().isoformat()
    out_path = staging_dir / f"decisiones_propuesta_{fecha}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(propuesta, f, ensure_ascii=False, indent=2)
    return out_path


def ejecutar_reconciliacion(cfg: dict, client: NotionClient = None) -> dict:
    """
    Punto de entrada usado por 05_weekly_digest.py (y standalone vía main()).
    Devuelve la propuesta como dict ({"nuevas": [...], "actualizaciones": [...]}),
    lista para pasarle a 07_aplicar_decisiones.py::aplicar_propuesta().
    Siempre guarda un JSON de propuesta en data/staging/, incluso si ambas
    listas terminan vacías — es el artefacto de auditoría de esta ejecución.
    """
    client = client or NotionClient()

    nuevas_candidatas = gather_decisiones_recientes(ROOT / cfg["paths"]["processed_dir"])

    if not nuevas_candidatas:
        # Nada que comparar esta semana — no vale la pena llamar a Claude.
        propuesta = {"nuevas": [], "actualizaciones": []}
    else:
        existentes = get_decisiones_existentes(client, cfg)
        prompt = build_prompt(nuevas_candidatas, existentes)
        with Stage("Llamando a Claude para reconciliar decisiones"):
            propuesta = call_claude(prompt, cfg)

    guardar_propuesta(propuesta, ROOT / cfg["paths"]["staging_dir"])
    return propuesta


def main():
    with logged_run("06_decisiones_reconciliacion", ROOT) as log_path:
        print(f"📄 Log de esta corrida: {log_path}")
        inicio = time.monotonic()

        cfg = load_config()
        propuesta = ejecutar_reconciliacion(cfg)
        nuevas = propuesta.get("nuevas", [])
        actualizaciones = propuesta.get("actualizaciones", [])
        if not nuevas and not actualizaciones:
            print("Sin propuestas de decisiones esta semana.")
            return
        print(f"{len(nuevas)} decisión(es) nueva(s), {len(actualizaciones)} actualización(es) propuesta(s).")
        print(f"Tiempo total: {format_duration(time.monotonic() - inicio)}")
        print("Para aplicarlas a Notion: python scripts/07_aplicar_decisiones.py")


if __name__ == "__main__":
    main()
