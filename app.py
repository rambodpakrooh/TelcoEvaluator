from flask import Flask, render_template, request, redirect, url_for, jsonify, abort, flash
from models import (
Dimension, Metric, Outcome, HCP, DirichletState, MetricValue, 
EvaluationRun, Evidence, EvidenceSource, SourceTrust,
DimensionScore, TotalScore, SourceTrustLog
)
from extensions import db
from helpers import (
    initial_alpha_for_metric, 
    evidence_items_trustworthiness,
    dirichlet_update,
    ema_update,
    validate_ordinal_values,
    metric_expected_and_confidence,
    aggregate_dimension_scores,
    aggregate_total_scores
)
from tasks import collect_metric_evidence
from datetime import datetime

def create_app():
    app = Flask(__name__)

    app.secret_key = "uW8LVvD1WRJ8xLQnimyYCmy45GxsiIxW"
    
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
            rho = float(request.form.get("rho", 0.1))

            selected_source_ids = request.form.getlist("source_ids")
            if not selected_source_ids:
                flash("You must select at least one evidence source to run an evaluation.", "error")
                return redirect(url_for("evaluate"))
            
            run = EvaluationRun(status="initializing", rho=rho)
            db.session.add(run)
            db.session.commit()
            
            sources = EvidenceSource.query.filter(EvidenceSource.id.in_(selected_source_ids)).all()
            for src in sources:
                db.session.add(
                    SourceTrust(evaluation_run_id=run.id, source_id=src.id, trust=src.trust_value)
                )
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
                    collect_metric_evidence.delay(run.id, hcp.id, hcp.name, metric.id, metric.name)
            
            run.status="running"
            db.session.commit()
            return redirect(url_for("evaluation_run_detail", run_id=run.id))
        
        runs = EvaluationRun.query.order_by(EvaluationRun.created_at.desc()).all()
        all_sources = EvidenceSource.query.all()
        return render_template("evaluate.html", runs=runs, sources=all_sources)
    
    @app.route("/evaluation-runs/<int:run_id>")
    def evaluation_run_detail(run_id):
        run = EvaluationRun.query.get_or_404(run_id)
        states = DirichletState.query.filter_by(evaluation_run_id=run_id).all()

        metric_values = MetricValue.query.filter_by(evaluation_run_id=run_id).all()
        
        d_scores = DimensionScore.query.filter_by(evaluation_run_id=run_id).all()
        dimension_scores = {}
        hcps = {h.id: h for h in HCP.query.all()}
        dims = {d.id: d for d in Dimension.query.all()}

        for ds in d_scores:
            h = hcps[ds.hcp_id]
            d = dims[ds.dimension_id]
            if h not in dimension_scores:
                dimension_scores[h] = {}
            dimension_scores[h][d] = ds.score

        t_scores = TotalScore.query.filter_by(evaluation_run_id=run_id).all()
        total_scores = {}

        for ts in t_scores:
            h = hcps[ts.hcp_id]
            total_scores[h] = ts.score

        logs = SourceTrustLog.query.filter_by(evaluation_run_id=run_id).order_by(SourceTrustLog.updated_at).all()

        chart_data = {}
        for log in logs:
            s_name = log.source.name
            if s_name not in chart_data:
                chart_data[s_name] = [{
                    "x": run.created_at.isoformat(),
                    "y": log.old_trust
                }]

            chart_data[s_name].append({
                "x": log.updated_at.isoformat(),
                "y": log.new_trust
            })
        
        return render_template(
            "evaluation_run_detail.html",
            run=run,
            states=states,
            metric_values=metric_values,
            dimension_scores=dimension_scores,
            total_scores=total_scores,
            chart_data=chart_data
        )

    @app.post("/api/evidence_batch")
    def api_evidence_batch():
        payload = request.get_json(force=True)
        run_id = int(payload["evaluation_run_id"])
        hcp_id = int(payload["hcp_id"])
        metric_id = int(payload["metric_id"])
        items = payload["items"] # list of dictionaries {source_id, observed_outcome_label, raw (optional)}

        run = EvaluationRun.query.get_or_404(run_id)
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
        source_trusts = SourceTrust.query.filter_by(evaluation_run_id=run_id).filter(SourceTrust.source_id.in_(source_ids)).all()
        trust_by_source_id = {st.source_id: st for st in source_trusts}
        sources_by_id = {st.source_id: st.source for st in source_trusts}
        missing = [sid for sid in source_ids if sid not in sources_by_id]
        if missing:
            abort(400, f"Unknown source_id(s): {missing}")
        
        t_es = evidence_items_trustworthiness(items, sources_by_id, trust_by_source_id)

        state = DirichletState.query.filter_by(evaluation_run_id=run_id, hcp_id=hcp_id, metric_id=metric_id).first_or_404()
        
        batchtime = datetime.utcnow()
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

            sid = int(itm["source_id"])
            src = sources_by_id[sid]
            st = trust_by_source_id[sid]
            if not src.accredited:
                old_val = st.trust
                new_val = ema_update(st.trust, t_e, run.rho)

                log_entry = SourceTrustLog(
                    evaluation_run_id=run_id, source_id=sid,
                    old_trust=old_val, new_trust=new_val, updated_at=batchtime
                )
                db.session.add(log_entry)
                st.trust = new_val

        db.session.commit()

        sorted_outcomes = sorted(metric.outcomes, key=lambda o: o.ordinal_value)
        ordinal_values = [o.ordinal_value for o in sorted_outcomes]

        authoritative_ev = Evidence.query.join(EvidenceSource).filter(
            Evidence.evaluation_run_id == run_id, Evidence.hcp_id == hcp_id,
            Evidence.metric_id == metric_id, EvidenceSource.accredited.is_(True)
        ).first()

        if authoritative_ev:
            outcome_idx = authoritative_ev.observed_outcome_index
            expected = ordinal_values[outcome_idx]
            conf = 1.0
        else:
            expected, conf = metric_expected_and_confidence(state.alpha, ordinal_values)

        mv = MetricValue(
            evaluation_run_id=run_id, hcp_id=hcp_id, metric_id=metric_id,
            expected_value=expected, confidence=conf
        )
        db.session.add(mv)
        db.session.commit()

        dim_metrics_count = Metric.query.filter_by(dimension_id=metric.dimension_id).count()

        processed_metrics_count = MetricValue.query.join(Metric).filter(
            MetricValue.evaluation_run_id == run_id,
            MetricValue.hcp_id == hcp_id,
            Metric.dimension_id == metric.dimension_id
        ).count()

        if processed_metrics_count == dim_metrics_count:
            mvs = MetricValue.query.join(Metric).filter(
                MetricValue.evaluation_run_id == run_id,
                MetricValue.hcp_id == hcp_id,
                Metric.dimension_id == metric.dimension_id
            ).all()

            dim_scores_map = aggregate_dimension_scores(mvs)
            score_val = dim_scores_map[(hcp_id, metric.dimension_id)]

            ds = DimensionScore(
                evaluation_run_id=run_id, hcp_id=hcp_id,
                dimension_id=metric.dimension_id, score=score_val
            )
            db.session.add(ds)
            db.session.commit()

            total_dims_count = Dimension.query.count()
            processed_dims_count = DimensionScore.query.filter_by(
                evaluation_run_id=run_id, hcp_id=hcp_id
            ).count()

            if processed_dims_count == total_dims_count:
                d_scores = DimensionScore.query.filter_by(
                    evaluation_run_id=run_id, hcp_id=hcp_id
                ).all()

                ds_map = {(d.hcp_id, d.dimension_id): d.score for d in d_scores}

                all_dims = Dimension.query.all()
                dims_by_id = {d.id: d for d in all_dims}

                total_map = aggregate_total_scores(ds_map, dims_by_id)
                t_score_val = total_map[hcp_id]

                ts = TotalScore(evaluation_run_id=run_id, hcp_id=hcp_id, score=t_score_val)
                db.session.add(ts)
                db.session.commit()

                total_hcps = HCP.query.count()
                processed_hcps = TotalScore.query.filter_by(evaluation_run_id=run_id).count()

                if processed_hcps == total_hcps:
                    run.status = "completed"
                    db.session.commit()
        
        return jsonify({
            "ok": True
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
                    "initial_trust_value": s.trust_value
                } for s in srcs
            ]
        })

    @app.get("/api/runs/<int:run_id>/sources")
    def api_run_sources(run_id):
        trusts = SourceTrust.query.filter_by(evaluation_run_id=run_id).all()
        sources = [t.source for t in trusts]
        return jsonify({
            "sources": [
                {
                    "id": s.id,
                    "name": s.name,
                    "base_url": s.base_url,
                    "accredited": s.accredited,
                    "initial_trust_value": s.trust_value
                } for s in sources
            ]
        })

    @app.route("/evidence-sources", methods=["GET", "POST"])
    def evidence_sources():
        if request.method == "POST":
            name = request.form["name"]
            base_url = request.form["base_url"]
            accredited = (request.form.get("accredited") == "on")
            trust_value = 1.0 if accredited else 0.5
            if request.form.get("trust_value"):
                trust_value = float(request.form["trust_value"])

            source = EvidenceSource(
                name=name,
                base_url=base_url,
                accredited=accredited,
                trust_value=trust_value
            )
            db.session.add(source)
            db.session.commit()
            return redirect(url_for("evidence_sources"))
        sources = EvidenceSource.query.all()
        return render_template("evidence_sources.html", sources=sources)
     
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
    
