#!/usr/bin/env python3
"""
Paso 4 — Escritura final a Notion

Uso:
    python scripts/04_push_notion.py data/staging/20260807_120000_reunion-mim.json

Qué hace:
1. Lee el JSON aprobado (después de tu revisión en el paso 3).
2. Crea la página en la base "Reuniones" con las propiedades correspondientes
   y todo el contenido (resumen, decisiones, ideas, etc.) como bloques.
3. Crea una página en "Tareas" por cada tarea extraída, con relación a la
   página de la reunión recién creada.
4. Mueve el JSON de staging/ a processed/ (evita reprocesar por error).

Este script asume que ya creaste las dos bases en Notion con las propiedades
definidas en config.yaml (ver README.md sección 'Setup de Notion').
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import shutil
import yaml
from pathlib import Path
from datetime import datetime, date
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import (  # noqa: E402
    NotionClient,
    get_database_id,
    prop_title,
    prop_rich_text,
    prop_select,
    prop_status,
    prop_multi_select,
    prop_date,
    prop_relation,
    prop_people,
    block_heading,
    block_paragraph,
    block_bulleted_item,
    block_todo,
)

# La base "Tareas" (Kanban) usa un campo de tipo "status" para Estado, no
# "select" — Notion no deja crear opciones de status nuevas vía API, así que
# mapeamos el vocabulario en español del pipeline a las opciones en inglés
# ya definidas en la base.
ESTADO_TAREA_A_STATUS = {
    "Pendiente": "Not started",
    "En progreso": "In progress",
    "Hecho": "Done",
}


def capitalizar_prioridad(prioridad: str) -> str:
    # La extracción de Claude produce "alta"/"media"/"baja" en minúscula, pero
    # el select "Prioridad" en Notion espera "Alta"/"Media"/"Baja".
    return prioridad.capitalize() if prioridad else prioridad


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_api_users_by_name(client: NotionClient) -> dict:
    """
    Respaldo automático para resolver nombre -> user ID cuando
    config.yaml -> notion.resolucion_personas no tiene el ID a mano.
    Nota: /v1/users solo devuelve miembros con cuenta completa del
    workspace — invitados con acceso limitado no aparecen acá aunque
    tengan tareas asignadas en Notion (ver README.md).
    """
    try:
        users = client.list_users()
    except RuntimeError as e:
        print(f"  ⚠️  No se pudo consultar /v1/users para resolver personas: {e}")
        return {}
    return {u["name"]: u["id"] for u in users if u.get("name")}


def resolver_persona(nombre: str, cfg: dict, api_users_by_name: dict):
    """
    Resuelve un nombre en texto libre al user ID real de Notion que
    necesita el campo "Responsable" (tipo person). Prioridad:
    1. config.yaml -> notion.resolucion_personas (mapeo editado a mano).
    2. Búsqueda por nombre entre los usuarios que devuelve la API.
    Devuelve None si ninguna de las dos fuentes lo resuelve — el llamador
    decide qué hacer (ver build_tarea_properties).
    """
    mapeo = (cfg["notion"].get("resolucion_personas") or {})
    user_id = mapeo.get(nombre)
    if user_id:
        return user_id
    return api_users_by_name.get(nombre)


def build_reunion_properties(data: dict, cfg: dict) -> dict:
    p = cfg["notion"]["propiedades_reuniones"]
    meta = data["metadata"]
    return {
        p["titulo"]: prop_title(meta["titulo_sugerido"]),
        p["fecha"]: prop_date(date.today().isoformat()),
        p["proyecto"]: prop_select(meta["proyecto_sugerido"]),
        p["personas"]: prop_multi_select(meta["personas_detectadas"]),
        p["tags"]: prop_multi_select(meta["tags_sugeridos"]),
        p["tipo"]: prop_select(meta["tipo_reunion"]),
        p["estado"]: prop_select("Revisado"),  # llega aquí porque ya pasó el staging
    }


def build_reunion_content_blocks(data: dict) -> list:
    blocks = []

    blocks.append(block_heading("Resumen ejecutivo"))
    blocks.append(block_paragraph(data["resumen_ejecutivo"]))

    blocks.append(block_heading("Resumen detallado"))
    for para in data["resumen_detallado"].split("\n\n"):
        if para.strip():
            blocks.append(block_paragraph(para.strip()))

    if data["decisiones"]:
        blocks.append(block_heading("Decisiones"))
        for d in data["decisiones"]:
            tag = "" if d["estado"] == "confirmada" else " (tentativa)"
            blocks.append(block_bulleted_item(f"{d['decision']}{tag} — {d['razon']}"))

    if data["ideas"]:
        blocks.append(block_heading("Ideas"))
        for i in data["ideas"]:
            estado_txt = {
                "propuesta": "Propuesta",
                "descartada": "Descartada",
                "en_evaluacion": "En evaluación",
            }[i["estado"]]
            line = f"[{estado_txt}] {i['idea']} — {i['contexto']}"
            if i.get("razon_descarte"):
                line += f" (Razón de descarte: {i['razon_descarte']})"
            blocks.append(block_bulleted_item(line))

    if data["preguntas_abiertas"]:
        blocks.append(block_heading("Preguntas abiertas"))
        for q in data["preguntas_abiertas"]:
            blocks.append(block_bulleted_item(q))

    if data["proximos_pasos"]:
        blocks.append(block_heading("Próximos pasos"))
        for step in data["proximos_pasos"]:
            blocks.append(block_bulleted_item(step))

    if data["tareas"]:
        blocks.append(block_heading("Tareas (ver también base Tareas)"))
        for t in data["tareas"]:
            blocks.append(block_todo(f"{t['titulo']} — {', '.join(t['responsable']) or 'sin asignar'}"))

    return blocks


def build_tarea_properties(
    tarea: dict, reunion_page_id: str, cfg: dict, api_users_by_name: dict
) -> tuple:
    """
    Devuelve (props, nombres_sin_resolver). "Responsable" es un campo person
    de Notion — exige user IDs reales, no nombres en texto. Cada nombre que
    la extracción encontró se resuelve vía resolver_persona(); los que no se
    logran resolver NO se pierden: quedan como advertencia en "Notas" para
    completar a mano, en vez de fallar la escritura o el campo quedar vacío
    sin dejar rastro.
    """
    p = cfg["notion"]["propiedades_tareas"]
    props = {
        p["titulo"]: prop_title(tarea["titulo"]),
        p["prioridad"]: prop_select(capitalizar_prioridad(tarea["prioridad"])),
        p["estado"]: prop_status(ESTADO_TAREA_A_STATUS["Pendiente"]),
        p["reunion_origen"]: prop_relation([reunion_page_id]),
    }

    resueltos = []
    no_resueltos = []
    for nombre in tarea["responsable"]:
        user_id = resolver_persona(nombre, cfg, api_users_by_name)
        if user_id:
            resueltos.append(user_id)
        else:
            no_resueltos.append(nombre)

    if resueltos:
        props[p["responsable"]] = prop_people(resueltos)

    if no_resueltos:
        nota = " ".join(f'[Responsable sin resolver: "{n}"]' for n in no_resueltos)
        props[p["notas"]] = prop_rich_text(nota)

    return props, no_resueltos


def main():
    if len(sys.argv) != 2:
        print("Uso: python scripts/04_push_notion.py <ruta_al_staging.json>")
        sys.exit(1)

    staging_path = Path(sys.argv[1]).resolve()
    if not staging_path.exists():
        print(f"Error: no existe el archivo {staging_path}")
        sys.exit(1)

    cfg = load_config()

    with open(staging_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    reuniones_db = get_database_id(cfg["notion"], "reuniones")
    tareas_db = get_database_id(cfg["notion"], "tareas")

    client = NotionClient()
    api_users_by_name = fetch_api_users_by_name(client)

    print("Creando página de reunión en Notion...")
    reunion_props = build_reunion_properties(data, cfg)
    reunion_blocks = build_reunion_content_blocks(data)
    reunion_page = client.create_page(reuniones_db, reunion_props, reunion_blocks)
    reunion_page_id = reunion_page["id"]
    print(f"  ✅ Página creada: {reunion_page.get('url', reunion_page_id)}")

    task_pages = []
    tareas_sin_resolver = []
    for tarea in data["tareas"]:
        print(f"  Creando tarea: {tarea['titulo']}...")
        tarea_props, no_resueltos = build_tarea_properties(
            tarea, reunion_page_id, cfg, api_users_by_name
        )
        if no_resueltos:
            print(
                f"    ⚠️  Responsable(s) sin resolver a user ID de Notion: "
                f"{', '.join(no_resueltos)} — quedaron anotados en '{cfg['notion']['propiedades_tareas']['notas']}'."
            )
            tareas_sin_resolver.append((tarea["titulo"], no_resueltos))
        tarea_page = client.create_page(tareas_db, tarea_props)
        task_pages.append(tarea_page.get("url", tarea_page["id"]))

    if task_pages:
        print(f"  ✅ {len(task_pages)} tarea(s) creada(s).")

    if tareas_sin_resolver:
        print(
            f"\n⚠️  Resumen: {len(tareas_sin_resolver)} de {len(task_pages)} tarea(s) "
            "con al menos un responsable sin resolver:"
        )
        for titulo, nombres in tareas_sin_resolver:
            print(f"   - \"{titulo}\": {', '.join(nombres)}")
        print(
            "   Completa 'notion.resolucion_personas' en config.yaml con sus user "
            "ID reales, o asígnalos a mano en Notion."
        )

    # Mover de staging a processed para no reprocesar por accidente
    processed_dir = ROOT / cfg["paths"]["processed_dir"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / staging_path.name
    shutil.move(str(staging_path), str(dest))

    md_path = staging_path.with_suffix(".md")
    if md_path.exists():
        shutil.move(str(md_path), str(processed_dir / md_path.name))

    print(f"\n✅ Listo. Reunión procesada y movida a: {dest}")
    print(f"   Fecha de procesamiento: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
