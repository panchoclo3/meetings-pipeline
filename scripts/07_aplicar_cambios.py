#!/usr/bin/env python3
"""
Aplicación automática de la propuesta de reconciliación — Ideas, Decisiones, Tareas

Uso:
    python scripts/07_aplicar_cambios.py
    python scripts/07_aplicar_cambios.py data/staging/reconciliacion_propuesta_20260819_120000.json

Qué hace:
1. Lee un bundle {"candidatos": ..., "propuesta": ...} generado por
   scripts/06_reconciliacion.py (por defecto, el más reciente en
   data/staging/).
2. Crea páginas nuevas en Ideas/Decisiones/Tareas para cada "nueva" de la
   propuesta, y actualiza estado en las páginas existentes listadas en
   "actualizaciones". Reglas duras (ver README):
   - Toda tarea nueva lleva Reunion origen + Tarea madre (épica) + Área +
     Prototipo. Si la épica sugerida no resuelve a un ID real (candidato
     "NINGUNA_ENCAJA" o código desconocido), la tarea NO se crea — queda
     pendiente con el motivo, nunca huérfana.
   - Toda decisión nueva lleva Reunion origen + Decision madre + Razon +
     Estado de vigencia. Si reemplaza a una anterior, la anterior se marca
     Superada y se enlaza Reemplazada por — desde el lado hijo (la anterior).
   - Self-relations (Tarea madre, Decision madre, Reemplazada por) se
     escriben SIEMPRE desde el lado hijo, nunca desde la épica/madre.
3. Corre 3 queries de consistencia (tareas huérfanas, decisiones huérfanas,
   decisiones Superada sin reemplazo declarado) y reporta los conteos.
4. Si todo se aplicó sin errores, mueve el bundle de staging/ a processed/.
   Si algo falló o quedó pendiente (típicamente: ninguna épica encaja), el
   bundle se reescribe SOLO con lo pendiente — un reintento no repite lo que
   ya se aplicó bien ni duplica nada.

Por qué esto escribe en Notion automáticamente (a diferencia del diseño
original de este pipeline): decisión explícita del dueño del pipeline — el
único punto de revisión humana es el paso 3 (staging), todo lo de acá en
adelante corre sin intervención. Como salvaguarda, cada página que este
script crea o modifica queda marcada en su contenido como generada por el
pipeline, para poder distinguirla de una carga manual si algo se ve raro.
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
from datetime import date, datetime
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
    block_paragraph,
)
from progress import Stage, logged_run, format_duration  # noqa: E402


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_latest_bundle(staging_dir: Path) -> Path:
    files = sorted(
        staging_dir.glob("reconciliacion_propuesta_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise RuntimeError(
            f"No se encontró ningún bundle de reconciliación en {staging_dir}. "
            "Corre primero: python scripts/06_reconciliacion.py"
        )
    return files[0]


# ---------------------------------------------------------------------------
# Resolución de personas (movido acá desde 04_push_notion.py — ahora es acá
# donde se crean las tareas).
# ---------------------------------------------------------------------------

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
    Resuelve un nombre en texto libre al user ID real de Notion. Prioridad:
    1. config.yaml -> notion.resolucion_personas (mapeo editado a mano).
    2. Búsqueda por nombre entre los usuarios que devuelve la API.
    Devuelve None si ninguna de las dos fuentes lo resuelve.
    """
    mapeo = cfg["notion"].get("resolucion_personas") or {}
    user_id = mapeo.get(nombre)
    if user_id:
        return user_id
    return api_users_by_name.get(nombre)


def resolver_responsables(nombres: list, cfg: dict, api_users_by_name: dict) -> tuple:
    resueltos, no_resueltos = [], []
    for nombre in nombres:
        user_id = resolver_persona(nombre, cfg, api_users_by_name)
        (resueltos if user_id else no_resueltos).append(user_id if user_id else nombre)
    return resueltos, no_resueltos


# ---------------------------------------------------------------------------
# Resolución de catálogos cerrados (épicas / decisiones madre)
# ---------------------------------------------------------------------------

def resolver_epica(codigo: str, cfg: dict):
    info = cfg["notion"]["epicas"].get(codigo)
    return info["id"] if info else None


def resolver_decision_madre(codigo: str, cfg: dict):
    info = cfg["notion"]["decisiones_madre"].get(codigo)
    return info["id"] if info else None


