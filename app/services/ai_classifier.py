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

# ─────────────────────────────────────────────────────────────────────────────
#  System prompt (separado del user message para mejor control)
# ─────────────────────────────────────────────────────────────────────────────
CLASSIFICATION_SYSTEM = """
Sos un experto en moda argentina. Tu única tarea es clasificar prendas de ropa.
Estos son SIEMPRE productos de vestimenta de tiendas argentinas.

Devolvés ÚNICAMENTE un objeto JSON válido. Sin texto adicional, sin backticks, sin explicaciones.

CAMPOS Y VALORES PERMITIDOS:

category (obligatorio):
  remera | musculosa | crop_top | blusa | camisa | polo | top | body |
  jean | pantalon | short | bermuda | falda | legging | calza | culotte |
  vestido | blazer | campera | tapado | chaleco | cardigan | sweater | buzo |
  jogger | chomba | conjunto | accesorio | zapatillas | otro

subcategory (obligatorio):
  Texto libre MUY descriptivo combinando tipo + fit + largo + tiro + detalles clave.
  Ejemplos:
    "remera oversized cropped manga corta cuello redondo"
    "jean wide leg tiro alto largo completo"
    "vestido midi wrap escote v sin mangas"
    "campera oversized con capucha"
    "falda mini plisada a-line"

fit (puede ser null):
  slim_fit | regular_fit | relaxed_fit | oversize | boxy | entallado | null

leg_cut (solo para pantalones/jeans/shorts/faldas, puede ser null):
  skinny | slim | straight | regular | tapered | wide_leg | baggy |
  bootcut | flare | palazzo | a_line | pencil | null

rise (solo para prendas inferiores, puede ser null):
  low_rise | mid_rise | high_rise | null

length (puede ser null):
  cropped | corto | midi | tobillero | largo | null

materials (array, puede estar vacío []):
  algodon, lino, denim, lana, poliester, seda, cuero, viscosa, elastano,
  nylon, acrilico, modal, rayon, cachemir, morley, lycra, spandex,
  gabardina, microfibra, jersey, otro

texture (puede ser null):
  suave | rugosa | rigida | elastica | otro | null

thickness (puede ser null):
  liviano | medio | grueso | null

stretch (puede ser null):
  true si tiene elasticidad/stretch, false si no, null si no se puede determinar

colors (array, obligatorio, al menos 1):
  negro, blanco, gris, rojo, azul, verde, amarillo, naranja, rosa, violeta,
  marron, beige, celeste, bordo, camel, nude, dorado, plateado, multicolor,
  azul marino, verde oliva

colors_secondary (array, puede estar vacío []):
  Mismos valores que colors. Colores de detalles o secundarios.

pattern (puede ser null):
  liso | rayado | floral | cuadros | animal_print | tie_dye | geometrico |
  lunares | abstracto | estampado_grafico | otro | null

design_details (array, puede estar vacío []):
  botones, cierre, bolsillos, bordados, volados, pliegues, costuras_visibles,
  capucha, hombreras, lazos, flecos, encaje, apliques, estampado_grafico,
  rasgado, parches, tiras_cruzadas, ribetes, hotfix, abertura, lazo, faja

neck_type (puede ser null — solo para prendas superiores):
  redondo | v | alto | camisa | bote | halter | bandeja | asimetrico | sin_cuello | otro | null

sleeve_type (puede ser null — solo para prendas superiores):
  corta | larga | tres_cuartos | sin_mangas | globo | raglan | campana | otra | null

hem_finish (puede ser null):
  dobladillo_simple | elastizado | ribbed | raw_hem | otro | null

style_tags (array, al menos 1):
  urbano, deportivo, casual, elegante, vintage, oriental, bohemio,
  minimalista, romantico, streetwear, formal, oversize, comodo,
  trendy, rock, basico, preppy, gothic, y2k, surf, outdoor

gender (obligatorio):
  mujer | hombre | unisex

condition (obligatorio):
  new | used

REGLAS:
- Si el título es un nombre propio (ej: "PENNY", "LIA", "BARBI"), usá descripción e imagen.
- Si hay colores_hint en el mensaje, usá ESOS colores en el campo colors.
- NUNCA uses strings vacíos en campos obligatorios.
- Materiales: "morley" → algodon, "jean/denim" → denim, "lycra/spandex" → elastano, "jersey" → jersey.
- Para gender: si no hay indicación clara, inferí por estilo y tienda.
- leg_cut, rise y length son MUY importantes para pantalones y jeans — intentá siempre inferirlos.
- fit para prendas superiores indica silueta (oversize, slim, etc.).

EJEMPLOS DE CLASIFICACIÓN CORRECTA:

Título: "BARBI LOCALIZADO" / Desc: "Jean tiro alto piernas anchas elastizado"
→ category: jean, fit: relaxed_fit, leg_cut: wide_leg, rise: high_rise, length: largo,
   stretch: true, materials: [denim, elastano]

Título: "OSLO" / Desc: "Remera básica de jersey 100% algodón cuello redondo oversize"
→ category: remera, fit: oversize, neck_type: redondo, sleeve_type: corta,
   materials: [algodon, jersey], length: regular

Título: "FALDA PLISADA MIDI"
→ category: falda, leg_cut: a_line, length: midi, pattern: liso
"""

