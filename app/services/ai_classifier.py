"""
Servicio de clasificación de productos usando Claude API.
Solo clasifica productos nuevos — los existentes NO se re-procesan
salvo que se llame con force=True desde el enrichment service.
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
#  System prompt
# ─────────────────────────────────────────────────────────────────────────────
CLASSIFICATION_SYSTEM = """
Sos un experto en moda argentina. Tu única tarea es clasificar prendas de ropa.
Estos son SIEMPRE productos de vestimenta de tiendas argentinas.

Devolvés ÚNICAMENTE un objeto JSON válido. Sin texto adicional, sin backticks, sin explicaciones.

════════════════════════════════════════════
REGLA #1 — CATEGORÍA (la más importante)
════════════════════════════════════════════
Leé PRIMERO la descripción completa, LUEGO el título.
Si la descripción dice explícitamente qué prenda es (ej: "Remera manga larga..."),
usá ESA PALABRA como category. NUNCA uses "otro" si la descripción o el título
contienen una de estas palabras:

  remera / camiseta / t-shirt → "remera"
  musculosa → "musculosa"
  top / crop top → "top" o "crop_top"
  blusa → "blusa"
  camisa → "camisa"
  chomba → "chomba"
  vestido / enterito → "vestido"
  falda / pollera → "falda"
  jean / jeans → "jean"
  pantalón / pantalon → "pantalon"
  short / bermuda → "short" o "bermuda"
  legging / calza → "legging" o "calza"
  buzo / hoodie → "buzo"
  sweater / sweter / suéter → "sweater"
  cardigan / cárdigan → "cardigan"
  blazer / saco → "blazer"
  campera / parka / anorak → "campera"
  tapado / abrigo → "tapado"
  chaleco → "chaleco"
  jogger → "jogger"
  conjunto → "conjunto"

Solo usá "otro" si NINGUNA fuente (título, descripción, imagen) permite determinarlo.

════════════════════════════════════════════
REGLA #2 — COLORES
════════════════════════════════════════════
Prioridad ESTRICTA para determinar el color:
1. Si hay colores_hint en el mensaje → USARLOS siempre, son los colores reales del producto
2. Si la descripción menciona el color explícitamente → usarlo
3. Si hay imagen → inferir visualmente
4. Si solo está el título → inferir del nombre (ej: "negro", "blanca", "rayada")

"Negro" como fallback solo si realmente no hay otra información.
Para prendas rayadas: intentá determinar los colores de las rayas.
  - Rayas grises/blancas → colors: ["gris", "blanco"], pattern: "rayado"
  - Rayas azul/blanco → colors: ["azul", "blanco"], pattern: "rayado"

════════════════════════════════════════════
CAMPOS Y VALORES
════════════════════════════════════════════

category (obligatorio):
  remera | musculosa | crop_top | blusa | camisa | polo | top | body |
  jean | pantalon | short | bermuda | falda | legging | calza | culotte |
  vestido | blazer | campera | tapado | chaleco | cardigan | sweater | buzo |
  jogger | chomba | conjunto | accesorio | zapatillas | otro

subcategory (obligatorio):
  Texto libre MUY descriptivo: tipo + fit + largo + tiro + detalles clave.
  Si el título es solo un nombre (ej: "SANDY"), construilo DESDE LA DESCRIPCIÓN.
  Ejemplos:
    "remera manga larga cuello redondo viscosa rayada"
    "jean wide leg tiro alto largo completo"
    "vestido midi wrap escote v sin mangas"
    "campera oversized con capucha gabardina"
    "falda mini plisada a-line"

fit (puede ser null):
  slim_fit | regular_fit | relaxed_fit | oversize | boxy | entallado | null

leg_cut (solo inferiores y faldas, puede ser null):
  skinny | slim | straight | regular | tapered | wide_leg | baggy |
  bootcut | flare | palazzo | a_line | pencil | null

rise (solo prendas inferiores, puede ser null):
  low_rise | mid_rise | high_rise | null

length (puede ser null):
  cropped | corto | midi | tobillero | largo | null

