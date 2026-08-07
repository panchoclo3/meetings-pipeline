# Segundo Cerebro — Pipeline de Reuniones

Pipeline personal para convertir grabaciones de audio en conocimiento estructurado
en Notion: transcripción + diarización → extracción con Claude → revisión humana
breve → escritura en Notion.

## Estado actual

Instalado y probado de punta a punta (pasos 1→2→3) en Windows con Python 3.12,
`whisperx` 3.8.6 y `ffmpeg` 9.0. Pendiente antes de poder usar el paso 4:
crear las bases de datos en Notion y pegar sus IDs en `config/config.yaml`
(ver [Setup de Notion](#3-setup-de-notion)).

## Filosofía del pipeline (por qué está diseñado así)

- **Disparo manual**: tú decides cuándo procesar un audio. No hay carpeta vigilada
  ni automatización silenciosa.
- **Un solo punto de revisión humana**: después de la extracción (paso 2), antes
  de escribir en Notion (paso 4). Todo lo demás corre sin intervención.
- **El JSON de staging es la fuente de verdad**: el `.md` que se genera es solo
  para lectura rápida. Si algo está mal, edita el `.json` directamente.
- **Notion es el sistema de registro, no el motor de búsqueda**: la búsqueda
  semántica ("¿qué hablamos de X hace 6 meses?") se resuelve en el paso de
  consulta (fuera de este pipeline), combinando filtros de Notion con el
  contexto largo de Claude. Este pipeline solo se encarga de capturar y
  estructurar.

## Estructura del proyecto

```
ventana-celeste-pipeline/
├── config/config.yaml          ← vocabulario controlado (proyectos, tags) y parámetros
├── prompts/extraction_prompt.txt ← prompt de extracción (editable sin tocar código)
├── scripts/
│   ├── 01_transcribe.py        ← Paso 1: ASR + diarización (WhisperX)
│   ├── 02_extract.py           ← Paso 2: extracción estructurada (Claude API)
│   ├── 03_staging_review.py    ← Paso 3: genera resumen .md para revisión
│   ├── 04_push_notion.py       ← Paso 4: escribe en Notion (API directa)
│   ├── pipeline.py             ← orquestador: corre pasos 1→2→3 y se detiene
│   ├── notion_client.py        ← wrapper de la API REST de Notion
│   └── schema.py                ← validación JSON Schema de la extracción
├── data/
│   ├── audio/                  ← coloca aquí tus grabaciones
│   ├── transcripts/            ← salida del paso 1
│   ├── staging/                ← salida del paso 2, pendiente de revisión
│   └── processed/              ← archivo histórico tras el paso 4
├── requirements.txt
└── .env.example
```

## Instalación

### 1. Entorno Python

```bash
cd ventana-celeste-pipeline
python3 -m venv venv
source venv/bin/activate        # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Necesitas `ffmpeg` instalado en el sistema:
- Mac: `brew install ffmpeg`
- Linux: `apt install ffmpeg`
- Windows: `winget install ffmpeg`

> **Nota sobre WhisperX**: sus dependencias (torch, ffmpeg) son sensibles a la
> versión de tu sistema. Si `pip install -r requirements.txt` falla en esa línea,
> instala WhisperX siguiendo la guía oficial: https://github.com/m-bain/whisperX
>
> **Compatibilidad verificada**: con `whisperx` 3.8.6, la API de diarización
> cambió respecto a versiones anteriores — `scripts/01_transcribe.py` ya está
> adaptado a estos cambios:
> - `whisperx.DiarizationPipeline` → ahora vive en `whisperx.diarize.DiarizationPipeline`.
> - El parámetro `use_auth_token` se renombró a `token`.
> - El modelo de diarización por defecto cambió a `pyannote/speaker-diarization-community-1`
>   (acceso restringido); el script fija explícitamente `pyannote/speaker-diarization-3.1`,
>   el modelo para el que aceptas condiciones de uso más abajo.
>
> Si actualizas `whisperx` en el futuro y la diarización vuelve a fallar con un
> `AttributeError` o `TypeError`, probablemente cambió la API de nuevo — revisa
> `whisperx.diarize.DiarizationPipeline.__init__` en la versión instalada.

### 2. Variables de entorno

```bash
cp .env.example .env
```

Completa `.env` con:

- **`ANTHROPIC_API_KEY`**: desde [console.anthropic.com](https://console.anthropic.com)
- **`HF_TOKEN`**: desde [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
  Además, debes **aceptar las condiciones de uso** (un clic, gratis) de estos dos
  modelos, o la diarización fallará con error de permisos:
  - https://huggingface.co/pyannote/speaker-diarization-3.1
  - https://huggingface.co/pyannote/segmentation-3.0
- **`NOTION_API_KEY`**: crea una integración en
  [www.notion.so/my-integrations](https://www.notion.so/my-integrations),
  copia el "Internal Integration Token".

Los scripts (`01_transcribe.py`, `02_extract.py`, `04_push_notion.py`) cargan
`.env` automáticamente vía `python-dotenv` — no necesitas exportar nada a mano.

### 3. Setup de Notion

Crea dos bases de datos en Notion (pueden estar en la misma página) con
**exactamente estos nombres de propiedad** (o ajusta `config.yaml` si prefieres
otros nombres):

**Base "Reuniones"**
| Propiedad | Tipo |
|---|---|
| Título | Title |
| Fecha | Date |
| Proyecto | Select (opciones: Ventana Celeste, Tesis, Personal, Otro) |
| Personas | Multi-select |
| Tags | Multi-select |
| Tipo | Select (reunion_proyecto, conversacion_profesor, brainstorming, espontanea) |
| Estado | Select (Borrador IA, Revisado) |

**Base "Tareas"**
| Propiedad | Tipo |
|---|---|
| Título | Title |
| Proyecto | Select (mismas opciones que en Reuniones) |
| Responsable | Select |
| Prioridad | Select (alta, media, baja) |
| Estado | Select (Pendiente, En progreso, Hecho) |
| Reunión origen | Relation → apunta a la base "Reuniones" |

Luego comparte ambas bases con tu integración (`···` en la esquina superior
derecha de cada base → "Conexiones" → selecciona tu integración).

Copia el ID de cada base (está en la URL: `notion.so/xxxxx?v=...` — el `xxxxx`
de 32 caracteres es el ID) y pégalo en `config/config.yaml`:

```yaml
notion:
  reuniones_database_id: "aquí el ID"
  tareas_database_id: "aquí el ID"
```

### 4. Ajustar vocabulario controlado

Edita `config/config.yaml` → `proyectos` y `tags_permitidos` con tus valores
reales (ya viene pre-cargado con los del proyecto Ventana Celeste como ejemplo).
Estas listas se inyectan automáticamente en el prompt de extracción.

## Uso

> Los pasos 1→2→3 fueron probados de punta a punta con un audio de prueba y
> funcionan tal como se describe abajo. El paso 4 (push a Notion) no se ha
> probado todavía porque falta completar el [Setup de Notion](#3-setup-de-notion).

**Flujo recomendado (orquestado, se detiene antes de escribir en Notion):**

```bash
python scripts/pipeline.py data/audio/2026-08-07_reunion-mim.mp3
```

Esto corre transcripción → extracción → staging, y te deja el comando exacto
para el paso final una vez que hayas revisado.

**Paso a paso manual (si quieres correr cada etapa por separado):**

```bash
python scripts/01_transcribe.py data/audio/mi_reunion.mp3
python scripts/02_extract.py data/transcripts/<archivo_generado>.json
python scripts/03_staging_review.py data/staging/<archivo_generado>.json

# Revisa data/staging/<archivo>.md — corrige el .json si algo está mal —

python scripts/04_push_notion.py data/staging/<archivo_generado>.json
```

## Qué revisar en el paso de staging

El archivo `.md` generado pone **arriba** cualquier señal de incertidumbre:

- `confianza_metadata` distinta de "alta" → revisa proyecto/tags sugeridos.
- Tareas con `confianza: "baja"` → verifica el responsable asignado.
- Cualquier entrada en `advertencias_extraccion` → el modelo te dice explícitamente
  qué no le quedó claro (hablante ambiguo, tarea sin dueño evidente, etc.).

Si todo se ve bien, aprueba y avanza al paso 4. Si algo está mal, edita el `.json`
correspondiente en `data/staging/` — es la fuente de verdad, no el `.md`.

## Próximos pasos fuera de este pipeline

- **Consulta en lenguaje natural**: usa el conector MCP de Notion en Claude.ai
  directamente desde el chat — filtra por proyecto/tag/fecha en Notion y pide
  a Claude que sintetice sobre el contenido recuperado.
- **Migración a AssemblyAI/Deepgram**: si el volumen de reuniones crece o
  quieres identificación de hablante por perfil de voz, reemplaza la lógica
  de `01_transcribe.py` sin tocar el resto del pipeline (el contrato de salida
  — `readable_transcript` — se mantiene igual).
- **Índice semántico**: si con el tiempo la búsqueda por filtros de Notion +
  contexto de Claude deja de ser suficiente, se puede añadir una etapa de
  embeddings sin modificar los pasos 1-4 existentes.
