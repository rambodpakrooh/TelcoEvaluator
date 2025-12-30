import math
from collections import defaultdict

EPS = 1e-12

def initial_alpha_for_metric(metric):
    return [1.0] * metric.k

def validate_ordinal_values(ordinal_values):
    for v in ordinal_values:
        if not (0.0 <= v <= 1.0):
            raise ValueError("All ordinal values must be in [0,1].")

    vals = sorted(ordinal_values)
    for a, b in zip(vals, vals[1:]):
        if a == b:
            raise ValueError(f"Ordinal values must be unique.")

def dirichlet_update(alpha, outcome_index, t_e):
    new_alpha = list(alpha)
    new_alpha[outcome_index] += float(t_e)
    return new_alpha

def prod(values):
    p = 1.0
    for v in values:
        p *= v
    return p

def evidence_items_trustworthiness(items, sources_by_id, trust_by_source_id):
    """
    items: list of dictionaries with keys: source_id, observed_outcome_label, raw (optional), observed_outcome_index
    sources_by_id: dictionary of (id, EvidenceSource) pairs
    Returns: list of t_e values corresponding to items
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
        support_trust_by_outcome[o] = [trust_by_source_id[sid].trust for sid in support_ids]
        conflict_trust_by_outcome[o] = [trust_by_source_id[sid].trust for sid in conflict_ids]
    
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

def dirichlet_concentration(alpha):
    return float(sum(alpha))

def dirichlet_mean(alpha):
    a0 = dirichlet_concentration(alpha)
    return [a / a0 for a in alpha]

def metric_expected_and_confidence(alpha, ordinal_values):
    k = len(alpha)
    a0 = dirichlet_concentration(alpha)
    means = dirichlet_mean(alpha)

    expected = sum(m * o for m, o in zip(means, ordinal_values))

    second_moment = sum(m * (o ** 2) for m, o in zip(means, ordinal_values))
    var = (second_moment - expected ** 2) / (a0 + 1.0)

    sum_o2 = sum(o ** 2 for o in ordinal_values)
    sum_o = sum(o for o in ordinal_values)
    max_var = (k * sum_o2 - (sum_o ** 2)) / (k ** 2 * (k + 1.0))

    conf = 1.0 - math.sqrt(var / max_var)

    return expected, conf

def aggregate_dimension_scores(metric_values):
    """
    metric_values: iterable of MetricValue objects with:
            - hcp_id
            - metric
            - expected_value
            - confidence

    Returns: dict keyed by (hcp_id, dimension_id) -> dimension_score
    """
    
    buckets = defaultdict(list)
    for mv in metric_values:
        T_m = mv.expected_value * mv.confidence
        metric = mv.metric
        dim_id = metric.dimension_id
        buckets[(mv.hcp_id, dim_id)].append((T_m, metric.weight))

    dimension_scores = {}
    for (hcp_id, dim_id), values in buckets.items():
        total_weight = sum(w for _, w in values)
        log_weighted_sum = 0.0
        for T_m, w in values:
            w_norm = w / total_weight
            log_weighted_sum += w_norm * math.log(T_m + EPS)
        dimension_scores[(hcp_id, dim_id)] = math.exp(log_weighted_sum)

    return dimension_scores

def aggregate_total_scores(dimension_scores, dimensions_by_id):
    """
    dimension_scores: dict keyed by (hcp_id, dimension_id) -> dimension_score
    dimensions_by_id: dict dimension_id -> dimension object
    Returns: dict keyed by hcp_id -> total_trust_score
    """

    buckets = defaultdict(list)
    for (hcp_id, dim_id), score in dimension_scores.items():
        dim = dimensions_by_id[dim_id]
        buckets[hcp_id].append((score, dim.weight))

    total_scores = {}
    for hcp_id, values in buckets.items():
        total_weight = sum(w for _, w in values)
        log_weighted_sum = 0.0
        for score, w in values:
            w_norm = w / total_weight
            log_weighted_sum += w_norm * math.log(score + EPS)
        total_scores[hcp_id] = math.exp(log_weighted_sum)
    
    return total_scores
            