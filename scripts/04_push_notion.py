#!/usr/bin/env python3
"""
Paso 4 — Escritura final a Notion

Uso:
    python scripts/04_push_notion.py data/staging/20260807_120000_reunion-mim.json
    python scripts/04_push_notion.py data/staging/20260807_120000_reunion-mim.json --sin-tareas

Qué hace:
1. Lee el JSON aprobado (después de tu revisión en el paso 3).
2. Crea la página en la base "Reuniones" con las propiedades correspondientes
   y todo el contenido (resumen, ideas, decisiones, tareas candidatas) como
   bloques.
3. Reconcilia in-línea contra Notion en vivo (scripts/06_reconciliacion.py)
   y aplica el resultado (scripts/07_aplicar_cambios.py): crea/actualiza
   Ideas, Decisiones y Tareas — sin duplicar nada que ya exista, con Tarea
   madre / Decision madre resueltos al catálogo real. `--sin-tareas` excluye
   las tareas de este paso (ideas y decisiones igual se reconcilian).
4. Mueve el JSON de staging/ a processed/ (evita reprocesar por error). Si
   algo quedó pendiente de la reconciliación (típicamente: ninguna épica
   encaja para una tarea), se guarda aparte en staging/ para reintentar.

Este script asume que ya creaste las bases en Notion con las propiedades
definidas en config.yaml (ver README.md sección 'Setup de Notion').

Por qué la reconciliación corre acá y no en un paso aparte: es la filosofía
ya establecida de este pipeline — el único punto de revisión humana es el
paso 3 (staging); todo lo que sigue después de que vos aprobás corre solo,
hasta el final. Separarlo en otro paso manual solo agregaría fricción sin
agregar seguridad real (ver scripts/06_reconciliacion.py para el detalle de
qué hace la reconciliación en sí).
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import importlib
import json
import shutil
import time
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
    prop_select,
    prop_multi_select,
    prop_date,
    block_heading,
    block_paragraph,
    block_bulleted_item,
)
from progress import Stage, logged_run, format_duration  # noqa: E402


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


def build_reunion_content_blocks(data: dict, incluir_tareas: bool = True) -> list:
    blocks = []

    blocks.append(block_heading("Resumen ejecutivo"))
    blocks.append(block_paragraph(data["resumen_ejecutivo"]))

    blocks.append(block_heading("Resumen detallado"))
    for para in data["resumen_detallado"].split("\n\n"):
        if para.strip():
            blocks.append(block_paragraph(para.strip()))

    if data["ideas"]:
        blocks.append(block_heading("Ideas"))
        for i in data["ideas"]:
            horizonte = f" [{i['horizonte']}]" if i.get("horizonte") else ""
            blocks.append(block_bulleted_item(f"{i['idea']}{horizonte} — {i.get('problema_que_resuelve') or 'sin problema identificado'}"))

    if data["decisiones"]:
        blocks.append(block_heading("Decisiones"))
        for d in data["decisiones"]:
            tag = "" if d["estado_vigencia"] == "Vigente" else " (tentativa)"
            blocks.append(block_bulleted_item(f"{d['decision']}{tag} — {d['razon']}"))

    if data["preguntas_abiertas"]:
        blocks.append(block_heading("Preguntas abiertas"))
        for q in data["preguntas_abiertas"]:
            blocks.append(block_bulleted_item(q))

    if data["proximos_pasos"]:
        blocks.append(block_heading("Próximos pasos"))
        for step in data["proximos_pasos"]:
            blocks.append(block_bulleted_item(step))

    if incluir_tareas and data["tareas"]:
        blocks.append(block_heading("Tareas (ver también base Tareas)"))
        for t in data["tareas"]:
            responsables = ", ".join(t["responsable"]) or "sin asignar"
            blocks.append(block_bulleted_item(f"{t['titulo']} — {responsables} [{t['epica_sugerida']} / {t['area']}]"))

    return blocks


def main():
    argv = sys.argv[1:]
    sin_tareas = "--sin-tareas" in argv
    posicionales = [a for a in argv if a != "--sin-tareas"]

    if len(posicionales) != 1:
        print("Uso: python scripts/04_push_notion.py <ruta_al_staging.json> [--sin-tareas]")
        sys.exit(1)

    staging_path = Path(posicionales[0]).resolve()
    if not staging_path.exists():
        print(f"Error: no existe el archivo {staging_path}")
        sys.exit(1)

    with logged_run("04_push_notion", ROOT) as log_path:
        print(f"📄 Log de esta corrida: {log_path}")
        inicio = time.monotonic()

        cfg = load_config()

        with open(staging_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        reuniones_db = get_database_id(cfg["notion"], "reuniones")
        client = NotionClient()

        with Stage("Creando página de reunión en Notion"):
            reunion_props = build_reunion_properties(data, cfg)
            reunion_blocks = build_reunion_content_blocks(data, incluir_tareas=not sin_tareas)
            reunion_page = client.create_page(reuniones_db, reunion_props, reunion_blocks)
            reunion_page_id = reunion_page["id"]
        print(f"  ✅ Página creada: {reunion_page.get('url', reunion_page_id)}")

        # Grabamos el page_id real en el JSON — lo necesita la reconciliación
        # (esta y cualquier corrida semanal de catch-up futura) para setear
        # "Reunion origen" en lo que cree/actualice a partir de esta reunión.
        data.setdefault("_pipeline_meta", {})["reunion_page_id"] = reunion_page_id

        reconciliacion_mod = importlib.import_module("06_reconciliacion")
        aplicar_mod = importlib.import_module("07_aplicar_cambios")

        candidatos = reconciliacion_mod.candidatos_desde_reunion(
            data, reunion_page_id, incluir_tareas=not sin_tareas
        )
        n_candidatos = sum(len(v) for v in candidatos.values())

        if n_candidatos == 0:
            print("  (Sin ideas, decisiones ni tareas candidatas — nada que reconciliar.)")
        else:
            with Stage(f"Reconciliando {n_candidatos} candidato(s) contra Notion en vivo"):
                bundle = reconciliacion_mod.reconciliar(candidatos, cfg, client)

            with Stage("Aplicando cambios en Notion (Ideas/Decisiones/Tareas)"):
                resultado, bundle_pendiente = aplicar_mod.aplicar_propuesta(bundle, cfg, client)

            resumen = aplicar_mod.formatear_resumen_aplicacion(resultado)
            print(resumen if resumen else "  (Nada nuevo que crear ni actualizar — todo ya existía.)")

            with Stage("Verificando consistencia (tareas/decisiones huérfanas)"):
                chequeo = aplicar_mod.verificar_consistencia(cfg, client)
            print(aplicar_mod.formatear_consistencia(chequeo))

            if bundle_pendiente:
                pendientes_path = aplicar_mod.guardar_pendientes(
                    bundle_pendiente, ROOT / cfg["paths"]["staging_dir"]
                )
                print(
                    f"\n⚠️  Algo quedó pendiente (revisa el resumen de arriba) — guardado en: "
                    f"{pendientes_path}"
                )
                print(
                    "   Resuélvelo (ej. agrega la épica que falta a config.yaml) y corre:\n"
                    f"   python scripts/07_aplicar_cambios.py {pendientes_path}"
                )

        # Mover de staging a processed para no reprocesar por accidente. Se
        # mueve SIEMPRE, incluso si algo quedó pendiente arriba — el acta ya
        # se creó, y lo pendiente ya vive en su propio archivo para reintentar
        # (ver bundle_pendiente arriba); reprocesar este JSON de nuevo
        # duplicaría la reunión.
        processed_dir = ROOT / cfg["paths"]["processed_dir"]
        processed_dir.mkdir(parents=True, exist_ok=True)
        dest = processed_dir / staging_path.name
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        staging_path.unlink()

        md_path = staging_path.with_suffix(".md")
        if md_path.exists():
            shutil.move(str(md_path), str(processed_dir / md_path.name))

        print(f"\n✅ Listo. Reunión procesada y movida a: {dest}")
        print(f"   Tiempo total: {format_duration(time.monotonic() - inicio)}")
        print(f"   Fecha de procesamiento: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
