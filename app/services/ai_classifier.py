"""
Servicio de clasificación de productos usando Claude API.
Solo clasifica productos nuevos — los existentes NO se re-procesan.
"""
import json
import logging
import base64
from typing import Optional
import httpx
import anthropic
from app.core.config import settings

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

CLASSIFICATION_PROMPT = """Sos un experto en moda argentina. Tu tarea es clasificar prendas de ropa.

Producto:
Título: {title}
Descripción: {description}
Materiales/Tela: {materials}
Tienda: {store_context}
{colors_line}

INSTRUCCIONES IMPORTANTES:
- Estos son SIEMPRE prendas de vestir de una tienda de ropa argentina.
- Si el título es solo un nombre propio (ej: "PENNY", "LIA", "BARREL"), usá la descripción e imagen para clasificar.
- NUNCA uses "otro", "sin especificar", "no especificado" ni strings vacíos.
- Si hay "Colores disponibles" listados arriba, usá esos directamente en el campo colors.
- Para género: si no hay indicación clara, inferí por el estilo de la prenda y la tienda.

Respondé SOLO con JSON válido, sin texto adicional ni backticks:
{{
  "category": "remera|buzo|campera|pantalon|zapatillas|vestido|falda|bermuda|short|jogger|chomba|chaleco|tapado|impermeable|top|jean|calza|body|musculosa|blazer|camisa|cardigan|sweater|accesorio",
  "subcategory": "descripción breve (ej: manga larga, oversize, tiro alto, cuello redondo, bastón fino)",
  "colors": ["color1", "color2"],
  "style_tags": ["estilo1", "estilo2"],
  "gender": "hombre|mujer|unisex"
}}

Colores válidos: negro, blanco, rojo, azul, verde, amarillo, rosa, gris, beige, marrón, naranja, violeta, celeste, bordó, camel, nude, estampado, multicolor, azul marino, verde oliva.
Estilos válidos: urbano, deportivo, casual, elegante, vintage, streetwear, bohemio, minimalista, formal, oversize, slim, cómodo, trendy, romántico, rock, básico."""


def classify_product(
    title: str,
    description: Optional[str] = None,
    image_url: Optional[str] = None,
    store_name: Optional[str] = None,
    colors_hint: Optional[list] = None,
) -> dict:
    """
    Clasifica un producto con IA.
    Usa imagen si está disponible.
    colors_hint: colores reales extraídos de las variantes del producto (más precisos que IA).
    """
    desc = description or "Sin descripción — clasificar por título e imagen"
    store_context = store_name or "tienda de ropa argentina"

    # Detectar materiales embebidos en la descripción
    materials = "No especificado"
    if description:
        mat_keywords = ["tela:", "tejido:", "composición:", "material:", "confeccionado en",
                        "100%", "morley", "lycra", "algodón", "polyester", "microfibra"]
        for kw in mat_keywords:
            if kw in description.lower():
                materials = description[:300]
                break

    colors_line = ""
    if colors_hint:
        colors_line = f"Colores disponibles (reales, usar estos): {', '.join(colors_hint)}"

    prompt = CLASSIFICATION_PROMPT.format(
        title=title,
        description=desc[:600],
        materials=materials,
        store_context=store_context,
        colors_line=colors_line,
    )

    # Intentar con imagen primero
    if image_url:
        result = _classify_with_image(prompt, image_url)
        if result:
            if colors_hint:
                result["colors"] = [c.lower() for c in colors_hint[:5]]
            return result

    # Fallback: solo texto
    result = _classify_text_only(prompt)
    if colors_hint:
        result["colors"] = [c.lower() for c in colors_hint[:5]]
    return result


def _classify_with_image(prompt: str, image_url: str) -> Optional[dict]:
    """Clasifica usando la imagen del producto."""
    try:
        image_data, media_type = _download_image(image_url)
        if not image_data:
            return None

        message = client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }]
        )
        return _parse_response(message.content[0].text)

    except Exception as e:
        logger.warning(f"Error clasificando con imagen: {e}. Usando solo texto.")
        return None


def _classify_text_only(prompt: str) -> dict:
    """Clasifica usando solo el texto del producto."""
    try:
        message = client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )
        return _parse_response(message.content[0].text)

    except anthropic.APIError as e:
        logger.error(f"Error de API de Anthropic: {e}")
        return _fallback_classification()
    except Exception as e:
        logger.error(f"Error inesperado en clasificación: {e}")
        return _fallback_classification()


def _download_image(url: str) -> tuple[Optional[str], str]:
    """Descarga una imagen y la convierte a base64."""
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as http:
            r = http.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; FashionSearchBot/1.0)"
            })
            if r.status_code != 200:
                return None, "image/jpeg"

            ct = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if "webp" in ct:
                media_type = "image/webp"
            elif "png" in ct:
                media_type = "image/png"
            else:
                media_type = "image/jpeg"

            # Imágenes muy grandes aumentan el costo innecesariamente
            if len(r.content) > 1_500_000:
                logger.debug(f"Imagen muy grande ({len(r.content)} bytes), skip")
                return None, media_type

            return base64.standard_b64encode(r.content).decode("utf-8"), media_type

    except Exception as e:
        logger.debug(f"Error descargando imagen: {e}")
        return None, "image/jpeg"


def _parse_response(raw: str) -> dict:
    """Parsea y limpia la respuesta JSON de Claude."""
    raw = raw.strip()
    # Limpiar backticks si los hay
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando JSON de IA: {e} | Raw: {raw[:200]}")
        return _fallback_classification()

    INVALID = {
        "sin especificar", "sin definir", "no especificado", "indeterminado",
        "n/a", "otro", "", "sin información", "no disponible", "no especificado",
    }

    colors = [c for c in (data.get("colors") or []) if c and c.lower() not in INVALID]
    if not colors:
        colors = ["negro"]

    style_tags = [s for s in (data.get("style_tags") or []) if s and s.lower() not in INVALID]
    if not style_tags:
        style_tags = ["casual"]

    category = (data.get("category") or "").lower().strip()
    if not category or category in INVALID:
        category = "remera"

    subcategory = (data.get("subcategory") or "").strip()
    if subcategory.lower() in INVALID:
        subcategory = ""

    gender = (data.get("gender") or "mujer").lower()
    if gender not in ("hombre", "mujer", "unisex"):
        gender = "mujer"

    return {
        "category": category,
        "subcategory": subcategory,
        "colors": colors,
        "style_tags": style_tags,
        "gender": gender,
    }


def _fallback_classification() -> dict:
    return {
        "category": "remera",
        "subcategory": "",
        "colors": ["negro"],
        "style_tags": ["casual"],
        "gender": "mujer",
    }