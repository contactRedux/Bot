"""
models/registry.py — ModelRegistry for artifact save/load.

The ModelRegistry is a lightweight catalog that:
1. Saves model artifacts (weights + metadata) under ``models/artifacts/<model_id>/v<N>/``.
2. Records metadata (training timestamp, feature count, validation metrics) in a JSON index.
3. Exposes ``load_latest(model_id)`` to restore the most recently saved version of any model.
4. Supports tagging specific versions for A/B testing (e.g. ``tag='production'``).

Directory layout
----------------
::

    models/artifacts/
    ├── lstm_forecaster/
    │   ├── v1/
    │   │   ├── weights.pt
    │   │   ├── meta.json
    │   │   └── registry_entry.json
    │   └── v2/
    │       └── ...
    ├── gradient_boosting/
    │   └── v1/
    │       └── ...
    └── registry.json          ← master index of all registered models

Usage
-----
::

    from models.registry import ModelRegistry
    from models.lstm_forecaster import LSTMForecaster

    registry = ModelRegistry(artifacts_dir="models/artifacts")

    # Save a trained model
    version = registry.save(model, metrics={"val_sharpe": 1.2, "val_rmse": 0.003})
    print(f"Saved as version {version}")

    # Load latest version
    model = registry.load_latest("lstm_forecaster", model_class=LSTMForecaster)

    # List all versions
    for entry in registry.list_versions("lstm_forecaster"):
        print(entry)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Type

from models.base import BaseSignalModel

logger = logging.getLogger(__name__)

# Master registry filename
_REGISTRY_FILE = "registry.json"


class ModelRegistry:
    """
    Versioned artifact registry for BaseSignalModel subclasses.

    Parameters
    ----------
    artifacts_dir : str or Path
        Root directory for storing model artifacts.
        Defaults to ``models/artifacts/`` relative to this file.
    """

    def __init__(self, artifacts_dir: str | Path | None = None) -> None:
        if artifacts_dir is None:
            # Default: sibling ``artifacts/`` directory relative to this file
            artifacts_dir = Path(__file__).parent / "artifacts"
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.artifacts_dir / _REGISTRY_FILE
        self._index: dict[str, list[dict[str, Any]]] = self._load_index()

    # ── Save ─────────────────────────────────────────────────────────────────

    def save(
        self,
        model: BaseSignalModel,
        metrics: dict[str, float] | None = None,
        tag: str | None = None,
        description: str = "",
    ) -> int:
        """
        Save a trained model and register it in the index.

        Parameters
        ----------
        model : BaseSignalModel
            Trained model instance.
        metrics : dict, optional
            Evaluation metrics to store alongside the artifact
            (e.g. ``{'val_sharpe': 1.2, 'val_rmse': 0.003}``).
        tag : str, optional
            Human-readable tag (e.g. ``'production'``, ``'candidate'``).
        description : str
            Free-text description of this training run.

        Returns
        -------
        int
            Version number assigned to this artifact.
        """
        model_id = model.model_id
        existing = self._index.get(model_id, [])
        version = len(existing) + 1

        version_dir = self.artifacts_dir / model_id / f"v{version}"
        version_dir.mkdir(parents=True, exist_ok=True)

        # Delegate serialization to the model
        model.save(version_dir)

        entry: dict[str, Any] = {
            "model_id": model_id,
            "version": version,
            "path": str(version_dir),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "input_dim": model.input_dim,
            "metrics": metrics or {},
            "tag": tag,
            "description": description,
        }

        # Write per-version metadata
        (version_dir / "registry_entry.json").write_text(json.dumps(entry, indent=2))

        # Update master index
        if model_id not in self._index:
            self._index[model_id] = []
        self._index[model_id].append(entry)
        self._save_index()

        logger.info(
            "ModelRegistry: saved %s v%d → %s  metrics=%s",
            model_id, version, version_dir, metrics,
        )
        return version

    # ── Load ─────────────────────────────────────────────────────────────────

    def load_latest(
        self,
        model_id: str,
        model_class: Type[BaseSignalModel],
    ) -> BaseSignalModel:
        """
        Load the most recently saved version of a model.

        Parameters
        ----------
        model_id : str
            Stable model identifier (e.g. ``'lstm_forecaster'``).
        model_class : type
            The BaseSignalModel subclass to restore (used to call ``cls.load()``).

        Returns
        -------
        BaseSignalModel
            Restored model instance.

        Raises
        ------
        KeyError
            If model_id has never been saved.
        FileNotFoundError
            If the artifact directory is missing.
        """
        if model_id not in self._index or not self._index[model_id]:
            raise KeyError(f"No saved versions for model_id='{model_id}'")

        latest_entry = self._index[model_id][-1]
        path = Path(latest_entry["path"])
        if not path.exists():
            raise FileNotFoundError(f"Artifact directory not found: {path}")

        model = model_class.load(path)
        logger.info(
            "ModelRegistry: loaded %s v%d from %s",
            model_id, latest_entry["version"], path,
        )
        return model

    def load_version(
        self,
        model_id: str,
        version: int,
        model_class: Type[BaseSignalModel],
    ) -> BaseSignalModel:
        """Load a specific version of a model."""
        if model_id not in self._index:
            raise KeyError(f"No saved versions for model_id='{model_id}'")

        versions = self._index[model_id]
        try:
            entry = next(e for e in versions if e["version"] == version)
        except StopIteration:
            raise KeyError(f"Version {version} not found for model_id='{model_id}'")

        return model_class.load(Path(entry["path"]))

    def load_tagged(
        self,
        model_id: str,
        tag: str,
        model_class: Type[BaseSignalModel],
    ) -> BaseSignalModel:
        """Load the most recent model version with a given tag."""
        if model_id not in self._index:
            raise KeyError(f"No saved versions for model_id='{model_id}'")

        tagged = [e for e in self._index[model_id] if e.get("tag") == tag]
        if not tagged:
            raise KeyError(f"No version with tag='{tag}' for model_id='{model_id}'")

        return model_class.load(Path(tagged[-1]["path"]))

    # ── Listing / metadata ───────────────────────────────────────────────────

    def list_versions(self, model_id: str) -> list[dict[str, Any]]:
        """
        List all saved versions for a model.

        Returns
        -------
        list[dict]
            Registry entries in version order.
        """
        return list(self._index.get(model_id, []))

    def list_all(self) -> dict[str, list[dict[str, Any]]]:
        """Return the complete registry index."""
        return dict(self._index)

    def best_version(
        self,
        model_id: str,
        metric: str,
        higher_is_better: bool = True,
    ) -> dict[str, Any] | None:
        """
        Find the version with the best value for a given metric.

        Parameters
        ----------
        metric : str
            Key in the ``metrics`` dict (e.g. ``'val_sharpe'``).
        higher_is_better : bool
            If True, returns the version with the highest metric value.

        Returns
        -------
        dict or None
            Registry entry for the best version, or None if no versions have
            this metric recorded.
        """
        versions = [
            e for e in self._index.get(model_id, [])
            if metric in e.get("metrics", {})
        ]
        if not versions:
            return None
        return max(versions, key=lambda e: e["metrics"][metric]) if higher_is_better else \
               min(versions, key=lambda e: e["metrics"][metric])

    def tag_version(self, model_id: str, version: int, tag: str) -> None:
        """Attach a tag to an existing version."""
        if model_id not in self._index:
            raise KeyError(f"No saved versions for model_id='{model_id}'")
        for entry in self._index[model_id]:
            if entry["version"] == version:
                entry["tag"] = tag
                self._save_index()
                # Also update per-version file
                path = Path(entry["path"]) / "registry_entry.json"
                if path.exists():
                    path.write_text(json.dumps(entry, indent=2))
                logger.info("Tagged %s v%d as '%s'", model_id, version, tag)
                return
        raise KeyError(f"Version {version} not found for model_id='{model_id}'")

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load_index(self) -> dict[str, list[dict[str, Any]]]:
        if self._registry_path.exists():
            try:
                return json.loads(self._registry_path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load registry index: %s — starting fresh", exc)
        return {}

    def _save_index(self) -> None:
        self._registry_path.write_text(json.dumps(self._index, indent=2))
