# app/routers/recipe_fork.py
"""
Router to handle recipe forking via browser interface.

This module handles recipe forking using traditional HTML forms.
Forking creates a copy of the original recipe that the user can then modify.
"""

from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models import User, Recipe, RecipeTranslation, RecipeIngredient

router = APIRouter()


@router.post("/recipes/{recipe_id}/fork")
async def fork_recipe_form(
    recipe_id: UUID,  # Changed from int to UUID
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """
    Handle recipe forking via HTML form.
    
    Workflow:
    1. Verify user is authenticated
    2. Fetch the original recipe from database
    3. Verify that recipe exists and is published
    4. Create a new recipe (draft) by copying data
    5. Copy translations and ingredients
    6. Set the forked_from field
    7. Redirect to the edit page of the new recipe
    
    Args:
        recipe_id: UUID of the recipe to fork
        request: FastAPI Request (for future flash messages)
        db: Async database session
        current_user: Authenticated user (dependency injection)
    
    Returns:
        RedirectResponse: Redirect to the edit page of the forked recipe
    
    Raises:
        HTTPException 401: User not authenticated
        HTTPException 404: Recipe not found
        HTTPException 403: Recipe not accessible (e.g., someone else's draft)
    """
    
    # Step 0: Check authentication
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Step 1: Fetch the original recipe with all relationships
    # We use selectinload for efficient eager loading to avoid N+1 queries
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            # Eager loading to avoid N+1 queries
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients)
        )
    )
    original_recipe = result.scalar_one_or_none()
    
    # Step 2: Validations
    if not original_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    # Don't allow forking other users' draft recipes
    if original_recipe.status == "draft" and original_recipe.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot fork draft recipes")
    
    # Don't allow forking deleted recipes
    if original_recipe.status == "deleted":
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    # Step 3: Create the new recipe as a draft
    # It starts as draft so the user can modify it before publishing
    # Generate a unique ap_id for the forked recipe
    fork_ap_id = f"https://{request.url.hostname}/users/{current_user.username}/recipes/{uuid4()}"
    
    # Make slug unique: add a short random suffix in case of multiple forks
    from slugify import slugify
    base_slug = f"{original_recipe.slug}-fork-{current_user.username}"
    unique_slug = f"{base_slug}-{str(uuid4())[:6]}"

    new_recipe = Recipe(
        author_id=current_user.id,
        slug=unique_slug,
        status="draft",
        # forked_from is a String field storing the AP ID of the original
        forked_from=original_recipe.ap_id,
        ap_id=fork_ap_id,
    )
    
    # Copy optional fields if they exist on the model
    # This makes the code resilient to different Recipe model versions
    optional_fields = [
        'prep_time_minutes', 'cook_time_minutes', 'servings', 
        'difficulty', 'dietary_info'
    ]
    for field in optional_fields:
        if hasattr(original_recipe, field):
            value = getattr(original_recipe, field)
            if value is not None:
                setattr(new_recipe, field, value)
    
    db.add(new_recipe)
    await db.flush()  # Flush to get the ID without committing
    
    # Step 4: Copy translations
    # A recipe can have translations in multiple languages
    for original_translation in original_recipe.translations:
        new_translation = RecipeTranslation(
            recipe_id=new_recipe.id,
            language=original_translation.language,
            title=f"{original_translation.title} (Fork)",  # Indicate it's a fork
            description=original_translation.description,
            steps=original_translation.steps,  # JSONB, gets copied
        )
        db.add(new_translation)
    
    # Step 5: Copy ingredients
    for original_ingredient in original_recipe.ingredients:
        new_ingredient = RecipeIngredient(
            recipe_id=new_recipe.id,
            food_item_id=original_ingredient.food_item_id,  # Reference to food_item
            quantity=original_ingredient.quantity,
            unit=original_ingredient.unit,  # Unit enum
            name=original_ingredient.name,  # Changed from notes to name
        )
        # Copy notes if it exists as a field
        if hasattr(original_ingredient, 'notes') and original_ingredient.notes:
            new_ingredient.notes = original_ingredient.notes
        db.add(new_ingredient)
    
    # Step 6: Commit the transaction
    await db.commit()
    await db.refresh(new_recipe)
    
    # Step 7: Redirect to recipe detail page
    # The detail page shows the inline edit panel for draft recipes
    return RedirectResponse(
        url=f"/api/v1/recipes/{new_recipe.id}",
        status_code=303
    )


@router.post("/recipes/fork-remote")
async def fork_remote_recipe_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """
    Handle forking of remote recipes (from other Pasticcio instances).
    
    This endpoint uses the existing /api/v1/recipes/fork API but adapts it
    for HTML forms instead of JSON.
    
    The form must send:
    - url: ActivityPub ID of the remote recipe (e.g., https://pasta.social/users/chef/recipes/123)
    
    Workflow:
    1. Verify user is authenticated
    2. Get the URL from the form
    3. Call the /api/v1/recipes/fork API endpoint (reuse existing logic)
    4. Redirect to the forked recipe
    
    Args:
        request: FastAPI Request
        db: Database session
        current_user: Authenticated user
    
    Returns:
        RedirectResponse: To the forked recipe
    """
    
    # Check authentication
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Get data from form
    form = await request.form()
    remote_url = form.get("url")
    
    if not remote_url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    # TODO: Call the existing remote fork logic
    # For now placeholder - you should import and reuse logic from app/routers/recipes.py
    # or extract the logic into a service layer
    
    raise HTTPException(status_code=501, detail="Remote fork not yet implemented in HTML form")
