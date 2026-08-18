#!/usr/bin/env python3
"""
Paso 7 (independiente) — Aplicación automática de la propuesta de decisiones

Uso:
    python scripts/07_aplicar_decisiones.py
    python scripts/07_aplicar_decisiones.py data/staging/decisiones_propuesta_2026-08-12.json

Qué hace:
1. Lee una propuesta generada por 06_decisiones_reconciliacion.py (por
   defecto, la más reciente en data/staging/).
2. Crea una página nueva en la base "Decisiones" por cada entrada en
   "nuevas", con todas las propiedades pobladas (Decision, Tema, Razon,
   Prototipo, Estado, Fecha) y contenido en el cuerpo de la página (origen +
   razón completa) — a diferencia de las decisiones cargadas a mano hasta
   ahora, que quedaron con la página en blanco.
3. Actualiza el Estado de cada página existente listada en
   "actualizaciones", y deja un bloque de texto en el cuerpo de esa página
   con la fecha y la razón del cambio — un historial que hoy no existe ahí.
4. Si todo se aplicó sin errores, mueve el JSON de staging/ a processed/.
   Si algo falló, reescribe el JSON de staging/ dejando SOLO lo que no se
   pudo aplicar — así un reintento no vuelve a crear duplicados de lo que
   ya se escribió bien.

Por qué esto sí escribe en Notion automáticamente (a diferencia del diseño
original de este pipeline, donde 06_decisiones_reconciliacion.py solo
generaba una propuesta para aplicar a mano): decisión explícita del dueño
del pipeline — quiere el flujo semanal completamente automatizado, sin
punto de control humano para decisiones. Como salvaguarda mínima, cada
página que toca este script queda marcada en su contenido como generada o
modificada por el pipeline (ver build_nueva_decision_blocks /
build_actualizacion_blocks), así que si algo se ve mal es fácil rastrear
que vino de acá y no de una carga manual.
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
import shutil
import time
import yaml
from pathlib import Path
from datetime import date
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
    prop_date,
    block_paragraph,
)
from progress import Stage, logged_run, format_duration  # noqa: E402

ESTADOS_VALIDOS = {"Not started", "In progress", "Done"}
ESTADO_POR_DEFECTO = "Not started"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_latest_propuesta(staging_dir: Path) -> Path:
    files = sorted(
        staging_dir.glob("decisiones_propuesta_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise RuntimeError(
            f"No se encontró ninguna propuesta en {staging_dir}. "
            "Corre primero: python scripts/06_decisiones_reconciliacion.py"
        )
    return files[0]


def build_nueva_decision_properties(item: dict, cfg: dict) -> dict:
    p = cfg["notion"]["propiedades_decisiones"]
    estado = item.get("estado_inicial")
    if estado not in ESTADOS_VALIDOS:
        estado = ESTADO_POR_DEFECTO
    props = {
        p["decision"]: prop_title(item["decision"]),
        p["tema"]: prop_rich_text(item.get("tema") or ""),
        p["razon"]: prop_rich_text(item.get("razon") or ""),
        p["estado"]: prop_status(estado),
        p["fecha"]: prop_date(date.today().isoformat()),
    }
    if item.get("prototipo"):
        props[p["prototipo"]] = prop_select(item["prototipo"])
    return props


def build_nueva_decision_blocks(item: dict) -> list:
    # A diferencia de las decisiones cargadas a mano en esta base (todas con
    # la página en blanco), dejamos el origen y la razón completa en el
    # cuerpo — las propiedades de texto de Notion se truncan feo en vista
    # de tabla, el cuerpo de la página no.
    hoy = date.today().isoformat()
    origen = item.get("origen_reunion") or "sin origen registrado"
    return [
        block_paragraph(f"Origen: {origen}"),
        block_paragraph(f"Razón: {item.get('razon') or '(sin razón registrada)'}"),
        block_paragraph(f"Generado automáticamente por el pipeline el {hoy}."),
    ]


def build_actualizacion_blocks(item: dict) -> list:
    hoy = date.today().isoformat()
    razon_cambio = item.get("razon_cambio") or "(sin razón registrada)"
    return [
        block_paragraph(
            f"Actualización automática del pipeline ({hoy}): estado → "
            f"{item['estado_nuevo']} — {razon_cambio}"
        )
    ]


def aplicar_propuesta(propuesta: dict, cfg: dict, client: NotionClient) -> dict:
    """
    Aplica una propuesta ya generada (dict con "nuevas"/"actualizaciones") a
    la base real "Decisiones" en Notion. Devuelve:
    {
        "creadas": [...], "actualizadas": [...], "errores": [str, ...],
        "nuevas_pendientes": [...], "actualizaciones_pendientes": [...],
    }
    Los "*_pendientes" son los items que fallaron, en el mismo formato de
    entrada — sirven para reintentar sin repetir lo que ya se aplicó bien.
    """
    decisiones_db = get_database_id(cfg["notion"], "decisiones")
    resultado = {
        "creadas": [],
        "actualizadas": [],
        "errores": [],
        "nuevas_pendientes": [],
        "actualizaciones_pendientes": [],
    }

    nuevas = propuesta.get("nuevas", [])
    for i, item in enumerate(nuevas, start=1):
        print(f"  [{i}/{len(nuevas)}] Creando decisión: {item.get('decision', '?')}...")
        try:
            props = build_nueva_decision_properties(item, cfg)
            blocks = build_nueva_decision_blocks(item)
            page = client.create_page(decisiones_db, props, blocks)
            resultado["creadas"].append(
                {"decision": item["decision"], "url": page.get("url", page.get("id"))}
            )
        except Exception as e:
            print(f"    ❌ Falló: {e}")
            resultado["errores"].append(f"Crear \"{item.get('decision', '?')}\": {e}")
            resultado["nuevas_pendientes"].append(item)

    p = cfg["notion"]["propiedades_decisiones"]
    actualizaciones = propuesta.get("actualizaciones", [])
    for i, item in enumerate(actualizaciones, start=1):
        print(f"  [{i}/{len(actualizaciones)}] Actualizando decisión: {item.get('decision_existente', '?')}...")
        try:
            estado_nuevo = item.get("estado_nuevo")
            if estado_nuevo not in ESTADOS_VALIDOS:
                raise ValueError(f"estado_nuevo inválido: {estado_nuevo!r}")
            page_id = item["decision_id"]
            client.update_page(page_id, {p["estado"]: prop_status(estado_nuevo)})
            client.append_block_children(page_id, build_actualizacion_blocks(item))
            resultado["actualizadas"].append(
                {"decision": item.get("decision_existente", page_id), "estado_nuevo": estado_nuevo}
            )
        except Exception as e:
            print(f"    ❌ Falló: {e}")
            resultado["errores"].append(
                f"Actualizar \"{item.get('decision_existente', '?')}\": {e}"
            )
            resultado["actualizaciones_pendientes"].append(item)

    return resultado


def formatear_resumen_aplicacion(resultado: dict) -> str:
    """Devuelve "" si no hubo nada que aplicar ni errores."""
    creadas = resultado.get("creadas", [])
    actualizadas = resultado.get("actualizadas", [])
    errores = resultado.get("errores", [])
    if not creadas and not actualizadas and not errores:
        return ""

    lineas = ["📋 Decisiones (Notion, aplicado automáticamente):"]
    if creadas:
        lineas.append(f"- {len(creadas)} nueva(s): " + "; ".join(c["decision"] for c in creadas))
    if actualizadas:
        lineas.append(
            f"- {len(actualizadas)} actualizada(s): "
            + "; ".join(f"{a['decision']} → {a['estado_nuevo']}" for a in actualizadas)
        )
    if errores:
        lineas.append(f"- ⚠️ {len(errores)} error(es), revisa el log: " + "; ".join(errores))
    return "\n".join(lineas)


def main():
    if len(sys.argv) == 2:
        propuesta_path = Path(sys.argv[1]).resolve()
        if not propuesta_path.exists():
            print(f"Error: no existe el archivo {propuesta_path}")
            sys.exit(1)
    elif len(sys.argv) != 1:
        print("Uso: python scripts/07_aplicar_decisiones.py [ruta_a_propuesta.json]")
        sys.exit(1)
    else:
        propuesta_path = None  # se resuelve adentro, ya con logging activo

    with logged_run("07_aplicar_decisiones", ROOT) as log_path:
        print(f"📄 Log de esta corrida: {log_path}")
        inicio = time.monotonic()

        cfg = load_config()
        client = NotionClient()

        if propuesta_path is None:
            propuesta_path = find_latest_propuesta(ROOT / cfg["paths"]["staging_dir"])
            print(f"Usando la propuesta más reciente: {propuesta_path}")

        with open(propuesta_path, "r", encoding="utf-8") as f:
            propuesta = json.load(f)

        n_total = len(propuesta.get("nuevas", [])) + len(propuesta.get("actualizaciones", []))
        with Stage(f"Aplicando propuesta a la base 'Decisiones' en Notion ({n_total} ítem(s))"):
            resultado = aplicar_propuesta(propuesta, cfg, client)

        resumen = formatear_resumen_aplicacion(resultado)
        print(resumen if resumen else "Nada que aplicar (propuesta vacía).")
        print(f"Tiempo total: {format_duration(time.monotonic() - inicio)}")

        if resultado["errores"]:
            pendiente = {
                "nuevas": resultado["nuevas_pendientes"],
                "actualizaciones": resultado["actualizaciones_pendientes"],
            }
            with open(propuesta_path, "w", encoding="utf-8") as f:
                json.dump(pendiente, f, ensure_ascii=False, indent=2)
            print(
                f"\n⚠️  Hubo errores — {propuesta_path} quedó reescrito solo con lo "
                "pendiente. Corrígelo y vuelve a correr este script para reintentar."
            )
            sys.exit(1)

        processed_dir = ROOT / cfg["paths"]["processed_dir"]
        processed_dir.mkdir(parents=True, exist_ok=True)
        dest = processed_dir / propuesta_path.name
        shutil.move(str(propuesta_path), str(dest))
        print(f"\n✅ Propuesta aplicada por completo y movida a: {dest}")


if __name__ == "__main__":
    main()
