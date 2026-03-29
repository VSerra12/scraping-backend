import logging
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func, cast
from sqlalchemy.types import Text
from app.models.models import Product, Store, SearchLog, ProductVariant
from app.models.schemas import SearchRequest, SearchResponse, ProductSummary, VariantRead

logger = logging.getLogger(__name__)


def _jl(column, term):
    """Like sobre columna JSON casteada a texto, case-insensitive."""
    return func.lower(cast(column, Text)).like(term)


def search_products(request: SearchRequest, db: Session) -> SearchResponse:
    query = db.query(Product).join(Store).options(joinedload(Product.variants))
    query = query.filter(Product.available == True)   # noqa: E712
    query = query.filter(Store.active == True)        # noqa: E712

    # ── Búsqueda por texto libre ──────────────────────────────────────────────
    if request.query:
        t = f"%{request.query.lower()}%"
        query = query.filter(or_(
            func.lower(Product.title).like(t),
            func.lower(Product.description).like(t),
            func.lower(Product.category).like(t),
            func.lower(Product.subcategory).like(t),
            _jl(Product.style_tags, t),
            _jl(Product.colors, t),
            _jl(Product.design_details, t),
            _jl(Product.materials, t),
        ))

    # ── Filtros base ──────────────────────────────────────────────────────────
    if request.category:
        query = query.filter(func.lower(Product.category) == request.category.lower())

    if request.min_price is not None:
        query = query.filter(Product.price >= request.min_price)

    if request.max_price is not None:
        query = query.filter(Product.price <= request.max_price)

    if request.store_id is not None:
        query = query.filter(Product.store_id == request.store_id)

    if request.color:
        query = query.filter(_jl(Product.colors, f"%{request.color.lower()}%"))

    if request.gender:
        query = query.filter(or_(
            func.lower(Product.gender) == request.gender.lower(),
            func.lower(Product.gender) == "unisex",
        ))

    if request.location:
        lt = f"%{request.location.lower()}%"
        query = query.filter(or_(
            func.lower(Store.location).like(lt),
            func.lower(Store.country).like(lt),
        ))

    # ── Filtros expandidos ────────────────────────────────────────────────────
    if request.cut:
        query = query.filter(func.lower(Product.cut) == request.cut.lower())

    if request.pattern:
        query = query.filter(func.lower(Product.pattern) == request.pattern.lower())

    if request.style_tag:
        query = query.filter(_jl(Product.style_tags, f"%{request.style_tag.lower()}%"))

    if request.neck_type:
        query = query.filter(func.lower(Product.neck_type) == request.neck_type.lower())

    if request.sleeve_type:
        query = query.filter(func.lower(Product.sleeve_type) == request.sleeve_type.lower())

    if request.stretch is not None:
        query = query.filter(Product.stretch == request.stretch)

    # ── Ordenamiento y paginación ─────────────────────────────────────────────
    total = query.count()
    query = query.order_by(func.random())
    products = query.offset(request.offset).limit(request.limit).all()

    # ── Serialización ─────────────────────────────────────────────────────────
    store_map = {s.id: s.name for s in db.query(Store).all()}
    results = []
    for p in products:
        pr = ProductSummary.model_validate(p)
        pr.store_name = store_map.get(p.store_id)
        pr.variants = [
            VariantRead.model_validate(v)
            for v in p.variants
            if v.available
        ]
        results.append(pr)

    # ── Log de búsqueda ───────────────────────────────────────────────────────
    try:
        db.add(SearchLog(query=request.query, results_count=total, filters_used={}))
        db.commit()
    except Exception as e:
        logger.warning(f"Log error: {e}")

    return SearchResponse(query=request.query, total=total, results=results)