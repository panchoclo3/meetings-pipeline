#!/usr/bin/env python3
"""
Reconciliación contra Notion en vivo — ideas, decisiones y tareas

Uso independiente (modo semanal/catch-all, junta candidatos de los últimos
7 días desde data/processed/*.json — pensado como red de seguridad, ver
más abajo):
    python scripts/06_reconciliacion.py

Uso normal: `04_push_notion.py` llama a `reconciliar()` in-línea, justo
después de crear el acta, para los candidatos de ESA reunión — así las
tareas nuevas nunca se crean sin antes chequear contra Notion si ya existe
algo equivalente (ver README, "no duplicar" es la regla central de este
paso). El modo semanal (este archivo corrido directo, o invocado desde
`05_weekly_digest.py`) es un respaldo: solo importa si el paso 4 de alguna
reunión falló a mitad de la reconciliación y quedó algo sin procesar.

Qué hace `reconciliar()`:
1. Consulta Notion en vivo: ideas existentes, decisiones no-Superadas,
   tareas no-Done (agrupadas por épica).
2. Le pide a Claude que compare los candidatos contra eso y proponga, por
   cada tipo: qué es genuinamente nuevo, qué actualiza algo existente
   (estado de una idea o vigencia de una decisión), y qué es duplicado de
   algo que ya existe (tareas). Claude referencia lo "nuevo" por índice, no
   repitiendo su contenido — así no hay riesgo de que reformule o pierda un
   campo al copiarlo; el código recupera el candidato original completo.
3. Guarda candidatos + propuesta juntos en data/staging/ — artefacto de
   auditoría autocontenido, y lo que scripts/07_aplicar_cambios.py necesita
   para escribir en Notion.

Este script sigue sin escribir nada en Notion — solo compara y propone. La
escritura real vive en 07_aplicar_cambios.py, a propósito separada: así la
propuesta queda como un artefacto inspeccionable o reintentable sin repetir
la llamada a Claude. La clasificación de nivel (Idea/Decisión/Tarea) y la
épica/decisión madre sugerida NO se deciden acá — eso ya lo hizo el paso 2
(extracción); acá solo se decide si algo ya existe.
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
PROMPT_PATH = ROOT / "prompts" / "reconciliacion_prompt.txt"

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import NotionClient, get_database_id  # noqa: E402
from progress import Stage, logged_run, format_duration  # noqa: E402


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def candidatos_desde_reunion(data: dict, reunion_page_id: str, incluir_tareas: bool = True) -> dict:
    """
    Arma el dict de candidatos {"ideas": [...], "decisiones": [...], "tareas": [...]}
    a partir del JSON ya extraído de UNA reunión, adjuntando a cada item de
    qué reunión salió (título para "Origen"/trazabilidad, page_id real para
    la relation "Reunion origen"). `incluir_tareas=False` implementa
    --sin-tareas: las tareas no se mandan a reconciliar en absoluto.
    """
    titulo = data.get("metadata", {}).get("titulo_sugerido", "(sin título)")

    def marcar(items):
        marcados = []
        for item in items:
            item = dict(item)
            item["_origen_reunion_titulo"] = titulo
            item["_origen_reunion_page_id"] = reunion_page_id
            marcados.append(item)
        return marcados

    return {
        "ideas": marcar(data.get("ideas", [])),
        "decisiones": marcar(data.get("decisiones", [])),
        "tareas": marcar(data.get("tareas", [])) if incluir_tareas else [],
    }


def gather_candidatos_recientes(processed_dir: Path, dias: int = 7) -> dict:
    """
    Modo semanal/catch-all: junta candidatos de reuniones ya procesadas en
    los últimos `dias` días. Solo considera reuniones que ya tienen
    `_pipeline_meta.reunion_page_id` (grabado por 04_push_notion.py) — sin
    eso no hay forma de setear "Reunion origen" en lo que se cree, así que
    reuniones de antes de este cambio de esquema simplemente se omiten acá
    (no hay nada que reconciliar retroactivamente sin ese dato).
    """
    if not processed_dir.exists():
        return {"ideas": [], "decisiones": [], "tareas": []}

    limite = datetime.now() - timedelta(days=dias)
    candidatos = {"ideas": [], "decisiones": [], "tareas": []}
    for path in processed_dir.glob("*.json"):
        if path.stem.startswith("decisiones_propuesta_") or path.stem.startswith("reconciliacion_propuesta_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        meta = data.get("_pipeline_meta", {})
        extracted_at = meta.get("extracted_at")
        if not extracted_at:
            continue
        try:
            fecha = datetime.fromisoformat(extracted_at)
        except ValueError:
            continue
        if fecha < limite:
            continue

        reunion_page_id = meta.get("reunion_page_id")
        if not reunion_page_id:
            continue

        for tipo, items in candidatos_desde_reunion(data, reunion_page_id).items():
            candidatos[tipo].extend(items)

    return candidatos


def get_ideas_existentes(client: NotionClient, cfg: dict) -> list:
    p = cfg["notion"]["propiedades_ideas"]
    ideas_db = get_database_id(cfg["notion"], "ideas")
    pages = client.query_database(ideas_db)
    existentes = []
    for page in pages:
        props = page.get("properties", {})
        titulo = "".join(rt.get("plain_text", "") for rt in props.get(p["idea"], {}).get("title", []))
        estado = (props.get(p["estado"], {}).get("select") or {}).get("name")
        horizonte = (props.get(p["horizonte"], {}).get("select") or {}).get("name")
        existentes.append({"id": page["id"], "idea": titulo, "estado": estado, "horizonte": horizonte})
    return existentes


def get_decisiones_existentes(client: NotionClient, cfg: dict) -> list:
    p = cfg["notion"]["propiedades_decisiones"]
    decisiones_db = get_database_id(cfg["notion"], "decisiones")
    filter_ = {"property": p["estado"], "select": {"does_not_equal": "Superada"}}
    pages = client.query_database(decisiones_db, filter_)
    existentes = []
    for page in pages:
        props = page.get("properties", {})
        decision_title = "".join(
            rt.get("plain_text", "") for rt in props.get(p["decision"], {}).get("title", [])
        )
        estado = (props.get(p["estado"], {}).get("select") or {}).get("name")
        tema = "".join(rt.get("plain_text", "") for rt in props.get(p["tema"], {}).get("rich_text", []))
        existentes.append({"id": page["id"], "decision": decision_title, "tema": tema, "estado": estado})
    return existentes


def get_tareas_existentes(client: NotionClient, cfg: dict) -> list:
    """Tareas no-Done, con su épica resuelta a código (AUT-1, etc.) cuando se puede."""
    p = cfg["notion"]["propiedades_tareas"]
    tareas_db = get_database_id(cfg["notion"], "tareas")
    filter_ = {"property": p["estado"], "status": {"does_not_equal": "Done"}}
    pages = client.query_database(tareas_db, filter_)

    epica_id_a_codigo = {info["id"]: codigo for codigo, info in cfg["notion"]["epicas"].items()}

    existentes = []
    for page in pages:
        props = page.get("properties", {})
        titulo = "".join(rt.get("plain_text", "") for rt in props.get(p["titulo"], {}).get("title", []))
        madre_rel = props.get(p["tarea_madre"], {}).get("relation") or []
        madre_id = madre_rel[0]["id"].replace("-", "") if madre_rel else None
        epica = epica_id_a_codigo.get(madre_id, "SIN_EPICA")
        existentes.append({"id": page["id"], "titulo": titulo, "epica": epica})
    return existentes


def _con_indice(items: list) -> list:
    """Copia cada candidato sin los campos internos (_origen_*) y le agrega su índice posicional."""
    limpios = []
    for i, item in enumerate(items):
        limpio = {k: v for k, v in item.items() if not k.startswith("_")}
        limpio["indice"] = i
        limpios.append(limpio)
    return limpios


def build_prompt(candidatos: dict, ideas_existentes: list, decisiones_existentes: list, tareas_existentes: list) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        ideas_candidatas=json.dumps(_con_indice(candidatos["ideas"]), ensure_ascii=False, indent=2),
        ideas_existentes=json.dumps(ideas_existentes, ensure_ascii=False, indent=2),
        decisiones_candidatas=json.dumps(_con_indice(candidatos["decisiones"]), ensure_ascii=False, indent=2),
        decisiones_existentes=json.dumps(decisiones_existentes, ensure_ascii=False, indent=2),
        tareas_candidatas=json.dumps(_con_indice(candidatos["tareas"]), ensure_ascii=False, indent=2),
        tareas_existentes=json.dumps(tareas_existentes, ensure_ascii=False, indent=2),
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
    for tipo in ("ideas", "decisiones", "tareas"):
        if tipo not in data:
            raise ValueError(f"Respuesta de Claude no tiene el formato esperado (falta '{tipo}'): {data}")
    return data


def guardar_resultado(candidatos: dict, propuesta: dict, staging_dir: Path) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = staging_dir / f"reconciliacion_propuesta_{ts}.json"
    contenido = {"candidatos": candidatos, "propuesta": propuesta}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(contenido, f, ensure_ascii=False, indent=2)
    return out_path


def reconciliar(candidatos: dict, cfg: dict, client: NotionClient = None) -> dict:
    """
    Punto de entrada principal. `candidatos` es un dict
    {"ideas": [...], "decisiones": [...], "tareas": [...]} — típicamente de
    `candidatos_desde_reunion()` o `gather_candidatos_recientes()`. Devuelve
    {"candidatos": ..., "propuesta": ...} — el bundle completo que
    `07_aplicar_cambios.py::aplicar_propuesta()` necesita (los candidatos
    originales son la fuente de verdad de los campos; la propuesta solo dice
    qué hacer con cada índice). Siempre guarda este bundle en data/staging/,
    incluso vacío — es el artefacto de auditoría de esta corrida.
    """
    client = client or NotionClient()

    hay_candidatos = any(candidatos.values())
    if not hay_candidatos:
        propuesta = {
            "ideas": {"nuevas": [], "actualizaciones": []},
            "decisiones": {"nuevas": [], "actualizaciones": []},
            "tareas": {"nuevas": [], "duplicadas": []},
        }
    else:
        ideas_existentes = get_ideas_existentes(client, cfg)
        decisiones_existentes = get_decisiones_existentes(client, cfg)
        tareas_existentes = get_tareas_existentes(client, cfg)
        prompt = build_prompt(candidatos, ideas_existentes, decisiones_existentes, tareas_existentes)
        with Stage("Llamando a Claude para reconciliar contra Notion"):
            propuesta = call_claude(prompt, cfg)

    guardar_resultado(candidatos, propuesta, ROOT / cfg["paths"]["staging_dir"])
    return {"candidatos": candidatos, "propuesta": propuesta}


def _contar(propuesta: dict) -> str:
    n_ideas = len(propuesta["ideas"]["nuevas"]) + len(propuesta["ideas"]["actualizaciones"])
    n_dec = len(propuesta["decisiones"]["nuevas"]) + len(propuesta["decisiones"]["actualizaciones"])
    n_tar = len(propuesta["tareas"]["nuevas"])
    n_dup = len(propuesta["tareas"]["duplicadas"])
    return (
        f"{n_ideas} idea(s), {n_dec} decisión(es), {n_tar} tarea(s) nueva(s) "
        f"({n_dup} duplicada(s) evitada(s))"
    )


def main():
    with logged_run("06_reconciliacion", ROOT) as log_path:
        print(f"📄 Log de esta corrida: {log_path}")
        inicio = time.monotonic()

        cfg = load_config()
        candidatos = gather_candidatos_recientes(ROOT / cfg["paths"]["processed_dir"])
        resultado = reconciliar(candidatos, cfg)

        if not any(candidatos.values()):
            print("Sin candidatos recientes con reunión origen registrada.")
        else:
            print(_contar(resultado["propuesta"]))
            print("Para aplicar: python scripts/07_aplicar_cambios.py")
        print(f"Tiempo total: {format_duration(time.monotonic() - inicio)}")


if __name__ == "__main__":
    main()
