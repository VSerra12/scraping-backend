"""
Servicio de clasificación de productos usando Claude API.
Solo clasifica productos nuevos — los existentes NO se re-procesan.

Optimizaciones de costo aplicadas:
- Prompt caching: el system prompt (~1.800 tokens) se cachea entre llamadas
  del mismo batch, pagando $0.30/MTok en vez de $3/MTok en las relecturas.
- Imagen condicional: solo se usa imagen cuando el texto es insuficiente
  para clasificar (título sin palabras clave de prenda + descripción corta).
- Descripción limpia: se elimina texto de marketing irrelevante antes de
  mandar al modelo, reduciendo tokens de input innecesarios.
"""
import json
import logging
import re
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
Sos un experto en moda argentina. Tu única tarea es clasificar prendas de ropa de tiendas argentinas.
SIEMPRE son productos de vestimenta. Nunca respondas que no podés clasificar.

Devolvés ÚNICAMENTE un objeto JSON válido. Sin texto adicional, sin backticks, sin explicaciones.

════════════════════════════════════════
PASO 1 — LEER EL TÍTULO CON ATENCIÓN
════════════════════════════════════════
El título tiene palabras clave cruciales. Buscá:
- Tipo de prenda: "remera", "polera", "buzo", "jean", "vestido", "campera", etc.
- Cuello: "cuello alto", "tortuga", "cuello v", "cuello redondo", "escote"
- Manga: "manga larga", "manga corta", "sin mangas", "3/4"
- Largo: "cropped", "midi", "maxi", "mini"
- Fit: "oversize", "ajustado", "entallado", "wide leg", "slim"
- Material: "algodón", "lycra", "denim", "morley", "viscosa", "lino"

════════════════════════════════════════
CAMPOS OBLIGATORIOS Y VALORES PERMITIDOS
════════════════════════════════════════

category (OBLIGATORIO — elegí el que mejor describe la prenda):
  remera        → remeras, camisetas, poleras, tops manga corta/larga con cuello
  musculosa     → tirantes, sin mangas, musculosas
  crop_top      → tops cortos que dejan la panza al descubierto
  blusa         → blusas con caída suave, generalmente femeninas
  camisa        → camisas con botones, formales o casuales
  polo          → chomba con cuello polo
  top           → tops sin categoría clara, deportivos
  body          → bodies/enteritos de tela
  jean          → pantalones de denim/jean
  pantalon      → pantalones de cualquier tela que no sea denim
  short         → shorts, bermudas cortas
  bermuda       → bermudas largas hasta la rodilla
  falda         → faldas/polleras de cualquier largo
  legging       → leggings y calzas ajustadas
  calza         → calzas deportivas
  culotte       → pantalón culotte/palazzo corto
  vestido       → vestidos de cualquier largo
  blazer        → blazers y sacos
  campera       → camperas, buzos con cierre, rompevientos, pilotos
  tapado        → tapados, abrigos largos
  chaleco       → chalecos sin mangas
  cardigan      → cardigans abiertos al frente
  sweater       → sweaters, pulóveres de punto
  buzo          → buzos con o sin capucha (sin cierre o con cierre corto)
  jogger        → pantalones jogger/deportivos
  chomba        → chomba/polo
  conjunto      → conjuntos de dos o más piezas
  accesorio     → accesorios, cinturones, bolsos
  zapatillas    → calzado
  otro          → SOLO si realmente no entra en ninguna categoría anterior

REGLAS CRÍTICAS PARA category:
- "Polera" en Argentina = remera de cuello alto → category: "remera", neck_type: "alto"
- "Polera manga larga" → category: "remera", sleeve_type: "larga", neck_type: "alto"  
- Si el título dice "remera", "polera", "camiseta" → NUNCA uses "otro"
- Si el título dice "jean", "denim" → category: "jean"
- Si el título dice "buzo", "hoodie", "sweatshirt" → category: "buzo"
- Si el título dice "campera", "rompeviento", "piloto" → category: "campera"
- "Crop" en el título → length: "cropped"
- Conjuntos (top+short, top+pantalon) → category: "conjunto"

