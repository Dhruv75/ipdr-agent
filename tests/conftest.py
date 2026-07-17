import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def sample_df() -> pd.DataFrame:
    """Tiny in-memory dataset so tests never depend on the generated file."""
    rows = [
        ("2025-09-17 08:00:00", "10.0.0.1", "1.1.1.1", "youtube.com",
         "YouTube Streaming", "Streaming_Media", 443, "TCP"),
        ("2025-09-17 09:00:00", "10.0.0.1", "2.2.2.2", "hdfcbank.com",
         "Banking Login", "Banking", 443, "TCP"),
        ("2025-09-17 09:05:00", "10.0.0.1", "3.3.3.3", "shopfast-deals.net",
         "Card Detail Submission", "Fraud_Ecommerce", 443, "TCP"),
        ("2025-09-17 10:00:00", "10.0.0.2", "4.4.4.4", "cdn-analytics-sync.xyz",
         "Beacon Check-in", "C2_Beaconing", 443, "TCP"),
        ("2025-09-17 11:00:00", "10.0.0.2", "5.5.5.5", "cdn-analytics-sync.xyz",
         "Beacon Check-in", "C2_Beaconing", 443, "TCP"),
    ]
    cols = ["timestamp", "source_ip", "destination_ip", "destination_domain",
            "activity", "data_type", "port", "protocol"]
    df = pd.DataFrame(rows, columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["rag_text"] = df.apply(
        lambda r: f"{r.source_ip} -> {r.destination_domain} {r.activity} {r.data_type}",
        axis=1,
    )
    return df
