import json
from pathlib import Path

_DIR = Path(__file__).parent


def load_dataset(name: str) -> list[dict]:
    """
    Load evaluation items from evals/datasets/{name}.json.

    Returns the top-level 'items' list as a plain list[dict].
    Every JSON dataset file must have a top-level 'items' key.

    Usage:
        from evals.datasets import load_dataset
        cases = load_dataset("retrieval")
    """
    path = _DIR / f"{name}.json"
    if not path.exists():
        available = [p.stem for p in _DIR.glob("*.json")]
        raise FileNotFoundError(
            f"Dataset '{name}' not found. Available: {available}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if "items" not in data:
        raise KeyError(f"Dataset '{name}' has no top-level 'items' key.")
    return data["items"]
