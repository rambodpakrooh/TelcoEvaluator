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
        CREATE TABLE IF NOT EXISTS served_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            served_at TEXT NOT NULL,
            evaluation_run_id INTEGER NOT NULL,
            hcp_name TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            observed_outcome_label TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.post("/evidence")
def evidence():
    data = request.get_json(force=True)
    run_id = int(data["evaluation_run_id"])
    hcp_name = data["hcp_name"].strip()
    metric_name = data["metric_name"].strip()

    conn = get_db()
    cur = conn.cursor()

    row = cur.execute("SELECT observed_outcome_label FROM evidence WHERE hcp_name=? AND metric_name=?", (hcp_name, metric_name)).fetchone()
    
    if row is None:
        conn.close()
        return jsonify({"error": f"no evidence available for hcp_name \"{hcp_name}\" and metric_name \"{metric_name}\""}), 404
    
    outcome_label = row["observed_outcome_label"]
    served_at = datetime.utcnow().isoformat()

    cur.execute("INSERT INTO served_requests(served_at, evaluation_run_id, hcp_name, metric_name, observed_outcome_label) VALUES (?, ?, ?, ?, ?)", 
               (served_at, run_id, hcp_name, metric_name, outcome_label))
    
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