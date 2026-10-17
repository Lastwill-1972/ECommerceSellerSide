from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.merchants.models import Merchant
from app.modules.merchants.schemas import MerchantDirectoryItem, MerchantDirectoryPage


class MerchantDirectoryService:
    @staticmethod
    def list_directory(session: Session, *, limit: int, offset: int) -> MerchantDirectoryPage:
        total = session.scalar(select(func.count(Merchant.id))) or 0
        merchants = session.execute(
            select(Merchant.id, Merchant.name)
            .order_by(Merchant.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return MerchantDirectoryPage(
            items=[MerchantDirectoryItem(id=row.id, name=row.name) for row in merchants],
            limit=limit,
            offset=offset,
            total=total,
        )
