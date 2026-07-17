"""Few-shot examples for NL-to-SQL.

Curated (question, sql) pairs that ground the model on this specific schema and
DuckDB dialect. They are the single biggest lever for reducing hallucinated
columns and malformed queries on complex requests.

The list is also reused by the offline heuristic generator as canned templates,
and by the evaluation harness as part of the golden set.
"""
from __future__ import annotations

FEW_SHOT_EXAMPLES: list[dict[str, str]] = [
    {
        "question": "Top 10 domains accessed by IP 10.22.45.61",
        "sql": (
            "SELECT destination_domain, COUNT(*) AS hits\n"
            "FROM ipdr_logs\n"
            "WHERE source_ip = '10.22.45.61'\n"
            "GROUP BY destination_domain\n"
            "ORDER BY hits DESC\n"
            "LIMIT 10;"
        ),
    },
    {
        "question": "Show the hourly activity trend on 2025-09-17",
        "sql": (
            "SELECT DATE_TRUNC('hour', timestamp) AS hour, COUNT(*) AS events\n"
            "FROM ipdr_logs\n"
            "WHERE CAST(timestamp AS DATE) = '2025-09-17'\n"
            "GROUP BY hour\n"
            "ORDER BY hour\n"
            "LIMIT 100;"
        ),
    },
    {
        "question": "Distribution of threat categories",
        "sql": (
            "SELECT data_type, COUNT(*) AS events\n"
            "FROM ipdr_logs\n"
            "GROUP BY data_type\n"
            "ORDER BY events DESC\n"
            "LIMIT 100;"
        ),
    },
    {
        "question": "List all Fraud_Ecommerce events",
        "sql": (
            "SELECT timestamp, source_ip, destination_domain, activity\n"
            "FROM ipdr_logs\n"
            "WHERE data_type = 'Fraud_Ecommerce'\n"
            "ORDER BY timestamp\n"
            "LIMIT 100;"
        ),
    },
    {
        "question": "Compare TCP vs UDP traffic",
        "sql": (
            "SELECT protocol, COUNT(*) AS events\n"
            "FROM ipdr_logs\n"
            "GROUP BY protocol\n"
            "ORDER BY events DESC\n"
            "LIMIT 100;"
        ),
    },
    {
        "question": "Which source IPs generated the most events overall?",
        "sql": (
            "SELECT source_ip, COUNT(*) AS events\n"
            "FROM ipdr_logs\n"
            "GROUP BY source_ip\n"
            "ORDER BY events DESC\n"
            "LIMIT 10;"
        ),
    },
    {
        "question": "First activity of IP 10.22.45.77",
        "sql": (
            "SELECT timestamp, destination_domain, activity, data_type\n"
            "FROM ipdr_logs\n"
            "WHERE source_ip = '10.22.45.77'\n"
            "ORDER BY timestamp ASC\n"
            "LIMIT 1;"
        ),
    },
    {
        "question": "How many distinct domains did each user visit?",
        "sql": (
            "SELECT source_ip, COUNT(DISTINCT destination_domain) AS distinct_domains\n"
            "FROM ipdr_logs\n"
            "GROUP BY source_ip\n"
            "ORDER BY distinct_domains DESC\n"
            "LIMIT 100;"
        ),
    },
]


def render_few_shot(examples: list[dict[str, str]] | None = None) -> str:
    examples = examples or FEW_SHOT_EXAMPLES
    blocks = []
    for ex in examples:
        blocks.append(f"Q: {ex['question']}\nSQL:\n{ex['sql']}")
    return "\n\n".join(blocks)
