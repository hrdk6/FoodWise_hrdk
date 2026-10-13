"""Model registry: version rows in PostgreSQL, artifacts in object storage under tenants/{id}/models/."""

from __future__ import annotations

import io
import platform
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from foodwise.core.storage import storage, tenant_key
from foodwise.db.models import ModelVersion
from foodwise.ml.conformal import Calibrator
from foodwise.ml.features import FeatureSpec
from foodwise.ml.zoo import HAS_LIGHTGBM, ZooModel


@dataclass
class Artifact:
    algorithm: str
    model: ZooModel
    spec: FeatureSpec
    calibrator: Calibrator


def library_versions() -> dict[str, str]:
    versions = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit-learn": sklearn.__version__,
        "scipy": scipy.__version__,
    }
    if HAS_LIGHTGBM:
        import lightgbm

        versions["lightgbm"] = lightgbm.__version__
    return versions


def save_artifact(tenant_id: uuid.UUID, canteen_id: uuid.UUID, version: int, artifact: Artifact) -> str:
    buf = io.BytesIO()
    joblib.dump(artifact, buf, compress=3)
    key = tenant_key(tenant_id, "models", str(canteen_id), f"v{version}.joblib")
    return storage().put(key, buf.getvalue())


_cache: OrderedDict[str, Artifact] = OrderedDict()
_lock = threading.Lock()


def load_artifact(uri: str) -> Artifact:
    with _lock:
        if uri in _cache:
            _cache.move_to_end(uri)
            return _cache[uri]
    artifact = joblib.load(io.BytesIO(storage().get(uri)))
    with _lock:
        _cache[uri] = artifact
        while len(_cache) > 32:
            _cache.popitem(last=False)
    return artifact


def next_version(db: Session, canteen_id: uuid.UUID) -> int:
    current = db.execute(select(func.max(ModelVersion.version)).where(ModelVersion.canteen_id == canteen_id)).scalar()
    return int(current or 0) + 1


def champion(db: Session, canteen_id: uuid.UUID) -> ModelVersion | None:
    return db.execute(
        select(ModelVersion)
        .where(ModelVersion.canteen_id == canteen_id, ModelVersion.stage.in_(["champion", "degraded"]))
        .order_by(ModelVersion.promoted_at.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()


def challenger(db: Session, canteen_id: uuid.UUID) -> ModelVersion | None:
    return db.execute(
        select(ModelVersion)
        .where(ModelVersion.canteen_id == canteen_id, ModelVersion.stage == "challenger")
        .order_by(ModelVersion.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