subcategory (OBLIGATORIO — descripción detallada):
  Texto libre MUY descriptivo combinando tipo + fit + largo + cuello + manga + detalles.
  Mínimo 4 palabras. Ejemplos:
    "remera manga larga cuello alto ajustada algodón lycra"
    "remera básica cuello redondo manga corta oversize"
    "jean wide leg tiro alto denim elastizado"
    "vestido midi manga larga escote v cruzado"
    "campera oversize con capucha frisa interior"
    "buzo cropped cuello redondo sin capucha"
    "polera cuello alto manga larga lycra ajustada"

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
  Inferí de palabras clave:
  - "algodón", "cotton" → algodon
  - "lycra", "spandex", "elastano" → elastano
  - "denim", "jean" (la tela) → denim
  - "morley" → algodon (morley es punto de algodón)
  - "viscosa", "viscose" → viscosa
  - "lino" → lino
  - "poliéster", "polyester" → poliester
  - "lana", "wool" → lana
  - "seda", "silk" → seda
  - "modal" → modal
  - "ribb", "rib", "canalé" → algodon (tela acanalada, generalmente algodón)
  - "jersey" → jersey
  - "nylon" → nylon
  - "cuero", "leather" → cuero
  - "gabardina" → gabardina
  Valores: algodon, lino, denim, lana, poliester, seda, cuero, viscosa, elastano,
           nylon, acrilico, modal, rayon, cachemir, morley, lycra, spandex,
           gabardina, microfibra, jersey, otro

texture (puede ser null):
  suave | rugosa | rigida | elastica | otro | null

thickness (puede ser null):
  liviano | medio | grueso | null

stretch (true/false/null):
  true → si menciona lycra, spandex, elastano, elastizado, stretch, elástico
  false → si es rígido, sin stretch
  null → si no se puede determinar

colors (array OBLIGATORIO, al menos 1):
  Si hay colors_hint en el mensaje → USÁ EXACTAMENTE ESOS COLORES.
  Si no hay colors_hint → inferí del título/descripción/imagen.
  Valores: negro, blanco, gris, rojo, azul, verde, amarillo, naranja, rosa, violeta,
           marron, beige, celeste, bordo, camel, nude, dorado, plateado, multicolor,
           azul_marino, verde_oliva, crudo, natural, off_white

colors_secondary (array, puede estar vacío []):
  Colores de detalles, costuras, estampados secundarios. Mismos valores que colors.

pattern (puede ser null):
  liso | rayado | floral | cuadros | animal_print | tie_dye | geometrico |
  lunares | abstracto | estampado_grafico | otro | null

design_details (array, puede estar vacío []):
  botones, cierre, bolsillos, bordados, volados, pliegues, costuras_visibles,
  capucha, hombreras, lazos, flecos, encaje, apliques, estampado_grafico,
  rasgado, parches, tiras_cruzadas, ribetes, hotfix, abertura, lazo, faja

neck_type (solo prendas superiores, puede ser null):
  redondo | v | alto | camisa | bote | halter | bandeja | asimetrico | sin_cuello | otro | null
  IMPORTANTE: "polera" y "cuello tortuga/rulo" → alto

sleeve_type (solo prendas superiores, puede ser null):
  corta | larga | tres_cuartos | sin_mangas | globo | raglan | campana | otra | null

hem_finish (puede ser null):
  dobladillo_simple | elastizado | ribbed | raw_hem | otro | null

style_tags (array OBLIGATORIO, al menos 1):
  urbano, deportivo, casual, elegante, vintage, oriental, bohemio,
  minimalista, romantico, streetwear, formal, oversize, comodo,
  trendy, rock, basico, preppy, gothic, y2k, surf, outdoor

gender (OBLIGATORIO):
  mujer | hombre | unisex
  → Si la tienda vende solo ropa de mujer y no hay indicación → mujer

condition (OBLIGATORIO):
  new | used

════════════════════════════════════════
EJEMPLOS DE CLASIFICACIÓN
════════════════════════════════════════

Título: "Amanda / Polera Manga Larga Algodón y Lycra"
→ {
    "category": "remera",
    "subcategory": "polera manga larga cuello alto ajustada algodón lycra",
    "fit": "entallado",
    "neck_type": "alto",
    "sleeve_type": "larga",
    "materials": ["algodon", "elastano"],
    "stretch": true,
    "colors": ["negro"],
    "pattern": "liso",
    "style_tags": ["basico", "casual"],
    "gender": "mujer",
    "condition": "new"
  }

Título: "Campera Rompeviento Oversize con Capucha"
→ {
    "category": "campera",
    "subcategory": "campera rompeviento oversize con capucha impermeable",
    "fit": "oversize",
    "design_details": ["capucha", "cierre", "bolsillos"],
    "materials": ["nylon"],
    "colors": ["negro"],
    "pattern": "liso",
    "style_tags": ["urbano", "outdoor", "streetwear"],
    "gender": "unisex",
    "condition": "new"
  }