# ---------------------------------------------------------------------------
# Ideas
# ---------------------------------------------------------------------------

def build_nueva_idea_properties(item: dict, cfg: dict) -> dict:
    p = cfg["notion"]["propiedades_ideas"]
    props = {
        p["idea"]: prop_title(item["idea"]),
        p["estado"]: prop_select(item.get("estado") or "Propuesta"),
    }
    if item.get("problema_que_resuelve"):
        props[p["problema_que_resuelve"]] = prop_rich_text(item["problema_que_resuelve"])
    if item.get("horizonte"):
        props[p["horizonte"]] = prop_select(item["horizonte"])
    if item.get("encaje_filosofico"):
        props[p["encaje_filosofico"]] = prop_select(item["encaje_filosofico"])
    if item.get("prototipo"):
        props[p["prototipo"]] = prop_multi_select(item["prototipo"])
    if item.get("esfuerzo"):
        props[p["esfuerzo"]] = prop_select(item["esfuerzo"])
    origen = item.get("_origen_reunion_titulo")
    if origen:
        # "Origen" es texto libre en esta base — no existe relation a
        # Reuniones (confirmado contra el esquema real).
        props[p["origen"]] = prop_rich_text(origen)
    return props


def build_generado_por_pipeline_block(extra: str = "") -> dict:
    hoy = date.today().isoformat()
    texto = f"Generado automáticamente por el pipeline el {hoy}."
    if extra:
        texto = f"{extra} {texto}"
    return block_paragraph(texto)


def aplicar_ideas(candidatos: list, propuesta: dict, cfg: dict, client: NotionClient) -> tuple:
    p = cfg["notion"]["propiedades_ideas"]
    ideas_db = get_database_id(cfg["notion"], "ideas")

    creadas, errores, nuevas_pendientes = [], [], []
    nuevas = propuesta.get("nuevas", [])
    for i, ref in enumerate(nuevas, start=1):
        item = candidatos[ref["indice"]]
        print(f"  [{i}/{len(nuevas)}] Creando idea: {item.get('idea', '?')}...")
        try:
            props = build_nueva_idea_properties(item, cfg)
            blocks = [build_generado_por_pipeline_block()]
            page = client.create_page(ideas_db, props, blocks)
            creadas.append({"idea": item["idea"], "url": page.get("url", page.get("id"))})
        except Exception as e:
            print(f"    ❌ Falló: {e}")
            errores.append(f"Crear idea \"{item.get('idea', '?')}\": {e}")
            nuevas_pendientes.append(ref)

    actualizadas, actualizaciones_pendientes = [], []
    actualizaciones = propuesta.get("actualizaciones", [])
    for i, item in enumerate(actualizaciones, start=1):
        print(f"  [{i}/{len(actualizaciones)}] Actualizando idea: {item.get('idea_existente', '?')}...")
        try:
            estado_nuevo = item["estado_nuevo"]
            page_id = item["idea_id"]
            client.update_page(page_id, {p["estado"]: prop_select(estado_nuevo)})
            client.append_block_children(page_id, [build_generado_por_pipeline_block(
                f"Actualizado a '{estado_nuevo}' — {item.get('razon_cambio', '(sin razón registrada)')}."
            )])
            actualizadas.append({"idea": item.get("idea_existente", page_id), "estado_nuevo": estado_nuevo})
        except Exception as e:
            print(f"    ❌ Falló: {e}")
            errores.append(f"Actualizar idea \"{item.get('idea_existente', '?')}\": {e}")
            actualizaciones_pendientes.append(item)

    resultado = {"creadas": creadas, "actualizadas": actualizadas, "errores": errores}
    pendiente = {"nuevas": nuevas_pendientes, "actualizaciones": actualizaciones_pendientes}
    return resultado, pendiente


# ---------------------------------------------------------------------------
# Decisiones
# ---------------------------------------------------------------------------

