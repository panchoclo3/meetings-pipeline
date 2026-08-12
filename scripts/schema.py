"""
Esquema JSON para validar la salida de extracción de Claude (paso 2 del pipeline).

Por qué validamos con jsonschema en vez de confiar ciegamente en la respuesta
del modelo: si Claude devuelve algo con un campo faltante o mal tipado, mejor
que el script falle de inmediato con un error claro que descubrir el problema
tres pasos después, ya con la información parcialmente escrita en Notion.
"""

EXTRACTION_SCHEMA = {
    "type": "object",
    "required": [
        "metadata",
        "resumen_ejecutivo",
        "resumen_detallado",
        "decisiones",
        "tareas",
        "ideas",
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
        "decisiones": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["decision", "razon", "estado"],
                "properties": {
                    "decision": {"type": "string"},
                    "razon": {"type": "string"},
                    "estado": {"type": "string", "enum": ["confirmada", "tentativa"]},
                    "cita_transcripcion": {"type": ["string", "null"]},
                },
            },
        },
        "tareas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["titulo", "responsable", "prioridad", "confianza"],
                "properties": {
                    "titulo": {"type": "string"},
                    "responsable": {"type": "array", "items": {"type": "string"}},
                    "prioridad": {
                        "type": ["string", "null"],
                        "enum": ["alta", "media", "baja", None],
                    },
                    "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
                },
            },
        },
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["idea", "contexto", "estado"],
                "properties": {
                    "idea": {"type": "string"},
                    "contexto": {"type": "string"},
                    "estado": {
                        "type": "string",
                        "enum": ["propuesta", "descartada", "en_evaluacion"],
                    },
                    "razon_descarte": {"type": ["string", "null"]},
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
