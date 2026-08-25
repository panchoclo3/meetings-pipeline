"""
Esquema JSON para validar la salida de extracción de Claude (paso 2 del pipeline).

Por qué validamos con jsonschema en vez de confiar ciegamente en la respuesta
del modelo: si Claude devuelve algo con un campo faltante o mal tipado, mejor
que el script falle de inmediato con un error claro que descubrir el problema
tres pasos después, ya con la información parcialmente escrita en Notion.

Nota de diseño: "ideas", "decisiones" y "tareas" son CANDIDATOS, no registros
listos para escribir. El paso 2 (este esquema) decide el NIVEL de cada cosa
(Idea vs Decisión vs Tarea) según el lenguaje del audio — eso no se vuelve a
tocar después. Lo que SÍ falta resolver en pasos posteriores (scripts/06 y
scripts/07) es: si el candidato es duplicado de algo que ya existe en Notion,
y a qué épica / decisión madre cuelga — eso requiere consultar Notion en vivo,
no se puede decidir solo mirando la transcripción.
"""

EXTRACTION_SCHEMA = {
    "type": "object",
    "required": [
        "metadata",
        "resumen_ejecutivo",
        "resumen_detallado",
        "ideas",
        "decisiones",
        "tareas",
        "preguntas_abiertas",
        "proximos_pasos",
        "advertencias_extraccion",
    ],
    "properties": {
        "metadata": {
            "type": "object",
            "required": [
                "titulo_sugerido",
                "proyecto_sugerido",
                "tipo_reunion",
                "tags_sugeridos",
                "personas_detectadas",
                "confianza_metadata",
            ],
            "properties": {
                "titulo_sugerido": {"type": "string"},
                "proyecto_sugerido": {"type": "string"},
                "tipo_reunion": {
                    "type": "string",
                    "enum": [
                        "reunion_proyecto",
                        "conversacion_profesor",
                        "brainstorming",
                        "espontanea",
                    ],
                },
                "tags_sugeridos": {"type": "array", "items": {"type": "string"}},
                "personas_detectadas": {"type": "array", "items": {"type": "string"}},
                "confianza_metadata": {
                    "type": "string",
                    "enum": ["alta", "media", "baja"],
                },
            },
        },
        "resumen_ejecutivo": {"type": "string"},
        "resumen_detallado": {"type": "string"},
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["idea", "problema_que_resuelve", "estado"],
                "properties": {
                    "idea": {"type": "string"},
                    "problema_que_resuelve": {"type": ["string", "null"]},
                    "horizonte": {
                        "type": ["string", "null"],
                        "enum": ["Ahora", "V2 MIM", "Feria", "Futuro", None],
                    },
                    "encaje_filosofico": {
                        "type": ["string", "null"],
                        "enum": ["Refuerza", "Neutro", "Tensiona", None],
                    },
                    "prototipo": {"type": "array", "items": {"type": "string"}},
                    "esfuerzo": {
                        "type": ["string", "null"],
                        "enum": ["Bajo", "Medio", "Alto", "Muy alto", None],
                    },
                    "estado": {"type": "string", "enum": ["Propuesta", "En discusion"]},
                    "cita_transcripcion": {"type": ["string", "null"]},
                },
            },
        },
        "decisiones": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["decision", "tema", "razon", "estado_vigencia", "decision_madre_sugerida"],
                "properties": {
                    # La resolución en sí ("Mantener el eje interno"), nunca
                    # su estado ("Pendiente: interno vs externo").
                    "decision": {"type": "string"},
                    "tema": {"type": "string"},
                    "razon": {"type": "string"},
                    "prototipo": {"type": ["string", "null"]},
                    # Vigencia al momento de la extracción — nunca "Superada"
                    # ni "Revertida" acá: eso solo lo decide la reconciliación
                    # al comparar contra lo que ya existe en Notion.
                    "estado_vigencia": {"type": "string", "enum": ["Vigente", "Tentativa"]},
                    "decision_madre_sugerida": {
                        "type": "string",
                        "enum": ["D1", "D2", "D3", "D4"],
                    },
                    "posible_reemplazo_de": {"type": ["string", "null"]},
                    "cita_transcripcion": {"type": ["string", "null"]},
                },
            },
        },
        "tareas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["titulo", "responsable", "prioridad", "area", "epica_sugerida", "confianza"],
                "properties": {
                    "titulo": {"type": "string"},
                    "responsable": {"type": "array", "items": {"type": "string"}},
                    "prioridad": {
                        "type": ["string", "null"],
                        "enum": ["alta", "media", "baja", None],
                    },
                    "prototipo": {"type": ["string", "null"]},
                    "area": {"type": "string"},
                    # Código de épica (AUT-1, MED-2, ...) o "NINGUNA_ENCAJA" si
                    # de verdad no encaja ninguna — nunca se inventa una.
                    "epica_sugerida": {"type": "string"},
                    "justificacion_epica": {"type": ["string", "null"]},
                    "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
                },
            },
        },
        "preguntas_abiertas": {"type": "array", "items": {"type": "string"}},
        "proximos_pasos": {"type": "array", "items": {"type": "string"}},
        "advertencias_extraccion": {"type": "array", "items": {"type": "string"}},
    },
}


def validate_extraction(data: dict) -> list:
    """
    Valida `data` contra EXTRACTION_SCHEMA.
    Devuelve una lista de errores (vacía si es válido).
    """
    import jsonschema

    validator = jsonschema.Draft7Validator(EXTRACTION_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return [f"{'.'.join(str(p) for p in e.path)}: {e.message}" for e in errors]
