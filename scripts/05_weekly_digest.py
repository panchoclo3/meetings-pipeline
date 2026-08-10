#!/usr/bin/env python3
"""
Paso 5 (independiente) — Resumen semanal por Telegram

Uso:
    python scripts/05_weekly_digest.py

Qué hace:
1. Consulta la base "Reuniones" en Notion filtrando por reuniones de los
   últimos 7 días.
2. Lee el contenido (bloques) de cada página encontrada — el resumen
   ejecutivo, decisiones, tareas, etc. ya quedaron ahí en el paso 4.
3. Le pide a Claude un resumen semanal en texto plano, apto para Telegram.
4. Si `scripts/06_decisiones_reconciliacion.py` está configurado, le agrega
   al mismo mensaje una propuesta de decisiones nuevas/actualizadas (nunca
   escribe en Notion por su cuenta — ver ese script).
5. Envía el mensaje resultante por la API de Telegram.
6. Agrega el mismo texto como bloque nuevo (con la fecha como encabezado) al
   final de la página "Resúmenes semanales", creándola si no existe.

Por qué es un script independiente: no depende de WhisperX ni de audio —
solo hace llamadas HTTP a Notion, Claude y Telegram, así que puede correr
en un cron/tarea programada sin el resto del entorno de transcripción.

Filosofía del pipeline aplicada aquí: este script NUNCA modifica una
reunión, tarea o decisión ya escrita — solo lee reuniones y agrega contenido
nuevo (un resumen) al final de una página dedicada a resúmenes.
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import os
import requests
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


def send_telegram_message(text: str, cfg: dict) -> None:
    bot_token = os.environ.get(cfg["telegram"]["bot_token_env"])
    chat_id = os.environ.get(cfg["telegram"]["chat_id_env"])
    if not bot_token or not chat_id:
        raise RuntimeError(
            f"Faltan {cfg['telegram']['bot_token_env']} y/o {cfg['telegram']['chat_id_env']} "
            "en el entorno. Ver README.md sección 'Setup de Telegram'."
        )
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # parse_mode "Markdown" (legado) es el que soporta *negrita*/_cursiva_ tal
    # como se le pide al modelo en el prompt, sin el escapado estricto de caracteres
    # que exige "MarkdownV2".
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    if resp.status_code >= 400:
        raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text}")


def find_or_create_resumenes_page(client: NotionClient, cfg: dict) -> str:
    """
    Busca la página "Resúmenes semanales" bajo la página padre configurada
    (por defecto "Ventana Celeste"). Si no existe, la crea. Devuelve su ID.
    """
    tg_cfg = cfg["telegram"]
    nombre_padre = tg_cfg["pagina_padre"]
    nombre_resumenes = tg_cfg["pagina_resumenes"]

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
    for parrafo in digest_text.split("\n"):
        if parrafo.strip():
            blocks.append(block_paragraph(parrafo.strip()))
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

    # Reconciliación de decisiones (paso 6, opcional): si el módulo está
    # disponible, se agrega su propuesta al MISMO mensaje semanal (nunca se
    # escribe en Notion desde aquí — ver 06_decisiones_reconciliacion.py).
    try:
        import importlib
        decisiones_mod = importlib.import_module("06_decisiones_reconciliacion")
        print("Generando propuesta de reconciliación de decisiones...")
        propuesta_texto = decisiones_mod.ejecutar_reconciliacion(cfg)
        if propuesta_texto:
            digest = f"{digest}\n\n{propuesta_texto}"
    except Exception as e:
        print(f"  ⚠️  Reconciliación de decisiones omitida ({e}).")

    print("Enviando mensaje a Telegram...")
    send_telegram_message(digest, cfg)
    print("  ✅ Mensaje enviado.")

    print("Guardando resumen en la página 'Resúmenes semanales' de Notion...")
    page_id = find_or_create_resumenes_page(client, cfg)
    append_digest_to_notion(client, page_id, digest)
    print("  ✅ Resumen agregado a Notion.")

    print("\n✅ Resumen semanal completo.")


if __name__ == "__main__":
    main()
