# Segundo Cerebro — Pipeline de Reuniones

Pipeline personal para convertir grabaciones de audio en conocimiento estructurado
en Notion: transcripción + diarización → extracción con Claude → revisión humana
breve → escritura en Notion.

## Estado actual

Pipeline con esquema **Reunión → Idea → Decisión → Tarea**: transcripción
(paso 1) → extracción (paso 2, ya clasifica cada cosa en su nivel correcto)
→ staging (paso 3) → push del acta + reconciliación in-línea contra Notion
en vivo (paso 4 — ver [Filosofía](#filosofía-del-pipeline-por-qué-está-diseñado-así)).
Las cuatro bases de Notion (Reuniones, Tareas, Decisiones, Ideas) están
compartidas con la integración y sus IDs viven en `.env` (ver
[Setup de Notion](#3-setup-de-notion)). Detalle de qué se verificó en cada
corrida: [Qué se probó y qué no](#qué-se-probó-y-qué-no).

> Este pipeline no usa Telegram — el resumen semanal se escribe únicamente
> en una subpágina de Notion. Si ves referencias a Telegram en secciones
> como "Qué se probó y qué no" más abajo, son historial de una versión
> anterior; el código actual ya no lo usa.

## Filosofía del pipeline (por qué está diseñado así)

- **Disparo manual**: tú decides cuándo procesar un audio. No hay carpeta vigilada
  ni automatización silenciosa.
- **Un solo punto de revisión humana**: después de la extracción (paso 2), antes
  de escribir en Notion (paso 4). Todo lo demás corre sin intervención.
- **No duplicar es la regla central de este Notion**: el error más caro de
  este proyecto ha sido crear duplicados de trabajo ya registrado con otras
  palabras. Por eso el paso 4 nunca escribe una Idea/Decisión/Tarea nueva sin
  antes consultar Notion en vivo y comparar semánticamente contra lo que ya
  existe (ver `scripts/06_reconciliacion.py`). Ante la duda, el pipeline
  prefiere menos entradas, mejor ubicadas, con la razón explícita — no
  registrar cada mensaje puntual.
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
│   └── reconciliacion_prompt.txt         ← prompt de scripts/06_reconciliacion.py
├── scripts/
│   ├── 01_transcribe.py                  ← Paso 1: ASR + diarización (WhisperX)
│   ├── 02_extract.py                     ← Paso 2: extracción estructurada (Claude API) — clasifica Idea/Decisión/Tarea
│   ├── 03_staging_review.py              ← Paso 3: genera resumen .md para revisión
│   ├── 04_push_notion.py                 ← Paso 4: crea el acta y reconcilia+aplica in-línea (llama a 06 y 07)
│   ├── 05_weekly_digest.py               ← Paso 5 (independiente): resumen semanal + reconciliación de respaldo
│   ├── 06_reconciliacion.py              ← compara Ideas/Decisiones/Tareas candidatas contra Notion en vivo, solo lectura
│   ├── 07_aplicar_cambios.py             ← aplica la propuesta del paso 6 en Notion (crea/actualiza)
│   ├── pipeline.py                       ← orquestador: corre pasos 1→2→3 y se detiene
│   ├── notion_client.py                  ← wrapper de la API REST de Notion
│   ├── progress.py                       ← visibilidad de progreso/errores, usado por todos los pasos (ver abajo)
│   └── schema.py                          ← validación JSON Schema de la extracción
├── data/
│   ├── audio/                            ← coloca aquí tus grabaciones
│   ├── transcripts/                      ← salida del paso 1
│   ├── staging/                          ← salida del paso 2 (pendiente de revisión) y bundles de reconciliación/pendientes
│   ├── processed/                        ← archivo histórico tras el paso 4
│   └── logs/                             ← log completo de cada corrida (ver "Visibilidad de progreso y errores")
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

Necesitas cuatro bases de datos en Notion (pueden estar en distintas páginas):
**Reuniones**, **Tareas del equipo**, **Decisiones de diseño** e **Ideas**.
Los nombres de propiedad no tienen por qué ser exactamente los de abajo — lo
único que importa es que coincidan con lo que está en `config/config.yaml` →
`notion.propiedades_*`. Esta es la configuración real ya cargada en este
repo, a modo de referencia:

> Existe además una base separada "Tareas Coordinador (Francisco)" para
> admin personal — el pipeline **no la toca**, a propósito: no es trabajo que
> derive de reuniones de equipo.

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

**Base "Tareas del equipo"**
| Propiedad | Tipo | Nombre real en `config.yaml` |
|---|---|---|
| Título | Title | `Nombre` |
| Responsable | **Person** (usuarios reales de Notion, admite varios) | `Responsable` |
| Prototipo | Select (Autonomo, Mediado, Escolar, Hogar, Transversal) | `Prototipo` |
| Área | Select (Diseño, Fabricacion, Electronica, Software, Difusion, Administrativo, Investigacion) | `Área` |
| Prioridad | Select (Alta, Media, Baja) | `Prioridad` |
| Estado | **Status** (Not started, In progress, Done — en inglés) | `Estado` |
| Fecha | Date | `Fecha` |
| Reunión origen | Relation → "Reuniones" | `Reunion origen` (sin tilde) |
| Notas | Text | `Notas` |
| Tarea madre | Self-relation → la épica de la que cuelga | `Tarea madre` |
| Subtareas | Self-relation (lado inverso de Tarea madre) | `Subtareas` |

> **`Prototipo` y `Área` son ortogonales y no se mezclan** — un error
> histórico frecuente en este proyecto fue poner "Autonomo" en Área. Área es
> la disciplina del trabajo (Software, Fabricación...), Prototipo es a cuál
> de los productos pertenece.
>
> **Toda tarea nueva cuelga de una épica** (`Tarea madre` obligatorio, sin
> excepciones) — el catálogo cerrado de épicas vive en
> `config.yaml → notion.epicas` (nombre → page ID real, ver
> [Épicas y decisiones madre](#épicas-y-decisiones-madre)). Si ninguna
> encaja, el pipeline **no crea una épica nueva por su cuenta** — la tarea
> queda pendiente con el nombre que Claude propone, para que la agregues a
> mano.
>
> ⚠️ **`Responsable` es tipo `person`, no `select`** — Notion exige el user
> ID real de cada persona, no un nombre en texto. El pipeline extrae nombres
> en texto libre del audio y los resuelve vía
> `scripts/07_aplicar_cambios.py::resolver_persona()`, que primero consulta
> `config.yaml → notion.resolucion_personas` (mapeo editado a mano) y, si
> falta, intenta resolverlo automáticamente contra `/v1/users`. Los nombres
> que no se logran resolver (típico de invitados sin acceso completo a la
> integración) quedan anotados en el campo `Notas` en vez de perderse — ver
> [Resolución de personas](#resolución-de-personas-responsable).
>
> ⚠️ **`Estado` en Tareas es tipo `status`, no `select`** — Notion no deja
> crear opciones de `status` nuevas vía API, así que el pipeline escribe
> siempre `"Not started"` al crear una tarea (nunca inventa un estado de
> avance). `Estado` en Reuniones, en cambio, sí es `select`.

**Base "Decisiones de diseño"** — el pipeline la lee y escribe en ella
in-línea dentro del paso 4 (ver
[Reconciliación y aplicación](#reconciliación-y-aplicación-pasos-6-y-7)):
| Propiedad | Tipo | Nombre real en `config.yaml` |
|---|---|---|
| Decision | Title | `Decision` |
| Tema | Text | `Tema` |
| Razon | Text (**obligatorio** — el campo más importante de esta base) | `Razon` |
| Estado | **Select** — Vigente, Tentativa, Superada, Revertida | `Estado` |
| Prototipo | Select (Autonomo, Mediado, Escolar, Hogar, Transversal) | `Prototipo` |
| Fecha | Date | `Fecha` |
| Reunión origen | Relation → "Reuniones" | `Reunion origen` |
| Decision madre | Self-relation → una de las 4 decisiones madre | `Decision madre` |
| Sub-decisiones | Self-relation (lado inverso de Decision madre) | `Sub-decisiones` |
| Reemplazada por | Self-relation → la decisión que la dejó obsoleta | `Reemplazada por` |
| Reemplaza a | Self-relation (lado inverso de Reemplazada por) | `Reemplaza a` |
| Tareas de implementación | Relation → "Tareas del equipo" | `Tareas de implementacion` |

> **`Estado` describe VIGENCIA, nunca avance de implementación.** Una
> decisión ya tomada pero pendiente de ejecutar sigue **Vigente** — el
> trabajo va como Tarea con `Tareas de implementacion` apuntando a ella,
> nunca se usa un estado tipo "in progress" acá. Cuando el equipo cambia de
> opinión, el pipeline **nunca sobrescribe** una decisión: crea la nueva
> Vigente, marca la anterior Superada, y enlaza `Reemplazada por` (en la
> anterior) / `Reemplaza a` (lado inverso).
>
> **Toda decisión nueva cuelga de una de las 4 decisiones madre**
> (`Decision madre` obligatorio) — catálogo cerrado en
> `config.yaml → notion.decisiones_madre`, ver
> [Épicas y decisiones madre](#épicas-y-decisiones-madre).

**Base "Ideas"** — nivel más bajo del pipeline (propuestas sin compromiso
todavía). No tiene relation a Reuniones, solo un campo de texto:
| Propiedad | Tipo | Nombre real en `config.yaml` |
|---|---|---|
| Idea | Title | `Idea` |
| Estado | Select (Propuesta, En discusion, Aprobada, Descartada, Congelada) | `Estado` |
| Horizonte | Select (Ahora, V2 MIM, Feria, Futuro, Congelada) | `Horizonte` |
| Encaje filosofico | Select (Refuerza, Neutro, Tensiona) | `Encaje filosofico` |
| Prototipo | **Multi-select** (incluye "Estacion nueva", a diferencia de Tareas/Decisiones) | `Prototipo` |
| Origen | Text libre (título de la reunión, no relation) | `Origen` |
| Esfuerzo | Select (Bajo, Medio, Alto, Muy alto) | `Esfuerzo` |
| Problema que resuelve | Text | `Problema que resuelve` |

**Comparte las cuatro bases con tu integración** — este paso es fácil de
olvidar y la falla resultante no es obvia: Notion devuelve un
`404 object_not_found` (no un error de permisos) si la base existe pero no
está compartida. En cada base: `···` (esquina superior derecha) → "Conexiones"
→ selecciona tu integración.

Copia el ID de cada base (está en la URL: `notion.so/xxxxx?v=...` — el `xxxxx`
de 32 caracteres es el ID). **Los IDs no van en `config.yaml`** (ese archivo
se versiona en git) — van en `.env`, y `config.yaml` solo referencia el
nombre de la variable, igual que `whisperx.hf_token_env`:

```yaml
# config/config.yaml
notion:
  reuniones_database_id_env: "NOTION_REUNIONES_DATABASE_ID"
  tareas_database_id_env: "NOTION_TAREAS_DATABASE_ID"
  decisiones_database_id_env: "NOTION_DECISIONES_DATABASE_ID"
  ideas_database_id_env: "NOTION_IDEAS_DATABASE_ID"
```

```bash
# .env
NOTION_REUNIONES_DATABASE_ID=xxxxx
NOTION_TAREAS_DATABASE_ID=xxxxx
NOTION_DECISIONES_DATABASE_ID=xxxxx
NOTION_IDEAS_DATABASE_ID=xxxxx
```

### Épicas y decisiones madre

`config.yaml → notion.epicas` y `notion.decisiones_madre` son catálogos
cerrados (código → `{id, nombre}`) que el pipeline usa para resolver a qué
página real de Notion cuelga cada tarea/decisión nueva. Son deliberadamente
un catálogo chico y estable — el pipeline nunca crea una épica o decisión
madre nueva por su cuenta (ver Filosofía). Si necesitas agregar una:

1. Créala a mano en Notion (una tarea sin `Tarea madre` para una épica
   nueva, o una decisión sin `Decision madre` para una decisión madre nueva).
2. Copia su ID de página y agrégala a `config.yaml` con el mismo formato que
   las existentes.
3. Si había tareas/decisiones "pendientes" esperando esa épica (ver
   [Reconciliación y aplicación](#reconciliación-y-aplicación-pasos-6-y-7)),
   reintenta aplicando ese archivo pendiente.

Para el paso 5 (resumen semanal) también necesitas compartir con la
integración la página "Ventana Celeste" (o la que configures como
`resumen_semanal.pagina_padre`) — el pipeline busca esa página por nombre
para crear debajo la subpágina "Resúmenes semanales" la primera vez que
corre. Si quieres otros nombres, ajusta `resumen_semanal.pagina_padre` y
`resumen_semanal.pagina_resumenes` en `config.yaml` (por defecto: "Ventana
Celeste" → "Resúmenes semanales").

### 4. Ajustar vocabulario controlado

Edita `config/config.yaml` → `proyectos`, `tags_permitidos`,
`personas_permitidas`, `prototipos_permitidos`, `prototipos_ideas` y
`areas_permitidas` con tus valores reales (ya viene pre-cargado con los del
proyecto Ventana Celeste). Estas listas se inyectan automáticamente en el
prompt de extracción; `epicas` y `decisiones_madre` (catálogos con ID real
de Notion) se ajustan aparte, ver
[Épicas y decisiones madre](#épicas-y-decisiones-madre).

### Resolución de personas (`Responsable`)

El campo `Responsable` de la base Tareas es tipo *person* — Notion exige el
user ID real de cada persona, no su nombre en texto. El pipeline extrae
nombres en texto libre del audio (de la lista `personas_permitidas`) y
necesita mapearlos a IDs antes de escribir. Ese mapeo vive en
`config.yaml → notion.resolucion_personas`:

```yaml
notion:
  resolucion_personas:
    Francisco: "24bd872b-594c-81d6-bccc-0002620e0c90"
    Alessio: null
    Gonzalo: null
```

Cómo conseguir un user ID: en Notion, ve a **Settings → People**, abre el
perfil de la persona y copia el ID de la URL. Si la persona es invitada
("guest") con acceso limitado a páginas específicas, es posible que
`/v1/users` (lo que usa el pipeline como respaldo automático) no la
devuelva — en ese caso el ID a mano en `resolucion_personas` es la única vía.

Cuando un nombre extraído no tiene ID (ni en `resolucion_personas` ni vía
API), el pipeline **no** lo descarta silenciosamente: deja el campo
`Responsable` sin esa persona, anota `[Responsable sin resolver: "Nombre"]`
en el campo `Notas` de la tarea, y lo imprime como advertencia al correr
`04_push_notion.py` (donde ahora corre `07_aplicar_cambios.py::resolver_persona()`
in-línea). `05_weekly_digest.py` además agrega al resumen semanal en Notion
cuántas tareas siguen con un responsable sin resolver.

## Uso

> El esquema Reunión → Idea → Decisión → Tarea y la reconciliación in-línea
> contra Notion en vivo se agregaron el 2026-08-25. La sección
> [Qué se probó y qué no](#qué-se-probó-y-qué-no) más abajo describe una
> corrida anterior a este cambio — su contenido histórico usa nombres de
> campos ya deprecados (`Responsable (IA)`, sin `Área`/`Tarea madre`, etc.).

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

# Si por esta vez no quieres crear las tareas en Notion (solo la reunión +
# ideas/decisiones), agrega --sin-tareas.
python scripts/04_push_notion.py data/staging/<archivo_generado>.json --sin-tareas
```

Qué hace el paso 4 puntualmente: crea el acta en "Reuniones", y para cada
idea/decisión/tarea candidata de esta reunión — reconcilia in-línea contra
Notion en vivo (¿ya existe algo equivalente?) y aplica el resultado: crea lo
genuinamente nuevo (con Tarea madre / Decision madre / Reunion origen
resueltos), actualiza lo existente que corresponda (estado de una idea,
vigencia de una decisión, incluyendo marcar Superada + enlazar Reemplazada
por si una decisión reemplaza a otra), y dedupe tareas que ya existen en la
misma épica. Al final corre 3 chequeos de consistencia (tareas/decisiones
huérfanas, decisiones Superada sin reemplazo) y los imprime. Si algo queda
pendiente (típicamente: ninguna épica encaja para una tarea), se guarda en
`data/staging/pendientes_reconciliacion_<fecha-hora>.json` — resuélvelo
(ver [Épicas y decisiones madre](#épicas-y-decisiones-madre)) y corre
`python scripts/07_aplicar_cambios.py <ese_archivo>` para reintentar.

### Paso 5 (independiente) — Resumen semanal en Notion

No depende de audio ni de WhisperX — solo hace llamadas HTTP a Notion y
Claude, así que se puede correr desde un cron o tarea programada sin el
resto del entorno de transcripción instalado:

```bash
python scripts/05_weekly_digest.py
```

Qué hace: junta las reuniones de los últimos 7 días desde la base
"Reuniones", les pide a Claude un resumen en texto plano, y corre la
reconciliación (pasos 6/7) como **red de seguridad** — normalmente cada
reunión ya se reconcilió sola al correr el paso 4, así que esto solo
encuentra algo si esa reconciliación quedó pendiente en alguna reunión
reciente. Agrega todo — resumen + recuento de qué se creó/actualizó — como
bloque nuevo al final de la página "Resúmenes semanales" en Notion (la crea
si no existe todavía).

### Reconciliación y aplicación (pasos 6 y 7)

Normalmente no los corres a mano — `04_push_notion.py` ya los invoca
in-línea por vos. Sirven para depurar sin re-crear el acta, o para
reintentar algo que quedó pendiente:

```bash
# Paso 6 — compara candidatos contra Notion en vivo, junta de los últimos 7
# días desde data/processed/*.json, guarda el bundle en data/staging/:
python scripts/06_reconciliacion.py

# Paso 7 — aplica un bundle ya generado (por defecto, el más reciente):
python scripts/07_aplicar_cambios.py
python scripts/07_aplicar_cambios.py data/staging/reconciliacion_propuesta_20260819_120000.json
```

`06_reconciliacion.py` **nunca escribe en Notion** — solo compara (ideas
existentes, decisiones no-Superadas, tareas no-Done agrupadas por épica) y
guarda `{"candidatos": ..., "propuesta": ...}` en
`data/staging/reconciliacion_propuesta_<fecha-hora>.json`. La escritura real
la hace `07_aplicar_cambios.py`: crea páginas nuevas (con todo el contenido
completo en el cuerpo, a diferencia de páginas cargadas a mano que suelen
quedar en blanco) y actualiza las existentes, con las reglas de self-relation
child-side descritas en Setup de Notion.

Si algo falla o queda pendiente a mitad de camino, **no** se pierde ni se
duplica nada: el bundle se reescribe solo con lo pendiente (los candidatos
que ya se aplicaron bien no se vuelven a tocar), listo para reintentar
corriendo el script de nuevo.

> Esta es la única escritura automática (sin revisión humana previa) de
> este pipeline — el resto del flujo (el acta en sí) también escribe solo,
> pero todo pasa primero por el punto de control del paso 3. Las páginas que
> este mecanismo crea o modifica quedan marcadas en su contenido como
> generadas por el pipeline, para poder distinguirlas de una carga manual si
> algo se ve raro.

## Visibilidad de progreso y errores

Todos los scripts (`01` a `07`) usan `scripts/progress.py` para no dejarte
mirando una consola congelada durante los pasos largos (transcripción,
diarización, llamadas a Claude):

- **Cada paso largo imprime cuándo empieza, un "latido" cada ~20s mientras
  sigue corriendo** (con el tiempo transcurrido), **y cuándo termina** (con
  el tiempo total) — así sabés que sigue vivo aunque la librería subyacente
  (WhisperX, pyannote) no dé ninguna señal propia de progreso.
- **Cada corrida escribe un log completo** en `data/logs/<script>_<fecha-hora>.log`
  con todo lo que se imprimió en pantalla — útil si cerraste la terminal, se
  perdió el scroll, o corriste algo en segundo plano y volvés horas después.
- **Los errores no atrapados se reportan de forma clara**, con el paso en el
  que ocurrieron y cuánto tardó antes de fallar, en vez de un traceback
  crudo perdido en el buffer de la consola — el traceback completo igual
  queda guardado en el log, por si necesitás depurarlo.

Ejemplo de lo que ves ahora en un paso largo (antes no había nada entre el
"Diarizando..." inicial y el resultado final, aunque tardara 45 minutos):

```
▶ [4/4] Diarizando — identificando hablantes (puede tardar varios minutos en CPU)...
   … sigue en curso (20s transcurridos)
   … sigue en curso (40s transcurridos)
   … sigue en curso (1m 00s transcurridos)
✅ [4/4] Diarizando — identificando hablantes (puede tardar varios minutos en CPU) — listo (1m 12s)
```

## Qué revisar en el paso de staging

El archivo `.md` generado pone **arriba** cualquier señal de incertidumbre:

- `confianza_metadata` distinta de "alta" → revisa proyecto/tags sugeridos.
- Tareas con `confianza: "baja"` → verifica el responsable asignado.
- Tareas con `epica_sugerida: "NINGUNA_ENCAJA"` → revisa la justificación;
  van a quedar pendientes en el paso 4 hasta que agregues la épica.
- El **nivel** de cada cosa: ¿de verdad es una Tarea y no una Idea todavía sin
  compromiso? ¿Una Decisión de verdad quedó zanjada, o sigue Tentativa?
- Cualquier entrada en `advertencias_extraccion` → el modelo te dice explícitamente
  qué no le quedó claro (hablante ambiguo, tarea sin dueño evidente, nivel dudoso, etc.).

Si todo se ve bien, aprueba y avanza al paso 4. Si algo está mal, edita el `.json`
correspondiente en `data/staging/` — es la fuente de verdad, no el `.md`. Recordá
que esto son candidatos: el paso 4 todavía va a comparar contra Notion en vivo
antes de crear nada.

## Qué se probó y qué no

Estado real al momento de escribir esto, para que sepas exactamente qué
confiar y qué verificar vos mismo:

**Probado end-to-end con audio y APIs reales** (audio de una sola voz,
"Francisco", en `data/audio/test.mp3`):
1. `01_transcribe.py` → transcripción + diarización real.
2. `02_extract.py` → extracción estructurada real con Claude (sin datos de
   relleno: personas, título, tareas, etc. salen del audio).
3. `03_staging_review.py` → `.md` de revisión generado.
4. `04_push_notion.py` → página creada en la base real "Reuniones" y su
   tarea asociada en "Tareas", con Estado/Prioridad/Responsable (IA) en el
   formato correcto (confirmado por la respuesta 200 de Notion, no solo por
   inspección de payload).
5. `05_weekly_digest.py` → encontró la reunión recién creada, generó el
   resumen con Claude, lo mandó por Telegram y creó la subpágina
   "Resúmenes semanales" bajo "Ventana Celeste" (no existía todavía).
6. `06_decisiones_reconciliacion.py` (invocado desde el paso 5) → como el
   audio de prueba no generó decisiones, devolvió una propuesta vacía
   (`{"nuevas": [], "actualizaciones": []}`) sin llamar a Claude
   innecesariamente, y la guardó en
   `data/staging/decisiones_propuesta_<fecha>.json`. La comparación real
   vía Claude (con al menos una decisión de por medio) queda para la
   próxima vez que haya una reunión con decisiones reales.

**Problemas encontrados durante esta corrida, y cómo se resolvieron** (ver
también el historial de commits para el detalle completo):
- `mkl_malloc: failed to allocate memory` al cargar el modelo Whisper
  `large-v3` — falla transitoria de asignación de memoria; se resolvió
  reintentando el mismo comando.
- `UnicodeEncodeError` al imprimir emojis (✅, ⚠️) cuando los scripts corren
  en una consola Windows con code page `cp1252` (pasa en Git Bash / cmd.exe,
  no en esta versión de PowerShell) — se agregó
  `sys.stdout.reconfigure(encoding="utf-8")` al inicio de todos los scripts.
- `config.yaml` apuntaba a los IDs de las bases de Notion como strings
  literales (`"NOTION_REUNIONES_DATABASE_ID"`) en vez de sus valores reales,
  y el código todavía no sabía resolver eso — se implementó
  `notion_client.get_database_id()`, que resuelve el ID real desde la
  variable de entorno indicada en `config.yaml` (`*_database_id_env`, mismo
  patrón que `hf_token_env`/`bot_token_env`). De paso se corrigió un typo en
  `.env` (`NOTION_DECITIONS_DATABASE_ID` → `NOTION_DECISIONES_DATABASE_ID`).

**Sin probar todavía:**
- La reconciliación de decisiones con una decisión real de por medio (ver
  punto 6 arriba) — falta una reunión real con `decisiones` no vacías.
- El caso de una segunda corrida del paso 5 en la misma semana (¿la
  subpágina "Resúmenes semanales" acumula bloques correctamente sin
  duplicar contenido? — debería, porque solo hace `append`, pero no se
  verificó dos veces seguidas).

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
