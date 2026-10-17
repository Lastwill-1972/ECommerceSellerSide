"""Import all model modules so Alembic sees their shared Base.metadata.

Define future mappings in the corresponding module's models.py using app.db.base.Base.
If a module splits models into a package, re-export its mappings in that package.
"""

from app.modules.auth import models as auth_models
from app.modules.catalog import models as catalog_models
from app.modules.employees import models as employees_models
from app.modules.inventory import models as inventory_models
from app.modules.merchants import models as merchants_models
from app.modules.orders import models as orders_models
from app.modules.shops import models as shops_models
from app.modules.users import models as users_models

__all__ = [
    "auth_models", "catalog_models", "employees_models", "inventory_models",
    "merchants_models", "orders_models", "shops_models", "users_models",
]
