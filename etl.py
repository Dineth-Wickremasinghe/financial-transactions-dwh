import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
import os

# ── Load environment variables ───────────────────────────────────────────────
load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "port":     os.getenv("DB_PORT"),
    "dbname":   os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

# ── Get high-watermark ────────────────────────────────────────────────────────
def get_last_loaded_step(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(last_loaded_step) FROM warehouse.etl_metadata;")
        result = cur.fetchone()
        return result[0] if result[0] is not None else 0
    
# ── Update high-watermark ─────────────────────────────────────────────────────
def update_last_loaded_step(conn, step):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO warehouse.etl_metadata (last_loaded_step) VALUES (%s);",
            (step,)
        )
    conn.commit()
    print(f"  Watermark updated to step {step}")

# ── 1. Extract ────────────────────────────────────────────────────────────────
def extract(filepath, last_step):
    print(f"Extracting rows with step > {last_step}...")
    df = pd.read_csv(filepath)

    # Only load rows newer than the last loaded step
    df = df[df["step"] > last_step]
    print(f"  Found {len(df):,} new rows to load")
    return df

# ── 2. Transform ──────────────────────────────────────────────────────────────
def transform(df):
    if df.empty:
        print("  No new rows to transform — skipping")
        return df

    print("Transforming data...")

    df = df.rename(columns={
        "step":           "step",
        "type":           "type",
        "amount":         "amount",
        "nameOrig":       "name_orig",
        "oldbalanceOrg":  "old_balance_org",
        "newbalanceOrig": "new_balance_org",
        "nameDest":       "name_dest",
        "oldbalanceDest": "old_balance_dst",
        "newbalanceDest": "new_balance_dst",
        "isFraud":        "is_fraud",
        "isFlaggedFraud": "is_flagged_fraud",
    })

    before = len(df)
    df = df.dropna(subset=["amount", "name_orig", "name_dest"])
    print(f"  Dropped {before - len(df):,} rows with nulls")

    df["amount"]           = pd.to_numeric(df["amount"],          errors="coerce")
    df["old_balance_org"]  = pd.to_numeric(df["old_balance_org"], errors="coerce")
    df["new_balance_org"]  = pd.to_numeric(df["new_balance_org"], errors="coerce")
    df["old_balance_dst"]  = pd.to_numeric(df["old_balance_dst"], errors="coerce")
    df["new_balance_dst"]  = pd.to_numeric(df["new_balance_dst"], errors="coerce")
    df["is_fraud"]         = df["is_fraud"].astype(int)
    df["is_flagged_fraud"] = df["is_flagged_fraud"].astype(int)

    mean = df["amount"].mean()
    std  = df["amount"].std()
    df["is_anomaly"] = ((df["amount"] - mean) / std).abs() > 3

    print(f"  Transform complete — {len(df):,} rows ready to load")
    return df

# ── 3. Load ───────────────────────────────────────────────────────────────────
def load(df, conn):
    if df.empty:
        print("  No new rows to load — skipping")
        return

    print("Loading new rows into staging.raw_transactions...")

    columns = [
        "step", "type", "amount",
        "name_orig", "old_balance_org", "new_balance_org",
        "name_dest", "old_balance_dst", "new_balance_dst",
        "is_fraud", "is_flagged_fraud",
    ]

    rows = [tuple(row) for row in df[columns].itertuples(index=False)]

    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO staging.raw_transactions (
                    step, type, amount,
                    name_orig, old_balance_org, new_balance_org,
                    name_dest, old_balance_dst, new_balance_dst,
                    is_fraud, is_flagged_fraud
                ) VALUES %s
                ON CONFLICT DO NOTHING
                """,
                rows,
                page_size=1000
            )
            print(f"  Inserted {len(rows):,} new rows into staging.raw_transactions")

# ── Run pipeline ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    CSV_PATH = "data/PS_20174392719_1491204439457_log.csv"

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        # Get watermark
        last_step = get_last_loaded_step(conn)
        print(f"Last loaded step: {last_step}")

        # Run pipeline
        df = extract(CSV_PATH, last_step)
        df = transform(df)
        load(df, conn)

        # Update watermark only if new rows were loaded
        if not df.empty:
            new_watermark = int(df["step"].max())
            update_last_loaded_step(conn, new_watermark)

    finally:
        conn.close()

    print("\nETL complete.")