def build_nueva_decision_properties(item: dict, cfg: dict, reunion_page_id) -> dict:
    p = cfg["notion"]["propiedades_decisiones"]
    props = {
        p["decision"]: prop_title(item["decision"]),
        p["tema"]: prop_rich_text(item.get("tema") or ""),
        p["razon"]: prop_rich_text(item.get("razon") or ""),
        # "Estado" en Decisiones es tipo select (vigencia), no status —
        # distinto de "Estado" en Tareas.
        p["estado"]: prop_select(item.get("estado_vigencia") or "Vigente"),
        p["fecha"]: prop_date(date.today().isoformat()),
    }
    if item.get("prototipo"):
        props[p["prototipo"]] = prop_select(item["prototipo"])
    if reunion_page_id:
        props[p["reunion_origen"]] = prop_relation([reunion_page_id])
    madre_id = resolver_decision_madre(item.get("decision_madre_sugerida"), cfg)
    if madre_id:
        props[p["decision_madre"]] = prop_relation([madre_id])
    return props


def build_nueva_decision_blocks(item: dict) -> list:
    # A diferencia de las decisiones cargadas a mano en esta base (todas con
    # la página en blanco), dejamos el origen y la razón completa en el
    # cuerpo — las propiedades de texto de Notion se truncan feo en vista
    # de tabla, el cuerpo de la página no.
    origen = item.get("_origen_reunion_titulo") or "sin origen registrado"
    return [
        block_paragraph(f"Origen: {origen}"),
        block_paragraph(f"Razón: {item.get('razon') or '(sin razón registrada)'}"),
        build_generado_por_pipeline_block(),
    ]


def marcar_decision_superada(reemplaza_id: str, nueva_page_id: str, nueva_titulo: str, cfg: dict, client: NotionClient) -> None:
    """
    Nunca se sobrescribe una decisión: la que queda obsoleta se marca
    Superada y su "Reemplazada por" (lado hijo = la que queda obsoleta)
    apunta a la nueva.
    """
    p = cfg["notion"]["propiedades_decisiones"]
    client.update_page(
        reemplaza_id,
        {
            p["estado"]: prop_select("Superada"),
            p["reemplazada_por"]: prop_relation([nueva_page_id]),
        },
    )
    hoy = date.today().isoformat()
    client.append_block_children(
        reemplaza_id,
        [block_paragraph(f"Superada automáticamente por el pipeline el {hoy} — reemplazada por: {nueva_titulo}.")],
    )


def aplicar_decisiones(candidatos: list, propuesta: dict, cfg: dict, client: NotionClient) -> tuple:
    p = cfg["notion"]["propiedades_decisiones"]
    decisiones_db = get_database_id(cfg["notion"], "decisiones")

    creadas, errores, nuevas_pendientes = [], [], []
    nuevas = propuesta.get("nuevas", [])
    for i, ref in enumerate(nuevas, start=1):
        item = candidatos[ref["indice"]]
        print(f"  [{i}/{len(nuevas)}] Creando decisión: {item.get('decision', '?')}...")
        try:
            reunion_page_id = item.get("_origen_reunion_page_id")
            props = build_nueva_decision_properties(item, cfg, reunion_page_id)
            page = client.create_page(decisiones_db, props, build_nueva_decision_blocks(item))
            creadas.append({"decision": item["decision"], "url": page.get("url", page.get("id"))})

            reemplaza_id = ref.get("reemplaza_decision_id")
            if reemplaza_id:
                try:
                    marcar_decision_superada(reemplaza_id, page["id"], item["decision"], cfg, client)
                except Exception as e:
                    print(f"    ⚠️  Se creó la decisión pero no se pudo marcar Superada la anterior: {e}")
                    errores.append(
                        f"Marcar Superada \"{reemplaza_id}\" (reemplazada por \"{item['decision']}\"): {e}"
                    )
        except Exception as e:
            print(f"    ❌ Falló: {e}")
            errores.append(f"Crear decisión \"{item.get('decision', '?')}\": {e}")
            nuevas_pendientes.append(ref)

    actualizadas, actualizaciones_pendientes = [], []
    actualizaciones = propuesta.get("actualizaciones", [])
    for i, item in enumerate(actualizaciones, start=1):
        print(f"  [{i}/{len(actualizaciones)}] Actualizando decisión: {item.get('decision_existente', '?')}...")
        try:
            estado_nuevo = item["estado_nuevo"]
            page_id = item["decision_id"]
            client.update_page(page_id, {p["estado"]: prop_select(estado_nuevo)})
            razon_cambio = item.get("razon_cambio") or "(sin razón registrada)"
            hoy = date.today().isoformat()
            client.append_block_children(page_id, [
                block_paragraph(f"Actualización automática del pipeline ({hoy}): estado → {estado_nuevo} — {razon_cambio}")
            ])
            actualizadas.append({"decision": item.get("decision_existente", page_id), "estado_nuevo": estado_nuevo})
        except Exception as e:
            print(f"    ❌ Falló: {e}")
            errores.append(f"Actualizar decisión \"{item.get('decision_existente', '?')}\": {e}")
            actualizaciones_pendientes.append(item)

    resultado = {"creadas": creadas, "actualizadas": actualizadas, "errores": errores}
    pendiente = {"nuevas": nuevas_pendientes, "actualizaciones": actualizaciones_pendientes}
    return resultado, pendiente


