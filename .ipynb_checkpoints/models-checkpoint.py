#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Dec  5 16:37:26 2025

@author: rambodpakrooh
"""

from datetime import datetime
from extensions import db
from sqlalchemy.dialects.sqlite import JSON

class Dimension(db.Model):
    __tablename__ = "dimensions"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    weight = db.Column(db.Float, nullable=False, default=1)
    metrics = db.relationship("Metric", back_populates="dimension", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Dimension {self.name}>"
    
class Metric(db.Model):
    __tablename__ = "metrics"
    
    id = db.Column(db.Integer, primary_key=True)
    dimension_id = db.Column(db.Integer, db.ForeignKey("dimensions.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    weight = db.Column(db.Float, nullable=False, default=1)
    k = db.Column(db.Integer, nullable=False)
    dimension = db.relationship("Dimension", back_populates="metrics")
    outcomes = db.relationship("Outcome", back_populates="metric", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Metric {self.name} (dim={self.dimension_id})>"
    
class Outcome(db.Model):
    __tablename__ = "outcomes"
    
    id = db.Column(db.Integer, primary_key=True)
    metric_id = db.Column(db.Integer, db.ForeignKey("metrics.id"), nullable=False)
    label = db.Column(db.String(128), nullable=False)
    ordinal_value = db.Column(db.Float, nullable=False)
    metric = db.relationship("Metric", back_populates="outcomes")
    
    def __repr__(self):
        return f"<Outcome {self.label} (ordinal value = {self.ordinal_value})>"
    
class HCP(db.Model):
    __tablename__ = "hcps"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    
    def __repr__(self):
        return f"<HCP {self.name}>"

class DirichletState(db.Model):
    __tablename__ = "dirichlet_states"
    
    id = db.Column(db.Integer, primary_key=True)
    evaluation_run_id = db.Column(db.Integer, db.ForeignKey("evaluation_runs.id"), nullable=False)
    hcp_id = db.Column(db.Integer, db.ForeignKey("hcps.id"), nullable=False)
    metric_id = db.Column(db.Integer, db.ForeignKey("metrics.id"), nullable=False)
    alpha = db.Column(JSON, nullable=False)
    evaluation_run = db.relationship("EvaluationRun", back_populates="dirichlet_states")
    
    __table_args__ = (
        db.UniqueConstraint("evaluation_run_id", "hcp_id", "metric_id", name="uq_run_hcp_metric_dirichlet"),
    )
    def __repr__(self):
        return f"<DirichletState run={self.evaluation_run_id} hcp={self.hcp_id} metric={self.metric_id}>"
    
class MetricValue(db.Model):
    __tablename__ = "metric_values"
    
    id = db.Column(db.Integer, primary_key=True)
    hcp_id = db.Column(db.Integer, db.ForeignKey("hcps.id"), nullable=False)
    metric_id = db.Column(db.Integer, db.ForeignKey("metrics.id"), nullable=False)
    expected_value = db.Column(db.Float, nullable=False)
    confidence = db.Column(db.Float, nullable=False)
    
    __table_args__ = (
        db.UniqueConstraint("hcp_id", "metric_id", name="uq_hcp_metric_value"),
    )
    
class EvaluationRun(db.Model):
    __tablename__ = "evaluation_runs"
    
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(32), default="pending")
    dirichlet_states = db.relationship("DirichletState", back_populates="evaluation_run", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<EvaluationRun {self.id} ({self.status})>"
    
class EvidenceSource(db.Model):
    __tablename__ = "evidence_sources"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    base_url = db.Column(db.String(256), nullable=False)
    accredited = db.Column(db.Boolean, default=False, nullable=False)
    trust_value = db.Column(db.Float, default=0.5, nullable=False)
    
    __table_args__ = (
        db.CheckConstraint("trust_value >= 0.0 AND trust_value <= 1.0", name="ck_source_trust_0_1"),
    )
    
    def __repr__(self):
        return f"<EvidenceSource {self.name} acc={self.accredited} t={self.trust_value}>"
    
class Evidence(db.Model):
    __tablename__ = "evidence"
    
    id = db.Column(db.Integer, primary_key=True)
    evaluation_run_id = db.Column(db.Integer, db.ForeignKey("evaluation_runs.id"), nullable=False)
    hcp_id = db.Column(db.Integer, db.ForeignKey("hcps.id"), nullable=False)
    metric_id = db.Column(db.Integer, db.ForeignKey("metrics.id"), nullable=False)
    source_id = db.Column(db.Integer, db.ForeignKey("evidence_sources.id"), nullable=False)
    observed_outcome_index = db.Column(db.Integer, nullable=False)
    t_e = db.Column(db.Float, nullable=False)
    raw = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow())
    
    __table_args__ = (
        db.CheckConstraint("t_e >= 0.0 AND t_e <= 1.0", name="ck_evidence_te_0_1"),
    )
    
    def __repr__(self):
        return f"Evidence run={self.evaluation_run_id} hcp={self.hcp_id} metric={self.metric_id} src={self.source_id}"
