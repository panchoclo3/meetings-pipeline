#!/usr/bin/env python3
"""
Paso 5 (independiente) — Resumen semanal en Notion

Uso:
    python scripts/05_weekly_digest.py

Qué hace:
1. Consulta la base "Reuniones" en Notion filtrando por reuniones de los
   últimos 7 días.
2. Lee el contenido (bloques) de cada página encontrada — el resumen
   ejecutivo, decisiones, tareas, etc. ya quedaron ahí en el paso 4.
3. Le pide a Claude un resumen semanal en texto plano.
4. Corre la reconciliación de decisiones (paso 6) y aplica la propuesta
   automáticamente en Notion (paso 7 — ver 07_aplicar_decisiones.py), y
   agrega al mismo resumen un recuento de qué se creó/actualizó.
5. Agrega el texto resultante como bloque nuevo (con la fecha como
   encabezado) al final de la página "Resúmenes semanales", creándola si no
   existe.

Por qué es un script independiente: no depende de WhisperX ni de audio —
solo hace llamadas HTTP a Notion y Claude, así que puede correr en un
cron/tarea programada sin el resto del entorno de transcripción.

Filosofía del pipeline aplicada aquí: este script NUNCA modifica una
reunión ya escrita — solo lee reuniones y agrega contenido nuevo (un
resumen) al final de una página dedicada a resúmenes. Las decisiones, en
cambio, sí se crean/actualizan automáticamente desde acá (ver paso 7) —
ese es el único punto donde este pipeline escribe fuera de "solo agregar".
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPT_PATH = ROOT / "prompts" / "weekly_digest_prompt.txt"

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_client import (  # noqa: E402
    NotionClient,
    get_database_id,
    block_plain_text,
    page_title_plain_text,
    block_heading,
    block_paragraph,
    block_bulleted_item,
)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_meetings_last_week(client: NotionClient, cfg: dict) -> list:
    """Consulta la base Reuniones filtrando por Fecha >= hace 7 días."""
    p = cfg["notion"]["propiedades_reuniones"]
    hace_7_dias = (datetime.now() - timedelta(days=7)).date().isoformat()
    filter_ = {"property": p["fecha"], "date": {"on_or_after": hace_7_dias}}
    sorts = [{"property": p["fecha"], "direction": "ascending"}]
    reuniones_db = get_database_id(cfg["notion"], "reuniones")
    return client.query_database(reuniones_db, filter_, sorts)


def count_tareas_responsable_sin_resolver(client: NotionClient, cfg: dict) -> int:
    """
    Cuenta cuántas tareas en la base Tareas todavía tienen un responsable sin
    resolver a user ID de Notion (ver scripts/04_push_notion.py::resolver_persona,
    que deja constancia de esto en la propiedad "Notas" al crear la tarea).
    Es una foto del estado actual en Notion, no solo de esta semana — nadie
    edita "Notas" para "resolverla" automáticamente, así que sirve como
    recordatorio recurrente hasta que se complete a mano.
    """
    p = cfg["notion"]["propiedades_tareas"]
    tareas_db = get_database_id(cfg["notion"], "tareas")
    filter_ = {
        "property": p["notas"],
        "rich_text": {"contains": "Responsable sin resolver"},
    }
    try:
        return len(client.query_database(tareas_db, filter_))
    except RuntimeError as e:
        print(f"  ⚠️  No se pudo consultar tareas con responsable sin resolver: {e}")
        return 0


def build_meeting_content(client: NotionClient, page: dict, cfg: dict) -> str:
    """Arma un bloque de texto plano con el título, fecha y contenido de una reunión."""
    p = cfg["notion"]["propiedades_reuniones"]
    titulo = page_title_plain_text(page, p["titulo"]) or "(sin título)"
    fecha_prop = page.get("properties", {}).get(p["fecha"], {}).get("date")
    fecha = fecha_prop["start"] if fecha_prop else "(sin fecha)"

    blocks = client.get_block_children(page["id"])
    texto_bloques = "\n".join(t for t in (block_plain_text(b) for b in blocks) if t.strip())

    return f"### {titulo} ({fecha})\n{texto_bloques}"


def build_prompt(contenido_reuniones: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(contenido_reuniones=contenido_reuniones)


def call_claude(prompt: str, cfg: dict) -> str:
    import anthropic

    client = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY del entorno
    response = client.messages.create(
        model=cfg["claude"]["model"],
        max_tokens=cfg["claude"]["max_tokens"],
        temperature=cfg["claude"]["temperature"],
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def find_or_create_resumenes_page(client: NotionClient, cfg: dict) -> str:
    """
    Busca la página "Resúmenes semanales" bajo la página padre configurada
    (por defecto "Ventana Celeste"). Si no existe, la crea. Devuelve su ID.
    """
    rs_cfg = cfg["resumen_semanal"]
    nombre_padre = rs_cfg["pagina_padre"]
    nombre_resumenes = rs_cfg["pagina_resumenes"]

    padre_resultados = [
        r for r in client.search(nombre_padre, object_type="page")
        if page_title_plain_text(r) == nombre_padre
    ]
    if not padre_resultados:
        raise RuntimeError(
            f'No se encontró la página "{nombre_padre}" compartida con la integración. '
            "Verifica que exista en Notion y que esté compartida (ver README.md)."
        )
    padre_id = padre_resultados[0]["id"]

    resumenes_resultados = [
        r for r in client.search(nombre_resumenes, object_type="page")
        if page_title_plain_text(r) == nombre_resumenes
        and r.get("parent", {}).get("page_id") == padre_id
    ]
    if resumenes_resultados:
        return resumenes_resultados[0]["id"]

    print(f'  "{nombre_resumenes}" no existe todavía bajo "{nombre_padre}" — creándola...')
    nueva_pagina = client.create_subpage(padre_id, nombre_resumenes)
    return nueva_pagina["id"]


def append_digest_to_notion(client: NotionClient, page_id: str, digest_text: str) -> None:
    hoy = datetime.now().date().isoformat()
    blocks = [block_heading(f"Resumen semanal — {hoy}", level=3)]
    for linea in digest_text.split("\n"):
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith("- "):
            blocks.append(block_bulleted_item(linea[2:]))
        else:
            blocks.append(block_paragraph(linea))
    client.append_block_children(page_id, blocks)


def main():
    cfg = load_config()
    client = NotionClient()

    print("Buscando reuniones de los últimos 7 días...")
    meetings = get_meetings_last_week(client, cfg)
    print(f"  {len(meetings)} reunión(es) encontrada(s).")

    if meetings:
        contenido = "\n\n".join(build_meeting_content(client, m, cfg) for m in meetings)
    else:
        contenido = "(No hubo reuniones registradas en los últimos 7 días.)"

    print("Generando resumen con Claude...")
    prompt = build_prompt(contenido)
    digest = call_claude(prompt, cfg)

    print("Revisando tareas con responsable sin resolver...")
    sin_resolver = count_tareas_responsable_sin_resolver(client, cfg)
    if sin_resolver:
        digest += (
            f"\n\n⚠️ {sin_resolver} tarea(s) en Notion con responsable sin "
            "resolver (ver campo Notas) — complétalas a mano o agrega el "
            "user ID en config.yaml → notion.resolucion_personas."
        )

    # Reconciliación de decisiones (paso 6) + aplicación automática (paso 7):
    # a diferencia del resto de este script (que solo lee reuniones y agrega
    # texto), esto sí escribe en la base "Decisiones" — ver docstring de
    # 07_aplicar_decisiones.py para la justificación de ese cambio de diseño.
    try:
        import importlib
        decisiones_mod = importlib.import_module("06_decisiones_reconciliacion")
        aplicar_mod = importlib.import_module("07_aplicar_decisiones")
        print("Generando propuesta de reconciliación de decisiones...")
        propuesta = decisiones_mod.ejecutar_reconciliacion(cfg, client)
        print("Aplicando propuesta de decisiones en Notion...")
        resultado = aplicar_mod.aplicar_propuesta(propuesta, cfg, client)
        resumen_decisiones = aplicar_mod.formatear_resumen_aplicacion(resultado)
        if resumen_decisiones:
            digest = f"{digest}\n\n{resumen_decisiones}"
    except Exception as e:
        print(f"  ⚠️  Reconciliación/aplicación de decisiones omitida ({e}).")

    print("Guardando resumen en la página 'Resúmenes semanales' de Notion...")
    page_id = find_or_create_resumenes_page(client, cfg)
    append_digest_to_notion(client, page_id, digest)
    print("  ✅ Resumen agregado a Notion.")

    print("\n✅ Resumen semanal completo.")


if __name__ == "__main__":
    main()