# ---------------------------------------------------------------------------
# Tareas
# ---------------------------------------------------------------------------

def build_nueva_tarea_properties(item: dict, cfg: dict, reunion_page_id, epica_id: str, api_users_by_name: dict) -> tuple:
    """Devuelve (props, nombres_sin_resolver) — ver resolver_responsables()."""
    p = cfg["notion"]["propiedades_tareas"]
    props = {
        p["titulo"]: prop_title(item["titulo"]),
        p["estado"]: prop_status("Not started"),
        p["area"]: prop_select(item["area"]),
        p["tarea_madre"]: prop_relation([epica_id]),
    }
    if item.get("prioridad"):
        props[p["prioridad"]] = prop_select(item["prioridad"].capitalize())
    if item.get("prototipo"):
        props[p["prototipo"]] = prop_select(item["prototipo"])
    if reunion_page_id:
        props[p["reunion_origen"]] = prop_relation([reunion_page_id])

    resueltos, no_resueltos = resolver_responsables(item.get("responsable", []), cfg, api_users_by_name)
    if resueltos:
        props[p["responsable"]] = prop_people(resueltos)
    if no_resueltos:
        nota = " ".join(f'[Responsable sin resolver: "{n}"]' for n in no_resueltos)
        props[p["notas"]] = prop_rich_text(nota)
    return props, no_resueltos


def aplicar_tareas(candidatos: list, propuesta: dict, cfg: dict, client: NotionClient, api_users_by_name: dict) -> tuple:
    tareas_db = get_database_id(cfg["notion"], "tareas")

    creadas, errores, nuevas_pendientes = [], [], []
    sin_responsable_resuelto = []
    nuevas = propuesta.get("nuevas", [])
    for i, ref in enumerate(nuevas, start=1):
        item = candidatos[ref["indice"]]
        print(f"  [{i}/{len(nuevas)}] Creando tarea: {item.get('titulo', '?')}...")

        epica_id = resolver_epica(item.get("epica_sugerida"), cfg)
        if not epica_id:
            motivo = item.get("justificacion_epica") or "ninguna épica del catálogo encaja"
            print(f"    ⏸️  Sin épica resuelta ('{item.get('epica_sugerida')}') — {motivo}")
            nuevas_pendientes.append(ref)
            continue

        try:
            reunion_page_id = item.get("_origen_reunion_page_id")
            props, no_resueltos = build_nueva_tarea_properties(
                item, cfg, reunion_page_id, epica_id, api_users_by_name
            )
            page = client.create_page(tareas_db, props)
            creadas.append({"titulo": item["titulo"], "url": page.get("url", page.get("id"))})
            if no_resueltos:
                sin_responsable_resuelto.append((item["titulo"], no_resueltos))
        except Exception as e:
            print(f"    ❌ Falló: {e}")
            errores.append(f"Crear tarea \"{item.get('titulo', '?')}\": {e}")
            nuevas_pendientes.append(ref)

    resultado = {
        "creadas": creadas,
        "duplicadas": propuesta.get("duplicadas", []),
        "errores": errores,
        "sin_responsable_resuelto": sin_responsable_resuelto,
    }
    pendiente = {"nuevas": nuevas_pendientes, "duplicadas": []}
    return resultado, pendiente


# ---------------------------------------------------------------------------
# Orquestación + consistencia
# ---------------------------------------------------------------------------

