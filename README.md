# Segundo Cerebro — Pipeline de Reuniones

Pipeline personal para convertir grabaciones de audio en conocimiento estructurado
en Notion: transcripción + diarización → extracción con Claude → revisión humana
breve → escritura en Notion.

## Estado actual

Instalado y probado de punta a punta (pasos 1→2→3) en Windows con Python 3.12,
`whisperx` 3.8.6 y `ffmpeg` 9.0. Las bases de Notion (Reuniones, Tareas,
Decisiones) ya existen con sus IDs cargados en `config/config.yaml` y sus
propiedades mapeadas correctamente — **pero ninguna está compartida todavía
con la integración de Notion**, así que los pasos 4, 5 y 6 fallan con un
`404 object_not_found` hasta que se comparta cada base (ver
[Setup de Notion](#3-setup-de-notion)). El resto de la lógica de esos tres
pasos está escrita y probada a nivel de funciones puras (ver
[Qué se probó y qué no](#qué-se-probó-y-qué-no)).

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
├── config/config.yaml                    ← vocabulario controlado, parámetros y mapeo de propiedades de Notion
├── prompts/
│   ├── extraction_prompt.txt             ← prompt del paso 2 (editable sin tocar código)
│   ├── weekly_digest_prompt.txt          ← prompt del paso 5 (resumen semanal)
│   └── decisiones_reconciliacion_prompt.txt ← prompt del paso 6 (propuesta de decisiones)
├── scripts/
│   ├── 01_transcribe.py                  ← Paso 1: ASR + diarización (WhisperX)
│   ├── 02_extract.py                     ← Paso 2: extracción estructurada (Claude API)
│   ├── 03_staging_review.py              ← Paso 3: genera resumen .md para revisión
│   ├── 04_push_notion.py                 ← Paso 4: escribe en Notion (API directa)
│   ├── 05_weekly_digest.py               ← Paso 5 (independiente): resumen semanal por Telegram
│   ├── 06_decisiones_reconciliacion.py   ← Paso 6 (independiente): propuesta de decisiones, solo lectura de Notion
│   ├── pipeline.py                       ← orquestador: corre pasos 1→2→3 y se detiene
│   ├── notion_client.py                  ← wrapper de la API REST de Notion
│   └── schema.py                          ← validación JSON Schema de la extracción
├── data/
│   ├── audio/                            ← coloca aquí tus grabaciones
│   ├── transcripts/                      ← salida del paso 1
│   ├── staging/                          ← salida del paso 2 (pendiente de revisión) y propuestas de decisiones del paso 6
│   └── processed/                        ← archivo histórico tras el paso 4
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

Necesitas tres bases de datos en Notion (pueden estar en distintas páginas):
**Reuniones**, **Tareas** y **Decisiones**. Los nombres de propiedad no tienen
por qué ser exactamente los de abajo — lo único que importa es que coincidan
con lo que está en `config/config.yaml` → `notion.propiedades_*`. Esta es la
configuración real ya cargada en este repo, a modo de referencia:

**Base "Reuniones"**
| Propiedad | Tipo | Nombre real en `config.yaml` |
|---|---|---|
| Título | Title | `Agenda` |
| Fecha | Date | `Fecha` |
| Proyecto | Select | `Proyecto` |
| Personas | Multi-select | `Personas` |
| Tags | Multi-select | `Tags` |
| Tipo | Select (reunion_proyecto, conversacion_profesor, brainstorming, espontanea) | `Tipo` |
| Estado | **Select** (Borrador IA, Revisado) | `Estado` |

**Base "Tareas" (Kanban)**
| Propiedad | Tipo | Nombre real en `config.yaml` |
|---|---|---|
| Título | Title | `Nombre` |
| Proyecto | Select | `Proyecto` |
| Responsable (IA) | Select — **distinto** del campo `Responsable` tipo *person* que ya usa el equipo; no lo pisamos | `Responsable (IA)` |
| Prioridad | Select (Alta, Media, Baja — con mayúscula inicial) | `Prioridad` |
| Estado | **Status** (Not started, In progress, Done — en inglés) | `Estado` |
| Reunión origen | Relation → apunta a "Reuniones" | `Reunion origen` (sin tilde) |

> ⚠️ **`Estado` en Tareas es tipo `status`, no `select`** — Notion no deja
> crear opciones de `status` nuevas vía API, así que el pipeline mapea
> `"Pendiente"` → `"Not started"` en código
> (`scripts/04_push_notion.py::ESTADO_TAREA_A_STATUS`). Si cambias las
> opciones de esa base, actualiza también ese diccionario.
> `Estado` en Reuniones, en cambio, sí es `select` — no necesita mapeo.

**Base "Decisiones"** (el pipeline solo la **lee**, nunca escribe en ella —
ver [Paso 6](#paso-6-independiente-reconciliación-de-decisiones-solo-propuesta)):
| Propiedad | Tipo | Nombre real en `config.yaml` |
|---|---|---|
| Decision | Title | `Decision` |
| Tema | Text | `Tema` |
| Razon | Text | `Razon` |
| Estado | Status (Not started, In progress, Done) | `Estado` |
| Prototipo | Select (Autonomo, Mediado) | `Prototipo` |
| Fecha | Date | `Fecha` |

**Comparte las tres bases con tu integración** — este paso es fácil de
olvidar y la falla resultante no es obvia: Notion devuelve un
`404 object_not_found` (no un error de permisos) si la base existe pero no
está compartida. En cada base: `···` (esquina superior derecha) → "Conexiones"
→ selecciona tu integración.

Copia el ID de cada base (está en la URL: `notion.so/xxxxx?v=...` — el `xxxxx`
de 32 caracteres es el ID) y pégalo en `config/config.yaml`:

```yaml
notion:
  reuniones_database_id: "aquí el ID"
  tareas_database_id: "aquí el ID"
  decisiones_database_id: "aquí el ID"
```

Para el paso 5 (resumen semanal) también necesitas compartir con la
integración la página "Ventana Celeste" (o la que configures como
`telegram.pagina_padre`) — el pipeline busca esa página por nombre para
crear debajo la subpágina "Resúmenes semanales" la primera vez que corre.

### 4. Setup de Telegram (opcional — solo para el paso 5)

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram → `/newbot` →
   sigue las instrucciones → copia el token que te da (formato
   `123456:ABC-...`) en `TELEGRAM_BOT_TOKEN`.
2. Mándale cualquier mensaje a tu bot recién creado (si no le escribís primero,
   Telegram no le deja enviarte mensajes a vos).
3. Visita `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` en el navegador
   y busca `"chat":{"id": ...}` en la respuesta — ese número es tu
   `TELEGRAM_CHAT_ID`.
4. Si quieres otros nombres de página para el resumen semanal, ajusta
   `telegram.pagina_padre` y `telegram.pagina_resumenes` en `config.yaml`
   (por defecto: "Ventana Celeste" → "Resúmenes semanales").

### 5. Ajustar vocabulario controlado

Edita `config/config.yaml` → `proyectos` y `tags_permitidos` con tus valores
reales (ya viene pre-cargado con los del proyecto Ventana Celeste como ejemplo).
Estas listas se inyectan automáticamente en el prompt de extracción.

## Uso

> Los pasos 1→2→3 fueron probados de punta a punta con un audio de prueba y
> funcionan tal como se describe abajo. Los pasos 4, 5 y 6 están escritos y
> probados a nivel de lógica, pero **la escritura/lectura real contra Notion
> está bloqueada** hasta compartir las bases con la integración — ver
> [Qué se probó y qué no](#qué-se-probó-y-qué-no).

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

### Paso 5 (independiente) — Resumen semanal por Telegram

No depende de audio ni de WhisperX — solo hace llamadas HTTP a Notion, Claude
y Telegram, así que se puede correr desde un cron o tarea programada sin el
resto del entorno de transcripción instalado:

```bash
python scripts/05_weekly_digest.py
```

Qué hace: junta las reuniones de los últimos 7 días desde la base
"Reuniones", les pide a Claude un resumen en texto plano apto para Telegram
(sin tablas ni encabezados grandes — solo `*negrita*`/`_cursiva_`), lo manda
al chat configurado, y lo agrega también como bloque nuevo al final de la
página "Resúmenes semanales" en Notion (la crea si no existe todavía).

Si `scripts/06_decisiones_reconciliacion.py` está disponible, este paso
importa su función `ejecutar_reconciliacion()` y agrega la propuesta de
decisiones al **mismo** mensaje de Telegram — no son dos mensajes separados.

### Paso 6 (independiente) — Reconciliación de decisiones (solo propuesta)

```bash
python scripts/06_decisiones_reconciliacion.py
```

Compara las decisiones extraídas esta semana (leyendo `data/processed/*.json`)
contra la base real "Decisiones" en Notion, y le pide a Claude que proponga:
qué decisiones son genuinamente nuevas, y qué decisiones existentes podrían
necesitar un cambio de estado según lo discutido esta semana.

**Este script nunca crea ni modifica páginas en la base "Decisiones"** —
las decisiones de proyecto son datos sensibles y este pipeline no escribe
sobre ellos sin confirmación humana explícita (ver
[Filosofía del pipeline](#filosofía-del-pipeline-por-qué-está-diseñado-así)).
En su lugar:
- Guarda la propuesta completa en `data/staging/decisiones_propuesta_<fecha>.json`.
- Cuando se corre como parte del paso 5, agrega la propuesta al mensaje de
  Telegram como texto plano con formato de lista.

La aplicación real de los cambios queda para que la revises a mano en Notion,
o para un futuro script de aplicación separado que lea ese JSON (no incluido
todavía a propósito).

## Qué revisar en el paso de staging

El archivo `.md` generado pone **arriba** cualquier señal de incertidumbre:

- `confianza_metadata` distinta de "alta" → revisa proyecto/tags sugeridos.
- Tareas con `confianza: "baja"` → verifica el responsable asignado.
- Cualquier entrada en `advertencias_extraccion` → el modelo te dice explícitamente
  qué no le quedó claro (hablante ambiguo, tarea sin dueño evidente, etc.).

Si todo se ve bien, aprueba y avanza al paso 4. Si algo está mal, edita el `.json`
correspondiente en `data/staging/` — es la fuente de verdad, no el `.md`.

## Qué se probó y qué no

Estado real al momento de escribir esto, para que sepas exactamente qué
confiar y qué verificar vos mismo:

**Probado con éxito:**
- Pasos 1→2→3 (transcripción, extracción, staging): end-to-end con audio real.
- `config/config.yaml`: nombres de propiedad corregidos contra el esquema real
  provisto (Agenda, Nombre, "Responsable (IA)", "Reunion origen" sin tilde).
- `scripts/notion_client.py`: `prop_status`, `query_database`,
  `get_block_children`, `append_block_children`, `search`, `create_subpage`,
  `block_plain_text`, `page_title_plain_text` — compilan y sus casos puros
  (sin red) se probaron con datos de Notion simulados.
- `scripts/04_push_notion.py`: mapeo `"Pendiente"` → `"Not started"` y
  capitalización de prioridad (`alta` → `Alta`) verificados por inspección
  y con un JSON de staging sintético.
- `scripts/05_weekly_digest.py`: `build_prompt()` y `build_meeting_content()`
  probados con un cliente de Notion simulado (sin red real).
- `scripts/06_decisiones_reconciliacion.py`: `gather_decisiones_recientes()`
  (filtra correctamente por fecha), `formatear_para_telegram()` (vacío y con
  datos) y `parse_json_response()` probados con datos sintéticos.
- El import dinámico de `06_decisiones_reconciliacion` desde `05_weekly_digest.py`
  (el nombre empieza con un dígito, no es un identificador válido para
  `import` normal — se usa `importlib.import_module` a propósito).

**Bloqueado / sin probar contra las APIs reales:**
- **Push real a Notion (paso 4)**: intenté correr `04_push_notion.py` contra
  un JSON sintético y las bases reales — falló con
  `404 object_not_found: Could not find database... Make sure the relevant
  pages and databases are shared with your integration`. Las bases existen
  y los IDs son correctos; lo que falta es **compartir las tres bases
  (Reuniones, Tareas, Decisiones) y la página "Ventana Celeste" con la
  integración** (ver [Setup de Notion](#3-setup-de-notion)). Una vez
  compartidas, hay que volver a correr el paso 4 para confirmar que no hay
  más errores 400 río abajo (por ejemplo, si alguna opción de `select`/`status`
  no existe todavía en la base real).
- **Consulta a Notion y envío a Telegram (pasos 5 y 6)**: no se probaron
  contra las APIs reales por el mismo bloqueo de permisos, y además
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` todavía no están configurados en
  `.env` (ver [Setup de Telegram](#4-setup-de-telegram-opcional-solo-para-el-paso-5)).
- **Reconciliación de decisiones con datos reales**: `data/processed/` está
  vacío en este momento, así que `ejecutar_reconciliacion()` nunca llegó a
  consultar la base "Decisiones" ni a llamar a Claude en las pruebas — la
  lógica de filtrado por fecha se probó con datos sintéticos, pero el flujo
  completo (comparación real vía Claude) queda sin probar hasta que haya al
  menos una reunión real procesada esta semana.

**Siguiente vez que se retome este trabajo**: compartir las bases/página con
la integración, completar las credenciales de Telegram, y volver a correr
los pasos 4, 5 y 6 contra datos reales.

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
