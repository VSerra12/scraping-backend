import logging
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func, cast
from sqlalchemy.types import Text
from app.models.models import Product, Store, SearchLog, ProductVariant
from app.models.schemas import SearchRequest, SearchResponse, ProductRead, VariantRead

logger = logging.getLogger(__name__)


def _jl(column, term):
    return func.lower(cast(column, Text)).like(term)


def search_products(request, db):
    query = db.query(Product).join(Store).options(joinedload(Product.variants))
    query = query.filter(Product.available == True)   # noqa: E712
    query = query.filter(Store.active == True)        # noqa: E712

    if request.query:
        t = f"%{request.query.lower()}%"
        query = query.filter(or_(
            func.lower(Product.title).like(t),
            func.lower(Product.description).like(t),
            func.lower(Product.category).like(t),
            func.lower(Product.subcategory).like(t),
            _jl(Product.style_tags, t),
            _jl(Product.colors, t),
        ))

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

    total = query.count()
    query = query.order_by(Product.ai_classified.desc(), Product.price.asc().nulls_last())
    products = query.offset(request.offset).limit(request.limit).all()

    store_map = {s.id: s.name for s in db.query(Store).all()}
    results = []
    for p in products:
        pr = ProductRead.model_validate(p)
        pr.store_name = store_map.get(p.store_id)
        pr.variants = [
            VariantRead.model_validate(v)
            for v in p.variants
            if v.available
        ]
        results.append(pr)

    try:
        db.add(SearchLog(query=request.query, results_count=total, filters_used={}))
        db.commit()
    except Exception as e:
        logger.warning(f"Log error: {e}")

    return SearchResponse(query=request.query, total=total, results=results)