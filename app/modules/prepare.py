from app.business import DomainIssue, by_kind, prepared, single
from app.modules.calculate import survival_bp


def cohort_observations(entry, exits, cutoff):
    """按观察截止折算 (龄期, 是否在截止时或更早取件)。截止后取件仍算未取（右删失）。"""
    observations = []
    for key, start in entry.items():
        pickup = exits.get(key)
        picked = pickup is not None and pickup <= cutoff
        end = pickup if picked else cutoff
        observations.append((end - start, picked))
    return observations


def prepare(rows):
    window = single(rows, 'observation')
    cutoff, horizon = window['exit'], window['observed']
    arrivals = by_kind(rows, 'arrival')
    entry = {r['parcel']: r['entry'] for r in arrivals}
    if not entry or len(entry) != len(arrivals) or window['basis_points'] > 10_000 or any(t > cutoff for t in entry.values()):
        raise DomainIssue('INVALID_COHORT')
    raw_pickups = {}
    for row in by_kind(rows, 'pickup'):
        key, time = row['parcel'], row['exit']
        if key not in entry or time < entry[key]:
            raise DomainIssue('PICKUP_BEFORE_ENTRY')
        raw_pickups.setdefault(key, set()).add(time)

    base_exits = {key: next(iter(times)) for key, times in raw_pickups.items() if len(times) == 1}
    ambiguous_keys = {key for key, times in raw_pickups.items() if len(times) > 1}
    ambiguous, equivalent, exits = [], [], dict(base_exits)
    for key, times in sorted(raw_pickups.items()):
        if len(times) == 1:
            continue
        candidates = sorted(times)
        # 同一柜件两个取件时刻：分别按各候选时刻估计滞留率；
        # 其余存在多时刻的柜件在本柜判定中一律按未取处理，避免串扰
        exits_without_ambiguity = {k: t for k, t in base_exits.items() if k not in ambiguous_keys}
        estimates = {time: survival_bp(cohort_observations(entry, {**exits_without_ambiguity, key: time}, cutoff), horizon)
                     for time in candidates}
        if len(set(estimates.values())) == 1:
            # 估出的滞留率相同：照常复核，取最早时刻并留痕
            exits[key] = candidates[0]
            equivalent.append({'parcel': key, 'times': candidates, 'remaining_bp': estimates[candidates[0]]})
        else:
            # 估得不同：暂缓整批
            ambiguous.append({'parcel': key, 'times': candidates, 'remaining_bp': estimates})
    if ambiguous:
        raise DomainIssue('PICKUP_AMBIGUOUS', {'ambiguous_pickups': ambiguous})

    observations = cohort_observations(entry, exits, cutoff)
    post_cutoff = [{'parcel': key, 'exit': time} for key, time in sorted(base_exits.items()) if time > cutoff]
    details = {'cutoff': cutoff, 'censored': sum(not picked for _, picked in observations),
               'post_cutoff_pickups': post_cutoff, 'equivalent_pickups': equivalent}
    return prepared(len(observations), details, (observations, horizon, window['basis_points']))
