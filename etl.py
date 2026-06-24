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

# ── 1. Extract ───────────────────────────────────────────────────────────────
def extract(filepath):
    print("Extracting data from CSV...")
    df = pd.read_csv(filepath)
    print(f"  Loaded {len(df):,} rows, {len(df.columns)} columns")
    return df

# ── 2. Transform ─────────────────────────────────────────────────────────────
def transform(df):
    print("Transforming data...")

    # Rename columns to match our staging table
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

    # Drop any rows where critical fields are null
    before = len(df)
    df = df.dropna(subset=["amount", "name_orig", "name_dest"])
    print(f"  Dropped {before - len(df):,} rows with nulls")

    # Ensure correct data types
    df["amount"]          = pd.to_numeric(df["amount"],          errors="coerce")
    df["old_balance_org"] = pd.to_numeric(df["old_balance_org"], errors="coerce")
    df["new_balance_org"] = pd.to_numeric(df["new_balance_org"], errors="coerce")
    df["old_balance_dst"] = pd.to_numeric(df["old_balance_dst"], errors="coerce")
    df["new_balance_dst"] = pd.to_numeric(df["new_balance_dst"], errors="coerce")
    df["is_fraud"]        = df["is_fraud"].astype(int)
    df["is_flagged_fraud"]= df["is_flagged_fraud"].astype(int)

    # Flag anomalies: transactions > 3 std deviations from the mean amount
    mean   = df["amount"].mean()
    std    = df["amount"].std()
    df["is_anomaly"] = ((df["amount"] - mean) / std).abs() > 3
    anomaly_count = df["is_anomaly"].sum()
    print(f"  Flagged {anomaly_count:,} anomalous transactions")

    print(f"  Transform complete — {len(df):,} rows ready to load")
    return df

# ── 3. Load ──────────────────────────────────────────────────────────────────
def load(df):
    print("Loading data into staging.raw_transactions...")

    columns = [
        "step", "type", "amount",
        "name_orig", "old_balance_org", "new_balance_org",
        "name_dest", "old_balance_dst", "new_balance_dst",
        "is_fraud", "is_flagged_fraud",
    ]

    # Convert dataframe to list of tuples for psycopg2
    rows = [tuple(row) for row in df[columns].itertuples(index=False)]

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn:
            with conn.cursor() as cur:
                # Clear staging table before loading fresh data
                cur.execute("TRUNCATE TABLE staging.raw_transactions;")

                # Batch insert
                execute_values(
                    cur,
                    """
                    INSERT INTO staging.raw_transactions (
                        step, type, amount,
                        name_orig, old_balance_org, new_balance_org,
                        name_dest, old_balance_dst, new_balance_dst,
                        is_fraud, is_flagged_fraud
                    ) VALUES %s
                    """,
                    rows,
                    page_size=1000
                )
                print(f"  Inserted {len(rows):,} rows into staging.raw_transactions")
    finally:
        conn.close()

# ── Run pipeline ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    CSV_PATH = "data/PS_20174392719_1491204439457_log.csv"  

    df = extract(CSV_PATH)
    df = transform(df)
    load(df)

    print("\nETL complete.")