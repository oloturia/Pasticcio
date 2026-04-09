# ============================================================
# app/models/__init__.py
# ============================================================
#
# Importing all models here serves two purposes:
#
# 1. Alembic needs to "see" all models when it generates
#    migrations. We import Base and all models in alembic/env.py,
#    and this single import pulls everything in automatically.
#
# 2. SQLAlchemy needs all models to be imported before it can
#    resolve relationships between them (e.g. User → Recipe).
#    Importing them here guarantees the right order.

from app.models.recipe import (
    Difficulty,
    IngredientUnit,
    Recipe,
    RecipeIngredient,
    RecipePhoto,
    RecipeStatus,
    RecipeTranslation,
    TranslationStatus,
    FoodItem,
)
from app.models.user import User

__all__ = [
    "User",
    "Recipe",
    "RecipeTranslation",
    "RecipeIngredient",
    "RecipePhoto",
    "RecipeStatus",
    "TranslationStatus",
    "Difficulty",
    "IngredientUnit",
    "FoodItem",
    "Follower",
]

from app.models.follower import Follower
from app.models.cooked_this import CookedThis
from app.models.reaction import Reaction, ReactionType
from app.models.known_instance import KnownInstance
from app.models.moderation import Bookmark, BlockType, InstanceRule, RuleType, UserBlock
from app.models.recipe import RecipeStepPhoto
from app.models.cooked_this import CookedThisPhoto
from app.models.follow_request import FollowRequest, FollowRequestStatus
from app.models.notification import Notification, NotificationType
