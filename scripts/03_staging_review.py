#!/usr/bin/env python3
"""
Paso 3 — Staging / Revisión humana

Uso:
    python scripts/03_staging_review.py data/staging/20260807_120000_reunion-mim.json

Qué hace:
1. Lee el JSON de extracción (paso 2).
2. Genera un archivo .md legible al lado del .json, con las advertencias del
   modelo destacadas ARRIBA (lo primero que debes leer).
3. Te muestra en terminal un resumen corto con las señales de baja confianza,
   para que sepas de un vistazo si conviene revisar con calma o aprobar rápido.
4. Si TODO está en orden, tú simplemente confirmas y el paso 4 usa el mismo
   JSON (edítalo directamente si necesitas corregir algo — es la fuente de
   verdad, el .md es solo para lectura).

Por qué no hay una "interfaz" más elaborada: para un flujo personal, abrir
un .md en tu editor y tocar el .json si algo está mal es más rápido y más
confiable que mantener una UI de revisión separada.

Nota: "ideas"/"decisiones"/"tareas" acá son CANDIDATOS todavía sin resolver
contra Notion (sin épica ni decisión madre reales, sin chequeo de duplicado)
— eso pasa recién en el paso 4. Revisa el contenido y el nivel (¿de verdad es
una Tarea y no una Idea?), no la ubicación final en Notion.
"""

import sys

# Ver nota equivalente en 01_transcribe.py: fuerza UTF-8 en stdout/stderr para
# que los print() con emojis no revienten en una consola Windows con cp1252.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from progress import logged_run  # noqa: E402


def render_markdown(data: dict) -> str:
    meta = data["metadata"]
    lines = []

    warnings = data.get("advertencias_extraccion", [])
    low_confidence_tasks = [t for t in data["tareas"] if t.get("confianza") == "baja"]
    tareas_sin_epica = [t for t in data["tareas"] if t.get("epica_sugerida") == "NINGUNA_ENCAJA"]

    lines.append(f"# {meta['titulo_sugerido']}")
    lines.append("")

    if warnings or meta["confianza_metadata"] != "alta" or low_confidence_tasks or tareas_sin_epica:
        lines.append("## ⚠️ REVISAR ANTES DE APROBAR")
        if meta["confianza_metadata"] != "alta":
            lines.append(f"- Confianza de metadata (proyecto/tags): **{meta['confianza_metadata']}**")
        for w in warnings:
            lines.append(f"- {w}")
        for t in low_confidence_tasks:
            lines.append(f"- Tarea de baja confianza: \"{t['titulo']}\" (responsable: {', '.join(t['responsable']) or 'sin asignar'})")
        for t in tareas_sin_epica:
            lines.append(
                f"- Tarea sin épica que encaje: \"{t['titulo']}\" — propuesta: "
                f"{t.get('justificacion_epica') or '(sin justificación)'}"
            )
        lines.append("")

    lines.append("## Metadata")
    lines.append(f"- **Proyecto sugerido:** {meta['proyecto_sugerido']}")
    lines.append(f"- **Tipo:** {meta['tipo_reunion']}")
    lines.append(f"- **Tags sugeridos:** {', '.join(meta['tags_sugeridos'])}")
    lines.append(f"- **Personas detectadas:** {', '.join(meta['personas_detectadas'])}")
    lines.append("")

    lines.append("## Resumen ejecutivo")
    lines.append(data["resumen_ejecutivo"])
    lines.append("")

    lines.append("## Resumen detallado")
    lines.append(data["resumen_detallado"])
    lines.append("")

    if data["decisiones"]:
        lines.append("## Decisiones (candidatas)")
        for d in data["decisiones"]:
            tag = "✅ Vigente" if d["estado_vigencia"] == "Vigente" else "🔸 Tentativa"
            reemplazo = f" (podría reemplazar: {d['posible_reemplazo_de']})" if d.get("posible_reemplazo_de") else ""
            lines.append(
                f"- {tag} — **{d['decision']}** [{d['decision_madre_sugerida']}] — {d['razon']}{reemplazo}"
            )
        lines.append("")

    if data["tareas"]:
        lines.append("## Tareas (candidatas)")
        for t in data["tareas"]:
            conf_tag = {"alta": "", "media": " (confianza media)", "baja": " ⚠️ (confianza baja)"}[t["confianza"]]
            epica = t["epica_sugerida"] if t["epica_sugerida"] != "NINGUNA_ENCAJA" else "⚠️ sin épica"
            lines.append(
                f"- [ ] {t['titulo']} — *{', '.join(t['responsable']) or 'sin asignar'}* "
                f"[{epica} / {t['area']}]{conf_tag}"
            )
        lines.append("")

    if data["ideas"]:
        lines.append("## Ideas")
        for i in data["ideas"]:
            estado_icono = "🔍" if i["estado"] == "En discusion" else "💡"
            horizonte = f" [{i['horizonte']}]" if i.get("horizonte") else ""
            lines.append(f"- {estado_icono} **{i['idea']}**{horizonte} — {i.get('problema_que_resuelve') or 'sin problema identificado'}")
        lines.append("")

    if data["preguntas_abiertas"]:
        lines.append("## Preguntas abiertas")
        for q in data["preguntas_abiertas"]:
            lines.append(f"- {q}")
        lines.append("")

    if data["proximos_pasos"]:
        lines.append("## Próximos pasos")
        for p in data["proximos_pasos"]:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


def print_terminal_summary(data: dict):
    meta = data["metadata"]
    warnings = data.get("advertencias_extraccion", [])
    low_conf_tasks = [t for t in data["tareas"] if t.get("confianza") == "baja"]
    sin_epica = [t for t in data["tareas"] if t.get("epica_sugerida") == "NINGUNA_ENCAJA"]

    print(f"\n📄 {meta['titulo_sugerido']}")
    print(f"   Proyecto: {meta['proyecto_sugerido']} | Tipo: {meta['tipo_reunion']}")
    print(f"   Confianza metadata: {meta['confianza_metadata']}")
    print(f"   Ideas: {len(data['ideas'])} | Decisiones: {len(data['decisiones'])} | Tareas: {len(data['tareas'])}")

    avisos = list(warnings)
    if low_conf_tasks:
        avisos.append(f"{len(low_conf_tasks)} tarea(s) de baja confianza")
    if sin_epica:
        avisos.append(f"{len(sin_epica)} tarea(s) sin épica que encaje")

    if avisos:
        print(f"\n   ⚠️  {len(avisos)} señal(es) a revisar")
        print("   → Revisa el archivo .md antes de aprobar.")
    else:
        print("\n   ✅ Sin advertencias — revisión rápida recomendada, no exhaustiva.")


def main():
    if len(sys.argv) != 2:
        print("Uso: python scripts/03_staging_review.py <ruta_al_staging.json>")
        sys.exit(1)

    staging_path = Path(sys.argv[1]).resolve()
    if not staging_path.exists():
        print(f"Error: no existe el archivo {staging_path}")
        sys.exit(1)

    with logged_run("03_staging_review", ROOT):
        with open(staging_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        md = render_markdown(data)
        md_path = staging_path.with_suffix(".md")
        md_path.write_text(md, encoding="utf-8")

        print_terminal_summary(data)
        print(f"\n📝 Resumen legible generado en: {md_path}")
        print("   Ábrelo, revisa. Si necesitas corregir algo, edita directamente:")
        print(f"   {staging_path}")
        print(f"\n   Cuando esté listo: python scripts/04_push_notion.py {staging_path}")


if __name__ == "__main__":
    main()