materials (array, puede estar vacío []):
  algodon, lino, denim, lana, poliester, seda, cuero, viscosa, elastano,
  nylon, acrilico, modal, rayon, cachemir, morley, lycra, spandex,
  gabardina, microfibra, jersey, otro

  CONVERSIONES obligatorias:
  "morley" → algodon | "jean/denim" → denim | "lycra/spandex" → elastano
  "viscosa/rayón/rayon" → viscosa | "jersey" → jersey | "algodón/cotton" → algodon

texture (puede ser null): suave | rugosa | rigida | elastica | otro | null
thickness (puede ser null): liviano | medio | grueso | null
stretch (bool o null): true si tiene lycra/spandex/elastano/stretch, false si no, null si no se sabe

colors (array, obligatorio, mínimo 1 elemento):
  negro, blanco, gris, rojo, azul, verde, amarillo, naranja, rosa, violeta,
  marron, beige, celeste, bordo, camel, nude, dorado, plateado, multicolor,
  azul marino, verde oliva

colors_secondary (array, puede estar vacío []):
  Mismos valores. Para detalles, estampados o colores secundarios.

pattern (puede ser null):
  liso | rayado | floral | cuadros | animal_print | tie_dye | geometrico |
  lunares | abstracto | estampado_grafico | otro | null

design_details (array, puede estar vacío []):
  botones, cierre, bolsillos, bordados, volados, pliegues, costuras_visibles,
  capucha, hombreras, lazos, flecos, encaje, apliques, estampado_grafico,
  rasgado, parches, tiras_cruzadas, ribetes, hotfix, abertura, lazo, faja

neck_type (null para prendas inferiores):
  redondo | v | alto | camisa | bote | halter | bandeja | asimetrico | sin_cuello | otro | null

sleeve_type (null para prendas inferiores):
  corta | larga | tres_cuartos | sin_mangas | globo | raglan | campana | otra | null

hem_finish (puede ser null):
  dobladillo_simple | elastizado | ribbed | raw_hem | otro | null

style_tags (array, mínimo 1):
  urbano, deportivo, casual, elegante, vintage, oriental, bohemio,
  minimalista, romantico, streetwear, formal, oversize, comodo,
  trendy, rock, basico, preppy, gothic, y2k, surf, outdoor

gender (obligatorio): mujer | hombre | unisex
condition (obligatorio): new | used

════════════════════════════════════════════
EJEMPLOS
════════════════════════════════════════════

Título: "SANDY RAYADA" / Desc: "Remera manga larga con cuello redondo en suave viscosa rayada"
→ { "category": "remera", "subcategory": "remera manga larga cuello redondo viscosa rayada",
    "sleeve_type": "larga", "neck_type": "redondo", "length": "largo",
    "materials": ["viscosa"], "pattern": "rayado", "colors": ["gris", "blanco"],
    "style_tags": ["casual", "basico"], "gender": "mujer", "condition": "new" }

Título: "BARBI LOCALIZADO" / Desc: "Jean tiro alto piernas anchas elastizado"
→ { "category": "jean", "subcategory": "jean wide leg tiro alto",
    "fit": "relaxed_fit", "leg_cut": "wide_leg", "rise": "high_rise", "length": "largo",
    "stretch": true, "materials": ["denim", "elastano"], "colors": ["negro"],
    "style_tags": ["casual", "trendy"], "gender": "mujer", "condition": "new" }

Título: "OSLO" / Desc: "Remera básica de jersey 100% algodón cuello redondo oversize"
→ { "category": "remera", "subcategory": "remera oversize cuello redondo jersey algodón",
    "fit": "oversize", "neck_type": "redondo", "sleeve_type": "corta",
    "materials": ["algodon", "jersey"], "colors": ["negro"],
    "style_tags": ["casual", "basico", "oversize"], "gender": "unisex", "condition": "new" }

Título: "CAMPERA CARGO VERDE" / Desc: "Campera de gabardina con múltiples bolsillos"
→ { "category": "campera", "subcategory": "campera cargo gabardina bolsillos",
    "materials": ["gabardina"], "design_details": ["bolsillos"],
    "colors": ["verde oliva"], "pattern": "liso",
    "style_tags": ["urbano", "casual", "streetwear"], "gender": "unisex", "condition": "new" }

