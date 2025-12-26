from flask import Flask, request, jsonify
import sqlite3
from datetime import datetime

DB_path = "source.db"

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect(DB_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS evidence(
            hcp_id INTEGER NOT NULL,
            metric_id INTEGER NOT NULL,
            observed_outcome_label TEXT NOT NULL,
            PRIMARY KEY (hcp_id, metric_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS served_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            served_at TEXT NOT NULL,
            evaluation_run_id INTEGER NOT NULL,
            hcp_id INTEGER NOT NULL,
            metric_id INTEGER NOT NULL,
            observed_outcome_label TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.post("/evidence")
def evidence():
    data = request.get_json(force=True)
    run_id = int(data["evaluation_run_id"])
    hcp_id = int(data["hcp_id"])
    metric_id = int(data["metric_id"])

    conn = get_db()
    cur = conn.cursor()

    row = cur.execute("SELECT observed_outcome_label FROM evidence WHERE hcp_id=? AND metric_id=?", (hcp_id, metric_id)).fetchone()
    
    if row is None:
        conn.close()
        return jsonify({"error": "no evidence available for this (hcp_id, metric_id}"}), 404
    
    outcome_label = row["observed_outcome_label"]
    served_at = datetime.utcnow().isoformat()

    cur.execute("INSERT INTO served_requests(served_at, evaluation_run_id, hcp_id, metric_id, observed_outcome_label) VALUES (?, ?, ?, ?, ?)", 
               (served_at, run_id, hcp_id, metric_id, outcome_label))
    
    conn.commit()
    conn.close()

    return jsonify({
        "observed_outcome_label": outcome_label,
        "raw": {
            "served_at": served_at,
            "source_db": DB_path
        }
    })

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)