"""

# Palabras de prenda que hacen suficiente al texto para clasificar sin imagen
_PRENDA_KEYWORDS = {
    "remera", "polera", "camiseta", "blusa", "camisa", "top", "musculosa",
    "crop", "body", "jean", "pantalon", "pantalón", "short", "bermuda",
    "falda", "pollera", "legging", "calza", "vestido", "blazer", "campera",
    "tapado", "chaleco", "cardigan", "sweater", "buzo", "hoodie", "jogger",
    "conjunto", "zapatilla", "chomba",
}

# Patrones de texto de marketing a eliminar antes de mandar al modelo
_NOISE_PATTERNS = [
    r"envío gratis[^\.\n]*",
    r"env[íi]o gratis[^\.\n]*",
    r"pag[áa] en \d+[^\.\n]*cuotas[^\.\n]*",
    r"comprá ahora[^\.\n]*",
    r"stock limitado[^\.\n]*",
    r"oferta[^\.\n]*",
    r"\d+% de descuento[^\.\n]*",
    r"seguinos en[^\.\n]*",
    r"visit[áa][^\.\n]*",
    r"\bwww\.\S+",
    r"https?://\S+",
    r"tel[eé]fono[^\.\n]*",
    r"whatsapp[^\.\n]*",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)

USER_TEMPLATE = """=== DATOS DEL PRODUCTO ===
Título: {title}
Tienda: {store_context}
Descripción: {description}
Materiales detectados: {materials}
{colors_line}