USER_TEMPLATE = """Título: {title}
Tienda: {store_context}
Descripción: {description}
Materiales detectados en página: {materials}
{colors_line}

Clasificá esta prenda:"""


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

    materials = "No especificado"
    if description:
        mat_keywords = ["tela:", "tejido:", "composición:", "composicion:", "material:",
                        "confeccionado en", "100%", "morley", "lycra", "algodón", "polyester",
                        "microfibra", "denim", "lino", "seda", "modal", "spandex"]
        for kw in mat_keywords:
            if kw in description.lower():
                materials = description[:300]
                break

    colors_line = ""
    if colors_hint:
        colors_line = f"Colores disponibles (reales, usar estos en el campo colors): {', '.join(colors_hint)}"

    prompt = USER_TEMPLATE.format(
        title=title,
        description=desc[:600],
        materials=materials,
        store_context=store_context,
        colors_line=colors_line,
    )

    if image_url:
        result = _classify_with_image(prompt, image_url)
        if result:
            if colors_hint:
                result["colors"] = [c.lower() for c in colors_hint[:5]]
            return result

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
            system=CLASSIFICATION_SYSTEM,
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
            system=CLASSIFICATION_SYSTEM,
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
        "n/a", "", "sin información", "no disponible",
    }

    def clean_list(val):
        if not val or not isinstance(val, list):
            return []
        return [v for v in val if v and str(v).lower() not in INVALID]

    def clean_str(val, fallback=None):
        if not val or str(val).lower() in INVALID:
            return fallback
        return str(val).lower().strip()

    colors = clean_list(data.get("colors"))
    if not colors:
        colors = ["negro"]

    style_tags = clean_list(data.get("style_tags"))
    if not style_tags:
        style_tags = ["casual"]

    category = clean_str(data.get("category"), "otro")
    subcategory = (data.get("subcategory") or "").strip()
    if subcategory.lower() in INVALID:
        subcategory = ""

    gender = clean_str(data.get("gender"), "unisex")
    if gender not in ("hombre", "mujer", "unisex"):
        gender = "unisex"

    return {
        # Campos base
        "category":         category,
        "subcategory":      subcategory,
        "colors":           colors,
        "style_tags":       style_tags,
        "gender":           gender,
        "condition":        clean_str(data.get("condition"), "new"),

        # Silueta y corte
        "cut":              clean_str(data.get("fit") or data.get("cut")),  # "fit" es el nombre nuevo
        "leg_cut":          clean_str(data.get("leg_cut")),
        "rise":             clean_str(data.get("rise")),
        "length":           clean_str(data.get("length")),

        # Materiales y textura
        "materials":        clean_list(data.get("materials")),
        "texture":          clean_str(data.get("texture")),
        "thickness":        clean_str(data.get("thickness")),
        "stretch":          data.get("stretch") if isinstance(data.get("stretch"), bool) else None,

        # Color y patrón
        "colors_secondary": clean_list(data.get("colors_secondary")),
        "pattern":          clean_str(data.get("pattern")),

        # Detalles constructivos
        "design_details":   clean_list(data.get("design_details")),
        "neck_type":        clean_str(data.get("neck_type")),
        "sleeve_type":      clean_str(data.get("sleeve_type")),
        "hem_finish":       clean_str(data.get("hem_finish")),
    }


def _fallback_classification() -> dict:
    return {
        "category":         "otro",
        "subcategory":      "",
        "colors":           ["negro"],
        "style_tags":       ["casual"],
        "gender":           "unisex",
        "condition":        "new",
        "cut":              None,
        "leg_cut":          None,
        "rise":             None,
        "length":           None,
        "materials":        [],
        "texture":          None,
        "thickness":        None,
        "stretch":          None,
        "colors_secondary": [],
        "pattern":          None,
        "design_details":   [],
        "neck_type":        None,
        "sleeve_type":      None,
        "hem_finish":       None,
    }