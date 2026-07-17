"""
Synthetic IPDR (IP Detail Record) dataset generator.

Produces realistic network forensic logs for the IPDR Forensic Agent.
The data is fully synthetic and contains a handful of deliberately planted
"threat scenarios" so that the analytical queries the app supports actually
have something interesting to find.

Design goals:
  * Deterministic (seeded) so results are reproducible in CI / demos.
  * Schema matches what the engine expects:
        timestamp, source_ip, destination_ip, destination_domain,
        activity, data_type, port, protocol, rag_text
  * Embeds 3 investigable patterns:
        1. E-commerce fraud   (banking login -> spoofed shop -> card entry)
        2. C2 beaconing       (regular fixed-interval calls to a rare domain)
        3. Data exfiltration   (large sustained upload to file-sharing host)

Usage:
    python scripts/generate_data.py --rows 5000 --out data/rag_formatted_data.xlsx
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42

# --- Actor population -------------------------------------------------------
# A small set of "users" (internal IPs). A few are cast as threat actors so the
# planted scenarios are attributable to specific source IPs.
NORMAL_USERS = [f"10.22.45.{i}" for i in range(10, 45)]
FRAUD_VICTIM = "10.22.45.61"      # falls for the e-commerce fraud
C2_HOST = "10.22.45.77"           # infected machine beaconing to C2
EXFIL_HOST = "10.22.45.88"        # insider / compromised host exfiltrating data
ALL_USERS = NORMAL_USERS + [FRAUD_VICTIM, C2_HOST, EXFIL_HOST]

# --- Benign browsing profile ------------------------------------------------
BENIGN_DOMAINS = {
    "youtube.com": ("YouTube Streaming", "Streaming_Media"),
    "google.com": ("Web Search", "Web_Browsing"),
    "wikipedia.org": ("Article Read", "Web_Browsing"),
    "github.com": ("Code Repository", "Developer_Activity"),
    "stackoverflow.com": ("Q&A Browse", "Developer_Activity"),
    "linkedin.com": ("Professional Network", "Social_Media"),
    "instagram.com": ("Social Feed", "Social_Media"),
    "whatsapp.com": ("WhatsApp Call", "Messaging"),
    "netflix.com": ("Video Streaming", "Streaming_Media"),
    "hdfcbank.com": ("Banking Login", "Banking"),
    "icicibank.com": ("Banking Login", "Banking"),
    "amazon.in": ("Online Shopping", "Ecommerce"),
    "flipkart.com": ("Online Shopping", "Ecommerce"),
    "gmail.com": ("Email Access", "Email"),
    "outlook.com": ("Email Access", "Email"),
    "zoom.us": ("Video Conference", "Collaboration"),
    "dns.google": ("DNS Lookup", "Infrastructure"),
}

# Malicious / suspicious infrastructure used by the planted scenarios.
SPOOFED_SHOP = "shopfast-deals.net"
C2_DOMAIN = "cdn-analytics-sync.xyz"
EXFIL_DOMAIN = "megafileupload.io"

PORT_BY_PROTOCOL = {
    "TCP": [443, 443, 443, 80, 8080],
    "UDP": [443, 53, 123],
}


def _rand_dest_ip(rng: random.Random) -> str:
    return f"{rng.randint(20, 210)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def _make_rag_text(row: dict) -> str:
    """Human-readable, embedding-friendly summary of the record."""
    return (
        f"At {row['timestamp']}, host {row['source_ip']} connected to "
        f"{row['destination_domain']} ({row['destination_ip']}) over "
        f"{row['protocol']}/{row['port']} performing '{row['activity']}'. "
        f"Classified as {row['data_type']}."
    )


def _base_row(rng: random.Random, ts: datetime, src: str, domain: str,
              activity: str, data_type: str) -> dict:
    protocol = "UDP" if domain in ("whatsapp.com", "zoom.us", "dns.google") else "TCP"
    port = rng.choice(PORT_BY_PROTOCOL[protocol])
    return {
        "timestamp": ts,
        "source_ip": src,
        "destination_ip": _rand_dest_ip(rng),
        "destination_domain": domain,
        "activity": activity,
        "data_type": data_type,
        "port": port,
        "protocol": protocol,
    }


def generate_benign(rng: random.Random, n: int, start: datetime, days: int) -> list[dict]:
    rows = []
    domains = list(BENIGN_DOMAINS.keys())
    # Weight domains so a few are dominant (realistic long-tail).
    weights = np.linspace(3.0, 1.0, len(domains))
    weights = weights / weights.sum()
    for _ in range(n):
        src = rng.choice(ALL_USERS)
        domain = np.random.choice(domains, p=weights)
        activity, data_type = BENIGN_DOMAINS[domain]
        # Diurnal pattern: more activity during working hours.
        day_offset = rng.randint(0, days - 1)
        hour = int(np.clip(np.random.normal(14, 4), 0, 23))
        minute, second = rng.randint(0, 59), rng.randint(0, 59)
        ts = start + timedelta(days=day_offset, hours=hour, minutes=minute, seconds=second)
        rows.append(_base_row(rng, ts, src, domain, activity, data_type))
    return rows


def generate_fraud_scenario(rng: random.Random, start: datetime, days: int) -> list[dict]:
    """Victim logs into bank, then is lured to a spoofed shop and enters card details."""
    rows = []
    for _ in range(12):  # repeated over the window so it is discoverable
        day_offset = rng.randint(0, days - 1)
        base = start + timedelta(days=day_offset, hours=rng.randint(19, 23),
                                 minutes=rng.randint(0, 59))
        rows.append(_base_row(rng, base, FRAUD_VICTIM, "hdfcbank.com",
                              "Banking Login", "Banking"))
        rows.append(_base_row(rng, base + timedelta(minutes=rng.randint(2, 8)),
                              FRAUD_VICTIM, SPOOFED_SHOP,
                              "Fake Deal Landing", "Fraud_Ecommerce"))
        rows.append(_base_row(rng, base + timedelta(minutes=rng.randint(9, 15)),
                              FRAUD_VICTIM, SPOOFED_SHOP,
                              "Card Detail Submission", "Fraud_Ecommerce"))
    return rows


def generate_beaconing_scenario(rng: random.Random, start: datetime, days: int) -> list[dict]:
    """C2 beaconing: near-fixed 30-min interval callbacks to a rare domain."""
    rows = []
    t = start + timedelta(hours=rng.randint(0, 6))
    end = start + timedelta(days=days)
    while t < end:
        jitter = rng.randint(-90, 90)  # seconds of jitter around a 30-min beacon
        ts = t + timedelta(seconds=jitter)
        row = _base_row(rng, ts, C2_HOST, C2_DOMAIN, "Beacon Check-in", "C2_Beaconing")
        row["port"], row["protocol"] = 443, "TCP"
        rows.append(row)
        t += timedelta(minutes=30)
    return rows


def generate_exfil_scenario(rng: random.Random, start: datetime, days: int) -> list[dict]:
    """Sustained large upload burst to a file-sharing host over ~2 hours one night."""
    rows = []
    day_offset = rng.randint(1, days - 1)
    burst_start = start + timedelta(days=day_offset, hours=2)  # 2am, off-hours
    for i in range(60):  # many chunked uploads
        ts = burst_start + timedelta(minutes=i * 2, seconds=rng.randint(0, 59))
        row = _base_row(rng, ts, EXFIL_HOST, EXFIL_DOMAIN,
                        "Large File Upload", "Data_Exfiltration")
        row["port"], row["protocol"] = 443, "TCP"
        rows.append(row)
    return rows


def build_dataframe(rows: int) -> pd.DataFrame:
    rng = random.Random(SEED)
    np.random.seed(SEED)

    start = datetime(2025, 9, 15, 0, 0, 0)
    days = 14

    scenario_rows = (
        generate_fraud_scenario(rng, start, days)
        + generate_beaconing_scenario(rng, start, days)
        + generate_exfil_scenario(rng, start, days)
    )
    benign_needed = max(rows - len(scenario_rows), 0)
    all_rows = generate_benign(rng, benign_needed, start, days) + scenario_rows

    df = pd.DataFrame(all_rows)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["rag_text"] = df.apply(_make_rag_text, axis=1)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["port"] = df["port"].astype(int)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic IPDR forensic data.")
    parser.add_argument("--rows", type=int, default=5000, help="Approximate total rows.")
    parser.add_argument("--out", type=str, default="data/rag_formatted_data.xlsx",
                        help="Output .xlsx path (a .csv sibling is also written).")
    args = parser.parse_args()

    df = build_dataframe(args.rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out, index=False)
    csv_path = out.with_suffix(".csv")
    df.to_csv(csv_path, index=False)

    print(f"Generated {len(df):,} rows")
    print(f"  -> {out}")
    print(f"  -> {csv_path}")
    print("\nThreat category distribution:")
    print(df["data_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