=== INSTRUCCIÓN ===
Analizá el título palabra por palabra. "{title_keywords}" son las palabras clave.
Clasificá esta prenda y devolvé SOLO el JSON:"""


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers de preparación de datos
# ─────────────────────────────────────────────────────────────────────────────

def _clean_description(text: str) -> str:
    """
    Elimina texto de marketing irrelevante para reducir tokens de input.
    Limita a 600 caracteres (suficiente para clasificar, vs 800 anterior).
    """
    cleaned = _NOISE_RE.sub("", text)
    # Colapsar espacios/líneas múltiples que quedaron
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned[:600]


def _text_is_sufficient(title: str, description: Optional[str]) -> bool:
    """
    Determina si el texto solo alcanza para clasificar sin necesitar imagen.
    True cuando:
      - El título contiene una palabra de tipo de prenda conocida, O
      - Hay descripción con más de 80 caracteres útiles.
    """
    title_lower = title.lower()
    has_prenda_keyword = any(kw in title_lower for kw in _PRENDA_KEYWORDS)
    has_good_description = bool(description and len(description.strip()) > 80)
    return has_prenda_keyword or has_good_description


def _extract_title_keywords(title: str) -> str:
    """Extrae las palabras más relevantes del título para enfatizarlas en el prompt."""
    keywords = []
    title_lower = title.lower()

    prenda_words = [
        "remera", "polera", "camiseta", "blusa", "camisa", "top", "musculosa",
        "crop", "body", "jean", "pantalon", "short", "bermuda", "falda", "pollera",
        "legging", "calza", "vestido", "blazer", "campera", "tapado", "chaleco",
        "cardigan", "sweater", "buzo", "hoodie", "jogger", "conjunto"
    ]
    for word in prenda_words:
        if word in title_lower:
            keywords.append(word)

    cuello_words = [
        "cuello alto", "tortuga", "cuello v", "cuello redondo", "escote v",
        "cuello bote", "manga larga", "manga corta", "sin mangas", "cropped",
        "oversize", "oversized", "wide leg", "tiro alto", "slim", "straight"
    ]
    for phrase in cuello_words:
        if phrase in title_lower:
            keywords.append(phrase)

    mat_words = [
        "algodón", "algodon", "lycra", "denim", "jean", "morley", "viscosa",
        "lino", "lana", "seda", "modal", "ribb", "rib"
    ]
    for word in mat_words:
        if word in title_lower:
            keywords.append(word)

    return ", ".join(keywords) if keywords else title[:50]


def _extract_materials_from_text(title: str, description: Optional[str]) -> str:
    """Extrae menciones de materiales del título y la descripción."""
    combined = f"{title} {description or ''}".lower()

    mat_keywords = {
        "algodón": "algodón", "algodon": "algodón", "cotton": "algodón",
        "100% algodón": "100% algodón", "100% algodon": "100% algodón",
        "lycra": "lycra/elastano", "spandex": "spandex/elastano",
        "elastano": "elastano", "elastizada": "elastizado",
        "denim": "denim", "jean": "jean/denim",
        "morley": "morley (punto algodón)",
        "viscosa": "viscosa", "viscose": "viscosa",
        "lino": "lino", "linen": "lino",
        "poliéster": "poliéster", "polyester": "poliéster", "poliester": "poliéster",
        "lana": "lana", "wool": "lana",
        "seda": "seda", "silk": "seda",
        "modal": "modal",
        "nylon": "nylon",
        "ribb": "ribb (acanalado)", "rib": "rib (acanalado)", "canalé": "canalé",
        "jersey": "jersey",
        "gabardina": "gabardina",
        "microfibra": "microfibra",
    }

    found = []
    for kw, label in mat_keywords.items():
        if kw in combined and label not in found:
            found.append(label)

    return ", ".join(found) if found else "No especificado"


def _normalize_colors(colors: list) -> list:
    """Normaliza y mapea colores a los valores válidos del sistema."""
    color_map = {
        "negro": "negro", "black": "negro",
        "blanco": "blanco", "white": "blanco", "crudo": "crudo", "natural": "natural",
        "gris": "gris", "grey": "gris", "gray": "gris",
        "rojo": "rojo", "red": "rojo",
        "azul": "azul", "blue": "azul",
        "celeste": "celeste", "light blue": "celeste", "cielo": "celeste",
        "azul marino": "azul_marino", "marino": "azul_marino", "navy": "azul_marino",
        "verde": "verde", "green": "verde",
        "verde oliva": "verde_oliva", "oliva": "verde_oliva", "olive": "verde_oliva",
        "amarillo": "amarillo", "yellow": "amarillo",
        "naranja": "naranja", "orange": "naranja",
        "rosa": "rosa", "pink": "rosa",
        "violeta": "violeta", "purple": "violeta", "lila": "violeta",
        "marron": "marron", "marrón": "marron", "brown": "marron",
        "beige": "beige",
        "bordo": "bordo", "bordó": "bordo", "vino": "bordo", "burgundy": "bordo",
        "camel": "camel",
        "nude": "nude",
        "dorado": "dorado", "gold": "dorado",
        "plateado": "plateado", "silver": "plateado",
        "multicolor": "multicolor",
        "off white": "off_white", "off-white": "off_white",
    }

    normalized = []
    for color in colors:
        if not color:
            continue
        c = str(color).lower().strip()
        mapped = color_map.get(c, c)
        if mapped not in normalized:
            normalized.append(mapped)

    return normalized if normalized else ["negro"]


# ─────────────────────────────────────────────────────────────────────────────
#  Función principal
# ─────────────────────────────────────────────────────────────────────────────

def classify_product(
    title: str,
    description: Optional[str] = None,
    image_url: Optional[str] = None,
    store_name: Optional[str] = None,
    colors_hint: Optional[list] = None,
) -> dict:
    """
    Clasifica un producto con IA.

    Lógica de uso de imagen (para reducir costo):
      - Si el texto es suficiente (título con keyword de prenda O descripción > 80 chars)
        → clasifica solo con texto (más barato).
      - Si el texto es insuficiente (título ambiguo como "EMILY" sin descripción)
        → intenta con imagen, con fallback a texto.

    colors_hint: colores reales extraídos de variantes (más precisos que IA).
    """
    # Limpiar descripción antes de cualquier uso
    clean_desc = _clean_description(description) if description else None

    desc_for_prompt = clean_desc or "Sin descripción disponible — clasificar por título e imagen"
    store_context = store_name or "tienda de ropa argentina"
    materials_text = _extract_materials_from_text(title, clean_desc)

    colors_line = ""
    if colors_hint:
        colors_normalized = _normalize_colors(colors_hint)
        colors_line = f"Colores reales de variantes (USAR ESTOS en el campo 'colors'): {', '.join(colors_normalized)}"

    title_keywords = _extract_title_keywords(title)

    prompt = USER_TEMPLATE.format(
        title=title,
        description=desc_for_prompt,
        materials=materials_text,
        store_context=store_context,
        colors_line=colors_line,
        title_keywords=title_keywords,
    )

    # Decidir si usar imagen o no
    use_image = image_url and not _text_is_sufficient(title, clean_desc)

    if use_image:
        logger.debug(f"  Usando imagen para '{title[:40]}' (texto insuficiente)")
        result = _classify_with_image(prompt, image_url)
        if result:
            if colors_hint:
                result["colors"] = _normalize_colors(colors_hint)[:8]
            return result
        logger.info(f"  Imagen falló para '{title[:40]}', usando solo texto")
    else:
        logger.debug(f"  Usando solo texto para '{title[:40]}' (texto suficiente)")

    result = _classify_text_only(prompt)

    if colors_hint:
        result["colors"] = _normalize_colors(colors_hint)[:8]

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Llamadas a la API
# ─────────────────────────────────────────────────────────────────────────────

def _classify_with_image(prompt: str, image_url: str) -> Optional[dict]:
    """Clasifica usando la imagen del producto."""
    try:
        image_data, media_type = _download_image(image_url)
        if not image_data:
            return None

        message = client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            # Cache aplicado al system prompt — se reutiliza entre llamadas
            # del mismo batch pagando $0.30/MTok en vez de $3/MTok
            system=[
                {
                    "type": "text",
                    "text": CLASSIFICATION_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
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
        raw = message.content[0].text
        result = _parse_response(raw)

        if result.get("category") == "otro":
            logger.warning(f"Clasificación con imagen devolvió 'otro'.")

        return result

    except anthropic.APIError as e:
        logger.error(f"APIError clasificando con imagen: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error clasificando con imagen: {e}")
        return None


def _classify_text_only(prompt: str) -> dict:
    """Clasifica usando solo el texto del producto."""
    try:
        message = client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            # Cache aplicado al system prompt — se reutiliza entre llamadas
            # del mismo batch pagando $0.30/MTok en vez de $3/MTok
            system=[
                {
                    "type": "text",
                    "text": CLASSIFICATION_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt}]
        )
        return _parse_response(message.content[0].text)

    except anthropic.APIError as e:
        logger.error(f"Error de API de Anthropic: {e}")
        return _fallback_classification()
    except Exception as e:
        logger.error(f"Error inesperado en clasificación: {e}")
        return _fallback_classification()


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers de imagen y parsing
# ─────────────────────────────────────────────────────────────────────────────

def _download_image(url: str) -> tuple[Optional[str], str]:
    """Descarga una imagen y la convierte a base64."""
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as http:
            r = http.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; FashionSearchBot/1.0)"
            })
            if r.status_code != 200:
                logger.debug(f"Imagen HTTP {r.status_code}: {url}")
                return None, "image/jpeg"

            ct = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if "webp" in ct:
                media_type = "image/webp"
            elif "png" in ct:
                media_type = "image/png"
            elif "gif" in ct:
                media_type = "image/gif"
            else:
                media_type = "image/jpeg"

            if len(r.content) > 1_500_000:
                logger.debug(f"Imagen muy grande ({len(r.content)/1024:.0f}KB), saltando")
                return None, media_type

            return base64.standard_b64encode(r.content).decode("utf-8"), media_type

    except Exception as e:
        logger.debug(f"Error descargando imagen {url}: {e}")
        return None, "image/jpeg"


def _parse_response(raw: str) -> dict:
    """Parsea y valida la respuesta JSON de Claude."""
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
        logger.error(f"JSON inválido de IA: {e} | Raw: {raw[:300]}")
        return _fallback_classification()

    INVALID = {
        "sin especificar", "sin definir", "no especificado", "indeterminado",
        "n/a", "", "sin información", "no disponible", "desconocido",
    }

    def clean_list(val):
        if not val or not isinstance(val, list):
            return []
        return [v for v in val if v and str(v).lower() not in INVALID]

    def clean_str(val, fallback=None):
        if val is None:
            return fallback
        s = str(val).lower().strip()
        return fallback if s in INVALID else s

    colors = clean_list(data.get("colors"))
    if not colors:
        colors = ["negro"]

    style_tags = clean_list(data.get("style_tags"))
    if not style_tags:
        style_tags = ["casual"]

    category = clean_str(data.get("category"), "otro")
    subcategory = str(data.get("subcategory") or "").strip()
    if subcategory.lower() in INVALID:
        subcategory = ""

    gender = clean_str(data.get("gender"), "unisex")
    if gender not in ("hombre", "mujer", "unisex"):
        gender = "unisex"

    stretch_raw = data.get("stretch")
    if isinstance(stretch_raw, bool):
        stretch = stretch_raw
    elif isinstance(stretch_raw, str):
        stretch = True if stretch_raw.lower() == "true" else (False if stretch_raw.lower() == "false" else None)
    else:
        stretch = None

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
        "stretch":          stretch,

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