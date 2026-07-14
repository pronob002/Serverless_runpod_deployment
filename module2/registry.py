"""
Model registry — the single place a cloning model is wired into Module 2.

Adding a new model is a two-file change and nothing else:
  1. write `adapters/<name>.py` with a `CloningAdapter` subclass, and
  2. add one line to `_REGISTRY` below.
`runner.py`, `reference.py`, and `run.py` never mention a specific model, so they stay untouched.

Entries are `"registry_key": "import.path:ClassName"` strings and are imported lazily, so listing
the available models (`available()`) never imports torch / voxcpm.
"""

from importlib import import_module

from .adapters.base import CloningAdapter

# key -> "module_path:ClassName"
_REGISTRY: dict[str, str] = {
    "voxcpm": "module2.adapters.voxcpm:VoxCPMAdapter",
    # Add future models here, e.g.:
    # "dots_tts": "module2.adapters.dots_tts:DotsTtsAdapter",
}


def available() -> list[str]:
    """Registry keys of every model that can be run. Does not import the heavy model code."""
    return sorted(_REGISTRY)


def get_adapter(name: str, **kwargs) -> CloningAdapter:
    """Instantiate (but do not load) the adapter registered under `name`."""
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown model '{name}'. Available: {', '.join(available()) or '(none)'}."
        )
    module_path, class_name = _REGISTRY[name].split(":")
    adapter_cls = getattr(import_module(module_path), class_name)
    return adapter_cls(**kwargs)
