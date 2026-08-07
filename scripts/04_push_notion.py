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
    prop_title,
    prop_rich_text,
    prop_select,
    prop_multi_select,
    prop_date,
    prop_relation,
    block_heading,
    block_paragraph,
    block_bulleted_item,
    block_todo,
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
            blocks.append(block_todo(f"{t['titulo']} — {t['responsable'] or 'sin asignar'}"))

    return blocks


def build_tarea_properties(tarea: dict, reunion_page_id: str, cfg: dict) -> dict:
    p = cfg["notion"]["propiedades_tareas"]
    props = {
        p["titulo"]: prop_title(tarea["titulo"]),
        p["proyecto"]: prop_select(tarea["proyecto"]),
        p["prioridad"]: prop_select(tarea["prioridad"]) if tarea["prioridad"] else {"select": None},
        p["estado"]: prop_select("Pendiente"),
        p["reunion_origen"]: prop_relation([reunion_page_id]),
    }
    if tarea["responsable"]:
        props[p["responsable"]] = prop_select(tarea["responsable"])
    return props


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

    reuniones_db = cfg["notion"]["reuniones_database_id"]
    tareas_db = cfg["notion"]["tareas_database_id"]

    if "REEMPLAZAR" in reuniones_db or "REEMPLAZAR" in tareas_db:
        print("Error: faltan los IDs de las bases de datos en config.yaml.")
        print("Ver README.md sección 'Setup de Notion'.")
        sys.exit(1)

    client = NotionClient()

    print("Creando página de reunión en Notion...")
    reunion_props = build_reunion_properties(data, cfg)
    reunion_blocks = build_reunion_content_blocks(data)
    reunion_page = client.create_page(reuniones_db, reunion_props, reunion_blocks)
    reunion_page_id = reunion_page["id"]
    print(f"  ✅ Página creada: {reunion_page.get('url', reunion_page_id)}")

    task_pages = []
    for tarea in data["tareas"]:
        print(f"  Creando tarea: {tarea['titulo']}...")
        tarea_props = build_tarea_properties(tarea, reunion_page_id, cfg)
        tarea_page = client.create_page(tareas_db, tarea_props)
        task_pages.append(tarea_page.get("url", tarea_page["id"]))

    if task_pages:
        print(f"  ✅ {len(task_pages)} tarea(s) creada(s).")

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
