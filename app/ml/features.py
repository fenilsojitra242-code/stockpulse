"""
Machine Learning feature helpers and label generation for dynamic multi-horizon targets.
"""
from app.services.feature_engine import (
    compute_market_features,
    create_dynamic_features_vector,
    FEATURE_NAMES,
    compute_supertrend_reversal_matrix,
    extract_supertrend_reversal_feature_vector,
    SUPERTREND_REVERSAL_FEATURE_NAMES
)

__all__ = [
    "compute_market_features",
    "create_dynamic_features_vector",
    "FEATURE_NAMES",
    "compute_supertrend_reversal_matrix",
    "extract_supertrend_reversal_feature_vector",
    "SUPERTREND_REVERSAL_FEATURE_NAMES"
]

