#!/usr/bin/env python3
"""
Reconciliación semanal de Decisiones — SOLO PROPUESTA, nunca escritura automática

Uso independiente (para depurar sin correr el resumen semanal completo):
    python scripts/06_decisiones_reconciliacion.py

Uso normal: `05_weekly_digest.py` importa este módulo y llama a
`ejecutar_reconciliacion()`, agregando el texto que devuelve al MISMO mensaje
de Telegram del resumen semanal.

Qué hace:
1. Junta las decisiones extraídas en la última semana, leyendo
   data/processed/*.json (reuniones que ya pasaron por el paso 4).
2. Consulta la base real "Decisiones" en Notion (solo lectura).
3. Le pide a Claude que compare ambas listas y proponga:
   - decisiones nuevas que no existen aún en la base.
   - actualizaciones de estado para decisiones existentes, con su razón.
4. Guarda la propuesta completa en
   data/staging/decisiones_propuesta_<fecha>.json — un artefacto estructurado
   pensado para que una futura automatización de aprobación (o vos a mano)
   la aplique.

Por qué esto NUNCA escribe en la base "Decisiones": las decisiones de
proyecto son exactamente el tipo de dato sensible que este pipeline no toca
sin confirmación humana explícita (ver filosofía en README.md). Cambiar el
estado de una decisión o crear una nueva sin que una persona lo confirme
sería la automatización silenciosa que este proyecto evita a propósito. La
aplicación real de estos cambios queda para revisión manual en Notion, o
para un script de aplicación separado (deliberadamente no incluido aquí)
que lea el JSON generado.
"""

import sys
import json
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPT_PATH = ROOT / "prompts" / "decisiones_reconciliacion_prompt.txt"

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import NotionClient  # noqa: E402


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
    pages = client.query_database(cfg["notion"]["decisiones_database_id"])

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


def formatear_para_telegram(propuesta: dict) -> str:
    """Devuelve "" si no hay nada que proponer, para no ensuciar el mensaje semanal."""
    nuevas = propuesta.get("nuevas", [])
    actualizaciones = propuesta.get("actualizaciones", [])
    if not nuevas and not actualizaciones:
        return ""

    lineas = [
        "🔍 *Propuesta de decisiones* (revisar y aplicar a mano en Notion — nada se escribió automáticamente):"
    ]

    if nuevas:
        lineas.append("\n_Nuevas:_")
        for n in nuevas:
            lineas.append(f"- {n['decision']} ({n.get('origen_reunion', 'sin origen')})")

    if actualizaciones:
        lineas.append("\n_Actualizaciones sugeridas:_")
        for a in actualizaciones:
            lineas.append(f"- {a['decision_existente']} → {a['estado_nuevo']} — {a['razon_cambio']}")

    return "\n".join(lineas)


def ejecutar_reconciliacion(cfg: dict) -> str:
    """
    Punto de entrada usado por 05_weekly_digest.py. Devuelve el texto
    (posiblemente vacío) para agregar al mensaje semanal de Telegram.
    Siempre guarda un JSON de propuesta en data/staging/, incluso si ambas
    listas terminan vacías — es el artefacto de auditoría de esta ejecución.
    """
    client = NotionClient()

    nuevas_candidatas = gather_decisiones_recientes(ROOT / cfg["paths"]["processed_dir"])

    if not nuevas_candidatas:
        # Nada que comparar esta semana — no vale la pena llamar a Claude.
        propuesta = {"nuevas": [], "actualizaciones": []}
    else:
        existentes = get_decisiones_existentes(client, cfg)
        prompt = build_prompt(nuevas_candidatas, existentes)
        propuesta = call_claude(prompt, cfg)

    guardar_propuesta(propuesta, ROOT / cfg["paths"]["staging_dir"])
    return formatear_para_telegram(propuesta)


def main():
    cfg = load_config()
    texto = ejecutar_reconciliacion(cfg)
    print(texto if texto else "Sin propuestas de decisiones esta semana.")


if __name__ == "__main__":
    main()
