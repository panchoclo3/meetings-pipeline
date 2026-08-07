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


# ---------------------------------------------------------------------------
# Helpers para construir propiedades de Notion en el formato que espera la API
# ---------------------------------------------------------------------------

def prop_title(text: str) -> dict:
    return {"title": [{"text": {"content": text[:2000]}}]}


def prop_rich_text(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text[:2000]}}]}


def prop_select(value: str) -> dict:
    return {"select": {"name": value}} if value else {"select": None}


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


def block_todo(text: str, checked: bool = False) -> dict:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": [{"text": {"content": text[:2000]}}],
            "checked": checked,
        },
    }
