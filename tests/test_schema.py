"""Regression: build_catalog must enumerate low-cardinality string columns
regardless of whether pandas represents them as the legacy 'object' dtype or the
newer 'string'/'str' dtype (the pandas 3.x default). This was a real CI failure:
under new pandas the value catalog came back empty and the heuristic SQL
generator fell through to a generic query."""
import pandas as pd

from ipdr_agent.schema import build_catalog


def _df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-09-17 08:00", "2025-09-17 09:00"]),
        "source_ip": ["10.0.0.1", "10.0.0.2"],
        "data_type": ["Banking", "Fraud_Ecommerce"],
        "rag_text": ["a", "b"],
    })


def test_enumerates_object_dtype():
    cat = build_catalog(_df())
    assert "Fraud_Ecommerce" in cat.distinct_values["data_type"]


def test_enumerates_string_dtype():
    df = _df()
    df["data_type"] = df["data_type"].astype("string")   # pandas 3.x-style dtype
    assert df["data_type"].dtype != object                # sanity: not 'object'
    cat = build_catalog(df)
    assert "Fraud_Ecommerce" in cat.distinct_values["data_type"]
