# app/services/unit_converter.py
"""
Service for converting ingredient units to grams.
Works with your existing ingredientunittype enum.
"""
from typing import Optional
from decimal import Decimal


# Base conversions (approximate)
CONVERSIONS = {
    # Weight units
    "g": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "oz": 28.35,
    "lb": 453.59,
    
    # Volume units (approximate for water density)
    "ml": 1.0,
    "l": 1000.0,
    "dl": 100.0,
    "cl": 10.0,
    
    # Spoons
    "tsp": 5.0,
    "teaspoon": 5.0,
    "cucchiaino": 5.0,
    "tbsp": 15.0,
    "tablespoon": 15.0,
    "cucchiaio": 15.0,
    
    # Cups
    "cup": 240.0,
    "tazza": 240.0,
    
    # Italian measures
    "bicchiere": 200.0,
    
    # Empty/pieces (no conversion)
    "": 0.0,  # Your enum has empty string default
    "piece": 0.0,
    "pz": 0.0,
    "pezzo": 0.0,
    "whole": 0.0,
}

# Ingredient-specific densities (g/ml)
INGREDIENT_DENSITIES = {
    "farina": 0.55,
    "flour": 0.55,
    "zucchero": 0.85,
    "sugar": 0.85,
    "sale": 1.2,
    "salt": 1.2,
    "olio": 0.92,
    "oil": 0.92,
    "miele": 1.4,
    "honey": 1.4,
    "burro": 0.96,
    "butter": 0.96,
    "latte": 1.03,
    "milk": 1.03,
    "acqua": 1.0,
    "water": 1.0,
    "panna": 1.01,
    "cream": 1.01,
}


def convert_to_grams(
    quantity: Decimal,
    unit: str,
    ingredient_name: Optional[str] = None
) -> Optional[Decimal]:
    """
    Convert ingredient quantity to grams.
    
    Args:
        quantity: Amount (e.g. 250)
        unit: Unit from ingredientunittype enum
        ingredient_name: Name for density lookup
    
    Returns:
        Quantity in grams, or None if conversion impossible
    
    Examples:
        >>> convert_to_grams(Decimal('250'), 'g', 'flour')
        Decimal('250.0')
        >>> convert_to_grams(Decimal('1'), 'cup', 'flour')
        Decimal('132.0')  # 240ml * 0.55 density
    """
    unit_lower = unit.lower().strip()
    
    # If already in grams
    if unit_lower == "g":
        return quantity
    
    # If unit is empty or piece-based, we can't convert
    if unit_lower in ["", "piece", "pz", "pezzo", "whole", "q.b.", "qb"]:
        return None
    
    # Get base conversion factor
    if unit_lower not in CONVERSIONS:
        # Unknown unit, cannot convert
        return None
    
    base_grams = float(quantity) * CONVERSIONS[unit_lower]
    
    # Apply ingredient-specific density if it's a volume measure
    if ingredient_name and CONVERSIONS[unit_lower] >= 5.0:  # Volume measures
        ingredient_lower = ingredient_name.lower()
        for key, density in INGREDIENT_DENSITIES.items():
            if key in ingredient_lower:
                return Decimal(str(base_grams * density))
    
    return Decimal(str(base_grams))


def can_convert_to_grams(unit: str) -> bool:
    """
    Check if a unit can be converted to grams.
    
    Args:
        unit: Unit string
    
    Returns:
        True if convertible, False otherwise
    """
    unit_lower = unit.lower().strip()
    
    # Empty or piece-based units cannot be converted
    if unit_lower in ["", "piece", "pz", "pezzo", "whole", "q.b.", "qb"]:
        return False
    
    return unit_lower in CONVERSIONS
