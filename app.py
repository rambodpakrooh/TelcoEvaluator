from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from models import Dimension, Metric, Outcome, HCP, DirichletState, MetricValue, EvaluationRun, Evidence, EvidenceSource
from extensions import db
from helpers import (
    initial_alpha_for_metric, 
    evidence_items_trustworthiness,
    dirichlet_update,
    ema_update,
    validate_ordinal_values,
    metric_expected_and_confidence,
    aggregate_dimension_score,
    aggregate_total_score
)
from tasks import collect_metric_evidence

RHO = 0.1

def create_app():
    app = Flask(__name__)
    
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///telcoevaluator.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    db.init_app(app)
    
    with app.app_context():
        db.create_all()
        
    @app.route("/")
    def home():
        return render_template("index.html")
    
    @app.route("/dimensions", methods = ["GET", "POST"])
    def dimensions():
        if request.method == "POST":
            name = request.form["name"]
            weight = float(request.form["weight"])
            description = request.form.get("description")
            
            dim = Dimension(name=name, weight=weight, description=description)
            db.session.add(dim)
            db.session.commit()
            return redirect(url_for("dimensions"))
        all_dims = Dimension.query.all()
        return render_template("dimensions.html", dimensions=all_dims)
    
    @app.route("/metrics")
    def metrics():
        all_dims = Dimension.query.all()
        return render_template("metrics.html", dimensions=all_dims)
    
    @app.route("/dimensions/<int:dimension_id>/metrics/create", methods=["GET", "POST"])
    def create_metric(dimension_id):
        dim = Dimension.query.get_or_404(dimension_id)
        
        if request.method == "POST":
            name = request.form["name"]
            weight = float(request.form["weight"])
            description = request.form.get("description")
            k = int(request.form["k"])
            if k < 2:
                abort(400, "k must be >= 2")
            metric = Metric(name=name, weight=weight, description=description, k=k, dimension=dim)
            db.session.add(metric)
            db.session.commit()
            return redirect(url_for("metrics"))
        return render_template("create_metric.html", dimension=dim)
    
    @app.route("/metrics/<int:metric_id>/outcomes", methods=["GET","POST"])
    def add_outcomes(metric_id):
        metric = Metric.query.get_or_404(metric_id)
        
        if request.method == "POST" and len(metric.outcomes) > 0:
            abort(400, "Outcomes for this metric are already defined and cannot be modified.")
        
        if request.method == "POST":
            outcomes = []

            for i in range(metric.k):
                label = request.form[f"label_{i}"].strip()
                ordinal_value = float(request.form[f"ordinal_value_{i}"])
                outcomes.append((label, ordinal_value))

            validate_ordinal_values([v for _, v in outcomes])

            for label, ordinal_value in outcomes:
                db.session.add(
                    Outcome(label=label, ordinal_value=ordinal_value, metric=metric)
                )
            db.session.commit()

            return redirect(url_for("add_outcomes", metric_id=metric.id))

        return render_template("outcomes.html", metric=metric)
    
    @app.route("/metrics/<int:metric_id>/edit", methods=["GET", "POST"])
    def edit_metric(metric_id):
        metric = Metric.query.get_or_404(metric_id)
        
        if request.method == "POST":
            metric.name = request.form["name"]
            metric.description = request.form.get("description")
            db.session.commit()
            return redirect(url_for("metrics"))
        return render_template("edit_metric.html", metric=metric)
    
    @app.route("/hcps", methods=["GET", "POST"])
    def hcps():
        if request.method == "POST":
            name = request.form["name"]
            description = request.form.get("description")
            hcp = HCP(name=name, description=description)
            db.session.add(hcp)
            db.session.commit()
            return redirect(url_for("hcps"))
        all_hcps = HCP.query.all()
        return render_template("hcps.html", hcps=all_hcps)
    
    @app.route("/evaluate", methods=["GET", "POST"])
    def evaluate():
        if request.method == "POST":
            run = EvaluationRun(status="initializing")
            db.session.add(run)
            db.session.commit()
            
            hcps = HCP.query.all()
            metrics = Metric.query.all()
            
            for hcp in hcps:
                for metric in metrics:
                    alpha = initial_alpha_for_metric(metric)
                    state = DirichletState(evaluation_run_id=run.id, hcp_id=hcp.id, metric_id=metric.id, alpha=alpha)
                    db.session.add(state)
            run.status="ready"
            db.session.commit()
            for hcp in hcps:
                for metric in metrics:
                    collect_metric_evidence.delay(run.id, hcp.id, metric.id)
            run.status="running"
            db.session.commit()
            return redirect(url_for("evaluation_run_detail", run_id=run.id))
        runs = EvaluationRun.query.order_by(EvaluationRun.created_at.desc()).all()
        return render_template("evaluate.html", runs=runs)
    
    @app.route("/evaluation-runs/<int:run_id>")
    def evaluation_run_detail(run_id):
        run = EvaluationRun.query.get_or_404(run_id)
        states = DirichletState.query.filter_by(evaluation_run_id=run_id).all()
        
        all_dimensions = Dimension.query.all()
        dimensions_by_id = {d.id: d for d in all_dimensions}
        
        all_hcps = HCP.query.all()
        hcps_by_id = {h.id: h for h in all_hcps}
        
        metric_values = []

        for state in states:
            hcp_obj = hcps_by_id[state.hcp_id]
            
            sorted_outcomes = sorted(state.metric.outcomes, key=lambda o: o.ordinal_value)
            ordinal_values = [o.ordinal_value for o in sorted_outcomes]

            expected, conf = metric_expected_and_confidence(state.alpha, ordinal_values)

            mv = MetricValue(
                evaluation_run_id=run_id,
                hcp_id=state.hcp_id,
                metric_id=state.metric_id,
                expected_value=expected,
                confidence=conf
            )
            mv.hcp = hcp_obj
            mv.metric = state.metric
            metric_values.append(mv)

        raw_dim_scores = aggregate_dimension_score(metric_values)
        dimension_scores = {}
        for (h_id, d_id), score in raw_dim_scores.items():
            h_obj = hcps_by_id[h_id]
            d_obj = dimensions_by_id[d_id]
            if h_obj not in dimension_scores:
                dimension_scores[h_obj] = {}
            dimension_scores[h_obj][d_obj] = score

        raw_total_scores = aggregate_total_score(raw_dim_scores, dimensions_by_id)
        total_scores = {}
        for h_id, score in raw_total_scores.items():
            h_obj = hcps_by_id[h_id]
            total_scores[h_obj] = score
        
        return render_template(
            "evaluation_run_detail.html",
            run=run,
            states=states,
            metric_values=metric_values,
            dimension_scores=dimension_scores,
            total_scores=total_scores
        )

    @app.post("/api/evidence_batch")
    def api_evidence_batch():
        payload = request.get_json(force=True)
        run_id = int(payload["evaluation_run_id"])
        hcp_id = int(payload["hcp_id"])
        metric_id = int(payload["metric_id"])
        items = payload["items"] # list of dictionaries {source_id, observed_outcome_label, raw (optional)}

        metric = Metric.query.get_or_404(metric_id)
        outcomes = Outcome.query.filter_by(metric_id=metric_id).order_by(Outcome.ordinal_value).all()
        if len(outcomes) != metric.k:
            abort(500, f"Metric {metric.name} has k={metric.k} but {len(outcomes)} outcomes defined")
        label_to_index = {o.label:idx for idx, o in enumerate(outcomes)}
        for itm in items:
            label = itm["observed_outcome_label"]
            if label not in label_to_index:
                abort(400, f"Unknown outcome label {label} for metric {metric.name}")
            itm["observed_outcome_index"] = label_to_index[label]

        source_ids = [int(itm["source_id"]) for itm in items]
        sources = EvidenceSource.query.filter(EvidenceSource.id.in_(source_ids)).all()
        sources_by_id = {s.id: s for s in sources}
        missing = [sid for sid in source_ids if sid not in sources_by_id]
        if missing:
            abort(400, f"Unknown source_id(s): {missing}")
        t_es = evidence_items_trustworthiness(items, sources_by_id)

        state = DirichletState.query.filter_by(evaluation_run_id=run_id, hcp_id=hcp_id, metric_id=metric_id).first_or_404()
        for itm, t_e in zip(items, t_es):
            outcome_idx = int(itm["observed_outcome_index"])
            raw = itm.get("raw")
            ev = Evidence(
                evaluation_run_id=run_id,
                hcp_id=hcp_id,
                metric_id=metric_id,
                source_id=int(itm["source_id"]),
                observed_outcome_index=outcome_idx,
                t_e=float(t_e),
                raw=raw
            )
            db.session.add(ev)
            state.alpha = dirichlet_update(state.alpha, outcome_idx, t_e)

            src = sources_by_id[int(itm["source_id"])]
            if not src.accredited:
                src.trust_value = ema_update(src.trust_value, t_e, RHO)

        db.session.commit()
        return jsonify({
            "ok": True,
            "new_alpha": state.alpha,
            "t_es": t_es
        })

    @app.get("/api/sources")
    def api_sources():
        srcs = EvidenceSource.query.all()
        return jsonify({
            "sources": [
                {
                    "id": s.id,
                    "name": s.name,
                    "base_url": s.base_url,
                    "accredited": s.accredited,
                    "trust_value": s.trust_value
                } for s in srcs
            ]
        })
     
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
    
