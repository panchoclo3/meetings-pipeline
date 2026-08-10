"""
Cliente ligero para la API REST de Notion.

Por qué API directa y no el conector MCP aquí: el MCP de Notion en Claude.ai
está pensado para uso interactivo dentro de una conversación. Este script
corre desatendido, fuera del chat, y necesita control explícito de reintentos,
rate limits y errores — la API REST directa es más predecible para eso.

El MCP de Notion sigue siendo la herramienta correcta para cuando TÚ le
preguntas algo a Claude desde el chat (paso de consulta, fuera de este script).
"""

import os
import time
import requests

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def get_database_id(cfg_notion: dict, name: str) -> str:
    """
    Resuelve el ID real de una base de datos a partir de la variable de
    entorno indicada en config.yaml (ej: notion.reuniones_database_id_env).

    Por qué: config.yaml se versiona en git, así que los IDs reales de las
    bases (no son secretos como una API key, pero sí específicos de tu
    workspace) viven en .env — mismo patrón que hf_token_env/bot_token_env.
    """
    env_var_key = f"{name}_database_id_env"
    env_var_name = cfg_notion.get(env_var_key)
    if not env_var_name:
        raise RuntimeError(f"Falta '{env_var_key}' en config.yaml → notion.")
    value = os.environ.get(env_var_name)
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno {env_var_name} en .env con el ID "
            f"real de la base '{name}'. Ver README.md sección 'Setup de Notion'."
        )
    return value


class NotionClient:
    def __init__(self, token: str = None):
        self.token = token or os.environ.get("NOTION_API_KEY")
        if not self.token:
            raise RuntimeError(
                "Falta NOTION_API_KEY en el entorno. Ver README.md sección 'Configuración'."
            )
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }

    def _request(self, method: str, path: str, payload: dict = None, retries: int = 3):
        url = f"{NOTION_API_URL}{path}"
        for attempt in range(retries):
            resp = requests.request(method, url, headers=self.headers, json=payload)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                print(f"  Rate limited por Notion, esperando {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Notion API error {resp.status_code}: {resp.text}")
            return resp.json()
        raise RuntimeError("Se agotaron los reintentos ante rate limiting de Notion.")

    def create_page(self, database_id: str, properties: dict, children: list = None) -> dict:
        payload = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        if children:
            payload["children"] = children
        return self._request("POST", "/pages", payload)

    def create_subpage(self, parent_page_id: str, title: str, children: list = None) -> dict:
        """
        Crea una página simple (no un item de base de datos) bajo otra página.
        Usado para la página "Resúmenes semanales" del paso 5 — a diferencia de
        create_page(), el parent es page_id y la única propiedad es "title".
        """
        payload = {
            "parent": {"page_id": parent_page_id},
            "properties": {"title": prop_title(title)},
        }
        if children:
            payload["children"] = children
        return self._request("POST", "/pages", payload)

    def query_database(self, database_id: str, filter_: dict = None, sorts: list = None) -> list:
        """
        Devuelve TODAS las páginas de una base, manejando la paginación de
        Notion internamente (el llamador no necesita preocuparse de cursors).
        """
        payload = {}
        if filter_:
            payload["filter"] = filter_
        if sorts:
            payload["sorts"] = sorts
        results = []
        while True:
            resp = self._request("POST", f"/databases/{database_id}/query", payload)
            results.extend(resp["results"])
            if not resp.get("has_more"):
                break
            payload["start_cursor"] = resp["next_cursor"]
        return results

    def get_block_children(self, block_id: str) -> list:
        """Devuelve TODOS los bloques hijos de una página o bloque, paginando."""
        results = []
        start_cursor = None
        while True:
            path = f"/blocks/{block_id}/children"
            if start_cursor:
                path += f"?start_cursor={start_cursor}"
            resp = self._request("GET", path)
            results.extend(resp["results"])
            if not resp.get("has_more"):
                break
            start_cursor = resp["next_cursor"]
        return results

    def append_block_children(self, block_id: str, children: list) -> dict:
        return self._request("PATCH", f"/blocks/{block_id}/children", {"children": children})

    def search(self, query: str, object_type: str = None) -> list:
        """
        Busca páginas/bases por título. Nota: solo encuentra objetos
        compartidos con esta integración — si algo "no aparece", lo más
        probable es que falte compartirlo (ver README.md).
        """
        payload = {"query": query}
        if object_type:
            payload["filter"] = {"value": object_type, "property": "object"}
        resp = self._request("POST", "/search", payload)
        return resp.get("results", [])


# ---------------------------------------------------------------------------
# Helpers para construir propiedades de Notion en el formato que espera la API
# ---------------------------------------------------------------------------

def prop_title(text: str) -> dict:
    return {"title": [{"text": {"content": text[:2000]}}]}


def prop_rich_text(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text[:2000]}}]}


def prop_select(value: str) -> dict:
    return {"select": {"name": value}} if value else {"select": None}


def prop_status(value: str) -> dict:
    # A diferencia de "select", Notion no permite crear opciones de "status"
    # nuevas vía API — el valor debe coincidir exactamente con una opción ya
    # definida en la base (ver mapeo de estados en 04_push_notion.py).
    return {"status": {"name": value}} if value else {"status": None}


def prop_multi_select(values: list) -> dict:
    return {"multi_select": [{"name": v} for v in values]}


def prop_date(iso_date: str) -> dict:
    return {"date": {"start": iso_date}}


def prop_relation(page_ids: list) -> dict:
    return {"relation": [{"id": pid} for pid in page_ids]}


# ---------------------------------------------------------------------------
# Helpers para construir bloques de contenido (cuerpo de la página)
# ---------------------------------------------------------------------------

def block_heading(text: str, level: int = 2) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": [{"text": {"content": text}}]}}


def block_paragraph(text: str) -> dict:
    # Notion limita cada rich_text a 2000 caracteres; se trunca por seguridad.
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": text[:2000]}}]},
    }


def block_bulleted_item(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"text": {"content": text[:2000]}}]},
    }


def block_plain_text(block: dict) -> str:
    """
    Extrae el texto plano de un bloque de Notion sin importar su tipo
    (heading_1/2/3, paragraph, bulleted_list_item, to_do, quote, etc.).
    Devuelve "" para bloques sin texto (divider, imagen, etc.) en vez de fallar.
    """
    block_type = block.get("type")
    content = block.get(block_type, {}) if block_type else {}
    rich_text = content.get("rich_text", [])
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def page_title_plain_text(page: dict, title_property: str = None) -> str:
    """
    Extrae el título de una página de Notion. Si no se indica `title_property`,
    busca automáticamente la propiedad de tipo "title" (su nombre varía entre
    bases: "Nombre", "Agenda", "title" en páginas simples, etc.).
    """
    properties = page.get("properties", {})
    prop = properties.get(title_property) if title_property else None
    if prop is None:
        prop = next((p for p in properties.values() if p.get("type") == "title"), None)
    if not prop:
        return ""
    return "".join(rt.get("plain_text", "") for rt in prop.get("title", []))


def block_todo(text: str, checked: bool = False) -> dict:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": [{"text": {"content": text[:2000]}}],
            "checked": checked,
        },
    }
