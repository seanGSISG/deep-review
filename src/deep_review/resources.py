"""Files that ship inside the wheel."""

from importlib import resources
from pathlib import Path

BASE_SHA_PLACEHOLDER = "{{BASE_SHA}}"


def review_prompt(base_sha: str) -> str:
    """The prompt the Reviewer runs, with the Base SHA substituted into it."""
    return _prompt_source().replace(BASE_SHA_PLACEHOLDER, base_sha)


def _prompt_source() -> str:
    """
    prompts/review.md at the repo root is the single source: installed wheels carry it as package
    data (the force-include in pyproject.toml), and a source checkout reads it where it lives.
    """
    packaged = resources.files("deep_review").joinpath("_data/review.md")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    return (Path(__file__).resolve().parents[2] / "prompts" / "review.md").read_text(
        encoding="utf-8"
    )


def skill_dir() -> Path:
    """
    The Skill's directory, skills/pre-pr-review at the repo root: the wheel carries it as package
    data and a source checkout reads it where it lives. It is a real directory either way, because
    install-skill symlinks to it and a loader follows the link to SKILL.md.
    """
    packaged = resources.files("deep_review").joinpath("_data/skills/pre-pr-review")
    if packaged.is_dir():
        return Path(str(packaged))
    return Path(__file__).resolve().parents[2] / "skills" / "pre-pr-review"
