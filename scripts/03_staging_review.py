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

    lines.append(f"# {meta['titulo_sugerido']}")
    lines.append("")

    if warnings or meta["confianza_metadata"] != "alta" or low_confidence_tasks:
        lines.append("## ⚠️ REVISAR ANTES DE APROBAR")
        if meta["confianza_metadata"] != "alta":
            lines.append(f"- Confianza de metadata (proyecto/tags): **{meta['confianza_metadata']}**")
        for w in warnings:
            lines.append(f"- {w}")
        for t in low_confidence_tasks:
            lines.append(f"- Tarea de baja confianza: \"{t['titulo']}\" (responsable: {', '.join(t['responsable']) or 'sin asignar'})")
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
        lines.append("## Decisiones")
        for d in data["decisiones"]:
            estado_tag = "✅" if d["estado"] == "confirmada" else "🔸 tentativa"
            lines.append(f"- {estado_tag} **{d['decision']}** — {d['razon']}")
        lines.append("")

    if data["tareas"]:
        lines.append("## Tareas")
        for t in data["tareas"]:
            conf_tag = {"alta": "", "media": " (confianza media)", "baja": " ⚠️ (confianza baja)"}[t["confianza"]]
            lines.append(f"- [ ] {t['titulo']} — *{', '.join(t['responsable']) or 'sin asignar'}*{conf_tag}")
        lines.append("")

    if data["ideas"]:
        lines.append("## Ideas")
        for i in data["ideas"]:
            estado = {"propuesta": "💡", "descartada": "❌", "en_evaluacion": "🔍"}[i["estado"]]
            lines.append(f"- {estado} **{i['idea']}** — {i['contexto']}")
            if i.get("razon_descarte"):
                lines.append(f"  - Razón de descarte: {i['razon_descarte']}")
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

    print(f"\n📄 {meta['titulo_sugerido']}")
    print(f"   Proyecto: {meta['proyecto_sugerido']} | Tipo: {meta['tipo_reunion']}")
    print(f"   Confianza metadata: {meta['confianza_metadata']}")
    print(f"   Decisiones: {len(data['decisiones'])} | Tareas: {len(data['tareas'])} | Ideas: {len(data['ideas'])}")

    if warnings or low_conf_tasks:
        print(f"\n   ⚠️  {len(warnings)} advertencia(s) del modelo, {len(low_conf_tasks)} tarea(s) de baja confianza")
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
