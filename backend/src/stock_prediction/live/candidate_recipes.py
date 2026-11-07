"""Finite, preregistered candidate-development recipes.

Candidate recipes are deliberately versioned and finite.  A completed recipe is
never silently rerun against the same development window; a later round must be
registered here with a materially different candidate matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CANDIDATE_RECIPE_V1 = "candidate-recipe-v1"
CANDIDATE_RECIPE_V2 = "candidate-recipe-v2"
CANDIDATE_RECIPE_UNIVERSE204_V1 = "candidate-recipe-universe204-v1"
CANDIDATE_RECIPE_SEED100_TARGETED_V4 = "candidate-recipe-seed100-targeted-v4"
CANDIDATE_RECIPE_SEED100_TARGETED_V5 = "candidate-recipe-seed100-targeted-v5"
CANDIDATE_RECIPE_SEED100_TARGETED_V6 = "candidate-recipe-seed100-targeted-v6"
CANDIDATE_RECIPE_SEED100_TARGETED_V7 = "candidate-recipe-seed100-targeted-v7"
CANDIDATE_RECIPE_SEED100_TARGETED_V8 = "candidate-recipe-seed100-targeted-v8"
CANDIDATE_RECIPE_SEED100_TARGETED_V9 = "candidate-recipe-seed100-targeted-v9"
CANDIDATE_RECIPE_SEED100_TARGETED_V10 = "candidate-recipe-seed100-targeted-v10"


@dataclass(frozen=True, slots=True)
class CandidateRecipe:
    version: str
    generation: int
    targeted_only: bool = False


_RECIPES = (
    CandidateRecipe(version=CANDIDATE_RECIPE_V1, generation=1),
    CandidateRecipe(version=CANDIDATE_RECIPE_V2, generation=2),
    CandidateRecipe(version=CANDIDATE_RECIPE_UNIVERSE204_V1, generation=3),
)
_TARGETED_RECIPES = (
    CandidateRecipe(
        version=CANDIDATE_RECIPE_SEED100_TARGETED_V4,
        generation=4,
        targeted_only=True,
    ),
    CandidateRecipe(
        version=CANDIDATE_RECIPE_SEED100_TARGETED_V5,
        generation=5,
        targeted_only=True,
    ),
    CandidateRecipe(
        version=CANDIDATE_RECIPE_SEED100_TARGETED_V6,
        generation=6,
        targeted_only=True,
    ),
    CandidateRecipe(
        version=CANDIDATE_RECIPE_SEED100_TARGETED_V7,
        generation=7,
        targeted_only=True,
    ),
    CandidateRecipe(
        version=CANDIDATE_RECIPE_SEED100_TARGETED_V8,
        generation=8,
        targeted_only=True,
    ),
    CandidateRecipe(
        version=CANDIDATE_RECIPE_SEED100_TARGETED_V9,
        generation=9,
        targeted_only=True,
    ),
    CandidateRecipe(
        version=CANDIDATE_RECIPE_SEED100_TARGETED_V10,
        generation=10,
        targeted_only=True,
    ),
)
_BY_VERSION = {recipe.version: recipe for recipe in (*_RECIPES, *_TARGETED_RECIPES)}


def candidate_recipe(version: str) -> CandidateRecipe:
    try:
        return _BY_VERSION[version]
    except KeyError as error:
        raise ValueError("candidate_recipe_unknown") from error


def report_recipe_version(report: dict[str, Any] | None) -> str | None:
    """Read a report recipe, treating pre-versioned reports as recipe v1."""

    if report is None:
        return None
    value = report.get("candidate_recipe_version")
    if isinstance(value, str) and value in _BY_VERSION:
        return value
    return CANDIDATE_RECIPE_V1


def next_candidate_recipe_version(latest_version: str | None) -> str | None:
    if latest_version is None:
        return CANDIDATE_RECIPE_V1
    for index, recipe in enumerate(_RECIPES):
        if recipe.version != latest_version:
            continue
        next_index = index + 1
        return _RECIPES[next_index].version if next_index < len(_RECIPES) else None
    raise ValueError("candidate_recipe_unknown")


def uses_expanded_direction_search(version: str) -> bool:
    return version in {CANDIDATE_RECIPE_V2, CANDIDATE_RECIPE_UNIVERSE204_V1}


__all__ = [
    "CANDIDATE_RECIPE_SEED100_TARGETED_V4",
    "CANDIDATE_RECIPE_SEED100_TARGETED_V5",
    "CANDIDATE_RECIPE_SEED100_TARGETED_V6",
    "CANDIDATE_RECIPE_SEED100_TARGETED_V7",
    "CANDIDATE_RECIPE_SEED100_TARGETED_V8",
    "CANDIDATE_RECIPE_SEED100_TARGETED_V9",
    "CANDIDATE_RECIPE_SEED100_TARGETED_V10",
    "CANDIDATE_RECIPE_V1",
    "CANDIDATE_RECIPE_V2",
    "CANDIDATE_RECIPE_UNIVERSE204_V1",
    "CandidateRecipe",
    "candidate_recipe",
    "next_candidate_recipe_version",
    "report_recipe_version",
    "uses_expanded_direction_search",
]