def aplicar_propuesta(bundle: dict, cfg: dict, client: NotionClient) -> tuple:
    """
    Aplica un bundle {"candidatos": ..., "propuesta": ...} completo.
    Devuelve (resultado, bundle_pendiente) — bundle_pendiente es None si
    todo se aplicó sin dejar nada pendiente, o un bundle con la misma forma
    (candidatos sin tocar + propuesta recortada a solo lo pendiente) listo
    para guardar y reintentar.
    """
    candidatos = bundle["candidatos"]
    propuesta = bundle["propuesta"]

    resultado_ideas, pendiente_ideas = aplicar_ideas(candidatos["ideas"], propuesta["ideas"], cfg, client)
    resultado_decisiones, pendiente_decisiones = aplicar_decisiones(
        candidatos["decisiones"], propuesta["decisiones"], cfg, client
    )

    if propuesta["tareas"].get("nuevas"):
        api_users_by_name = fetch_api_users_by_name(client)
    else:
        api_users_by_name = {}
    resultado_tareas, pendiente_tareas = aplicar_tareas(
        candidatos["tareas"], propuesta["tareas"], cfg, client, api_users_by_name
    )

    resultado = {"ideas": resultado_ideas, "decisiones": resultado_decisiones, "tareas": resultado_tareas}

    hay_pendientes = bool(
        pendiente_ideas["nuevas"] or pendiente_ideas["actualizaciones"]
        or pendiente_decisiones["nuevas"] or pendiente_decisiones["actualizaciones"]
        or pendiente_tareas["nuevas"]
    )
    bundle_pendiente = None
    if hay_pendientes:
        bundle_pendiente = {
            "candidatos": candidatos,
            "propuesta": {"ideas": pendiente_ideas, "decisiones": pendiente_decisiones, "tareas": pendiente_tareas},
        }
    return resultado, bundle_pendiente


def verificar_consistencia(cfg: dict, client: NotionClient) -> dict:
    """
    Las 3 queries de auditoría: tareas huérfanas (deberían ser solo las
    épicas), decisiones huérfanas (deberían ser solo D1-D4), y decisiones
    Superada sin "Reemplazada por" declarado (inconsistencia). Es la
    verificación programática de "el acta cuadra con lo que se creó".
    """
    pt = cfg["notion"]["propiedades_tareas"]
    pd = cfg["notion"]["propiedades_decisiones"]
    tareas_db = get_database_id(cfg["notion"], "tareas")
    decisiones_db = get_database_id(cfg["notion"], "decisiones")

    tareas_huerfanas = client.query_database(tareas_db, {
        "and": [
            {"property": pt["tarea_madre"], "relation": {"is_empty": True}},
            {"property": pt["prototipo"], "select": {"is_not_empty": True}},
        ]
    })
    decisiones_huerfanas = client.query_database(decisiones_db, {
        "property": pd["decision_madre"], "relation": {"is_empty": True}
    })
    superadas_sin_reemplazo = client.query_database(decisiones_db, {
        "and": [
            {"property": pd["estado"], "select": {"equals": "Superada"}},
            {"property": pd["reemplazada_por"], "relation": {"is_empty": True}},
        ]
    })
    return {
        "tareas_huerfanas": len(tareas_huerfanas),
        "decisiones_huerfanas": len(decisiones_huerfanas),
        "superadas_sin_reemplazo": len(superadas_sin_reemplazo),
    }


def formatear_consistencia(chequeo: dict) -> str:
    return (
        f"🔍 Consistencia Notion: {chequeo['tareas_huerfanas']} tarea(s) huérfana(s) sin épica, "
        f"{chequeo['decisiones_huerfanas']} decisión(es) huérfana(s) sin decisión madre, "
        f"{chequeo['superadas_sin_reemplazo']} decisión(es) Superada(s) sin reemplazo declarado."
    )


