from celery_app import celery_app
import requests
import os

@celery_app.task
def collect_metric_evidence(run_id, hcp_id, metric_id):
    CENTRAL = os.environ.get("TELCOEVALUATOR_CENTRAL", "http://127.0.0.1:5000")
    sources = requests.get(f"{CENTRAL}/api/sources", timeout=5).json()["sources"]

    items=[]
    for s in sources:
        source_id = int(s["id"])
        base_url = s["base_url"].rstrip("/")
        
        try:
            r = requests.post(
                f"{base_url}/evidence",
                json={"evaluation_run_id": run_id, "hcp_id": hcp_id, "metric_id": metric_id},
                timeout=5
            )
            r.raise_for_status()
            data = r.json()
            items.append({
                "source_id": source_id,
                "observed_outcome_label": data["observed_outcome_label"],
                "raw": data.get("raw")
            })
        except Exception:
            continue

    if not items:
        return
    requests.post(
        f"{CENTRAL}/api/evidence_batch",
        json={
            "evaluation_run_id": run_id,
            "hcp_id": hcp_id,
            "metric_id": metric_id,
            "items": items
        },
        timeout=10
    )