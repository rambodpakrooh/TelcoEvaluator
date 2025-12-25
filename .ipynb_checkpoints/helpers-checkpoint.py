def initial_alpha_for_metric(metric):
    return [1.0] * metric.k

def dirichlet_update(alpha, outcome_index, t_e):
    new_alpha = list(alpha)
    new_alpha[outcome_index] += float(t_e)
    return new_alpha

def prod(values):
    p = 1
    for v in values:
        p *= v
    return p

def evidence_items_trustworthiness(items, sources_by_id):
    """
    items: a list of dictionaries with keys: source_id, observed_outcome_index, raw (optional)
    sources_by_id: a dictionary of (id, EvidenceSource) pairs
    Returns: a list of t_e values corresponding to items
    """

    accredited_items = [itm for itm in items if sources_by_id[itm["source_id"]].accredited]
    if accredited_items:
        accredited_outcome = accredited_items[0]["observed_outcome_index"]
        t_es = []
        for itm in items:
            if itm["observed_outcome_index"] == accredited_outcome:
                t_es.append(1)
            else:
                t_es.append(0)
        return t_es

    sources_by_outcome = {}
    for itm in items:
        o = itm["observed_outcome_index"]
        sources_by_outcome.setdefault(o, []).append(itm["source_id"])
    
    support_trust_by_outcome = {}
    conflict_trust_by_outcome = {}
    all_source_ids = [itm["source_id"] for itm in items]
    
    for o, support_ids in sources_by_outcome.items():
        conflict_ids = [sid for sid in all_source_ids if sid not in support_ids]
        support_sources = [sources_by_id[sid] for sid in support_ids]
        conflict_sources = [sources_by_id[sid] for sid in conflict_ids]
        support_trust_by_outcome[o] = [s.trust_value for s in support_sources]
        conflict_trust_by_outcome[o] = [s.trust_value for s in conflict_sources]
    
    t_es = []
    for itm in items:
        o = itm["observed_outcome_index"]
        sup = 1.0 - prod([(1.0 - t) for t in support_trust_by_outcome[o]])
        if conflict_trust_by_outcome[o]:
            con = 1.0 - prod([(1.0 - t) for t in conflict_trust_by_outcome[o]])
            t_es.append(sup / (sup + con))
        else:
            t_es.append(sup)
    return t_es

def ema_update(old_value, new_observation, rho):
    return (1.0 - rho) * float(old_value) + float(rho) * new_observation