def formatear_resumen_aplicacion(resultado: dict) -> str:
    """Devuelve "" si no hubo nada que aplicar ni errores."""
    ideas, decisiones, tareas = resultado["ideas"], resultado["decisiones"], resultado["tareas"]

    hay_algo = any([
        ideas["creadas"], ideas["actualizadas"], ideas["errores"],
        decisiones["creadas"], decisiones["actualizadas"], decisiones["errores"],
        tareas["creadas"], tareas.get("duplicadas"), tareas["errores"],
    ])
    if not hay_algo:
        return ""

    lineas = ["📋 Notion (aplicado automáticamente):"]
    if ideas["creadas"]:
        lineas.append(f"- {len(ideas['creadas'])} idea(s) nueva(s): " + "; ".join(c["idea"] for c in ideas["creadas"]))
    if ideas["actualizadas"]:
        lineas.append(
            f"- {len(ideas['actualizadas'])} idea(s) actualizada(s): "
            + "; ".join(f"{a['idea']} → {a['estado_nuevo']}" for a in ideas["actualizadas"])
        )
    if decisiones["creadas"]:
        lineas.append(f"- {len(decisiones['creadas'])} decisión(es) nueva(s): " + "; ".join(c["decision"] for c in decisiones["creadas"]))
    if decisiones["actualizadas"]:
        lineas.append(
            f"- {len(decisiones['actualizadas'])} decisión(es) actualizada(s): "
            + "; ".join(f"{a['decision']} → {a['estado_nuevo']}" for a in decisiones["actualizadas"])
        )
    if tareas["creadas"]:
        lineas.append(f"- {len(tareas['creadas'])} tarea(s) nueva(s): " + "; ".join(c["titulo"] for c in tareas["creadas"]))
    if tareas.get("duplicadas"):
        lineas.append(
            f"- {len(tareas['duplicadas'])} tarea(s) NO creada(s) por ser duplicado de algo existente: "
            + "; ".join(d.get("tarea_existente_titulo", "?") for d in tareas["duplicadas"])
        )
    if tareas.get("sin_responsable_resuelto"):
        lineas.append(f"- ⚠️ {len(tareas['sin_responsable_resuelto'])} tarea(s) con responsable sin resolver (ver Notas).")

    errores_todos = ideas["errores"] + decisiones["errores"] + tareas["errores"]
    if errores_todos:
        lineas.append(f"- ⚠️ {len(errores_todos)} error(es), revisa el log: " + "; ".join(errores_todos))

    return "\n".join(lineas)


def guardar_pendientes(bundle_pendiente: dict, staging_dir: Path) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = staging_dir / f"pendientes_reconciliacion_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle_pendiente, f, ensure_ascii=False, indent=2)
    return out_path


def main():
    if len(sys.argv) == 2:
        bundle_path = Path(sys.argv[1]).resolve()
        if not bundle_path.exists():
            print(f"Error: no existe el archivo {bundle_path}")
            sys.exit(1)
    elif len(sys.argv) != 1:
        print("Uso: python scripts/07_aplicar_cambios.py [ruta_a_bundle.json]")
        sys.exit(1)
    else:
        bundle_path = None  # se resuelve adentro, ya con logging activo

    with logged_run("07_aplicar_cambios", ROOT) as log_path:
        print(f"📄 Log de esta corrida: {log_path}")
        inicio = time.monotonic()

        cfg = load_config()
        client = NotionClient()

        if bundle_path is None:
            bundle_path = find_latest_bundle(ROOT / cfg["paths"]["staging_dir"])
            print(f"Usando el bundle más reciente: {bundle_path}")

        with open(bundle_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)

        n_total = sum(len(bundle["propuesta"][t].get("nuevas", [])) for t in ("ideas", "decisiones", "tareas"))
        n_total += sum(len(bundle["propuesta"][t].get("actualizaciones", [])) for t in ("ideas", "decisiones"))
        with Stage(f"Aplicando bundle de reconciliación en Notion ({n_total} ítem(s))"):
            resultado, bundle_pendiente = aplicar_propuesta(bundle, cfg, client)

        resumen = formatear_resumen_aplicacion(resultado)
        print(resumen if resumen else "Nada que aplicar (propuesta vacía).")

        with Stage("Verificando consistencia (tareas/decisiones huérfanas)"):
            chequeo = verificar_consistencia(cfg, client)
        print(formatear_consistencia(chequeo))
        print(f"Tiempo total: {format_duration(time.monotonic() - inicio)}")

        if bundle_pendiente:
            with open(bundle_path, "w", encoding="utf-8") as f:
                json.dump(bundle_pendiente, f, ensure_ascii=False, indent=2)
            print(
                f"\n⚠️  Quedaron pendientes — {bundle_path} quedó reescrito solo con eso. "
                "Resuélvelo (ej. agrega la épica que falta a config.yaml) y vuelve a correr "
                "este script para reintentar."
            )
            sys.exit(1)

        processed_dir = ROOT / cfg["paths"]["processed_dir"]
        processed_dir.mkdir(parents=True, exist_ok=True)
        dest = processed_dir / bundle_path.name
        shutil.move(str(bundle_path), str(dest))
        print(f"\n✅ Bundle aplicado por completo y movido a: {dest}")


if __name__ == "__main__":
    main()