Título: "VESTIDO LILA MIDI" / sin descripción
→ { "category": "vestido", "subcategory": "vestido midi",
    "length": "midi", "colors": ["violeta"],
    "style_tags": ["casual", "romantico"], "gender": "mujer", "condition": "new" }
"""

USER_TEMPLATE = """Título: {title}
Tienda: {store_context}
Descripción: {description}
Materiales detectados en página: {materials}
{colors_line}
{keyword_hint}
Clasificá esta prenda:"""


def _extract_category_hint(title: str, description: str) -> str:
    """
    Busca palabras clave de categoría en título + descripción.
    Si las encuentra, agrega un hint explícito al prompt para reforzar.
    """
    KEYWORDS = {
        "remera": "remera", "camiseta": "remera", "t-shirt": "remera", "tshirt": "remera",
        "musculosa": "musculosa",
        "top": "top", "crop": "crop_top",
        "blusa": "blusa", "camisa": "camisa", "chomba": "chomba", "polo": "polo",
        "body": "body",
        "vestido": "vestido", "enterito": "vestido",
        "falda": "falda", "pollera": "falda",
        "jean": "jean", "jeans": "jean",
        "pantalon": "pantalon", "pantalón": "pantalon",
        "short": "short", "bermuda": "bermuda",
        "legging": "legging", "calza": "calza", "culotte": "culotte",
        "buzo": "buzo", "hoodie": "buzo",
        "sweater": "sweater", "sweter": "sweater", "suéter": "sweater",
        "cardigan": "cardigan", "cárdigan": "cardigan",
        "blazer": "blazer", "saco": "blazer",
        "campera": "campera", "parka": "campera", "anorak": "campera",
        "tapado": "tapado", "abrigo": "tapado",
        "chaleco": "chaleco", "jogger": "jogger", "conjunto": "conjunto",
    }

    text = (title + " " + (description or "")).lower()
    found = []
    for word, cat in KEYWORDS.items():
        if word in text and cat not in found:
            found.append(cat)

    if found:
        cats = ", ".join(found)
        return f"⚠ IMPORTANTE: Las siguientes categorías fueron detectadas en el texto: [{cats}]. Usar la más específica como 'category'. NO usar 'otro'."
    return ""


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
    colors_hint: colores reales extraídos de las variantes del producto.
    """
    desc = description or "Sin descripción — clasificar por título e imagen"
    store_context = store_name or "tienda de ropa argentina"

    # Extraer materiales de la descripción si hay palabras clave
    materials = "No especificado"
    if description:
        mat_keywords = [
            "tela:", "tejido:", "composición:", "composicion:", "material:",
            "confeccionado en", "100%", "morley", "lycra", "algodón", "polyester",
            "microfibra", "denim", "lino", "seda", "modal", "spandex", "viscosa",
            "jersey", "rayón", "elastano", "nylon", "gabardina", "cachemir",
        ]
        for kw in mat_keywords:
            if kw in description.lower():
                materials = description[:300]
                break

    colors_line = ""
    if colors_hint:
        colors_line = (
            f"Colores disponibles en variantes (son los colores REALES del producto, "
            f"usar OBLIGATORIAMENTE en el campo 'colors'): {', '.join(colors_hint)}"
        )

    keyword_hint = _extract_category_hint(title, description or "")

    prompt = USER_TEMPLATE.format(
        title=title,
        description=desc[:600],
        materials=materials,
        store_context=store_context,
        colors_line=colors_line,
        keyword_hint=keyword_hint,
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
        "category":         category,
        "subcategory":      subcategory,
        "colors":           colors,
        "style_tags":       style_tags,
        "gender":           gender,
        "condition":        clean_str(data.get("condition"), "new"),
        "cut":              clean_str(data.get("fit") or data.get("cut")),
        "leg_cut":          clean_str(data.get("leg_cut")),
        "rise":             clean_str(data.get("rise")),
        "length":           clean_str(data.get("length")),
        "materials":        clean_list(data.get("materials")),
        "texture":          clean_str(data.get("texture")),
        "thickness":        clean_str(data.get("thickness")),
        "stretch":          data.get("stretch") if isinstance(data.get("stretch"), bool) else None,
        "colors_secondary": clean_list(data.get("colors_secondary")),
        "pattern":          clean_str(data.get("pattern")),
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