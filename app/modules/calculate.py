from collections import defaultdict
from fractions import Fraction

from app.business import measured


def survival_bp(observations, horizon):
    """按 Kaplan–Meier 联乘观察时点仍未取件的比例，返回基点整数。"""
    groups = defaultdict(lambda: [0, 0])
    for age, picked in observations:
        groups[age][0 if picked else 1] += 1
    risk, probability, at_horizon = len(observations), Fraction(1), Fraction(1)
    for age, (events, censored) in sorted(groups.items()):
        probability *= Fraction(risk - events, risk)
        if age <= horizon:
            at_horizon = probability
        risk -= events + censored
    return int(at_horizon * 10000)


def compute(data):
    observations, horizon, expected = data
    groups = defaultdict(lambda: [0, 0])
    for age, picked in observations:
        groups[age][0 if picked else 1] += 1
    risk, probability, at_horizon = len(observations), Fraction(1), Fraction(1)
    curve = []
    for age, (events, censored) in sorted(groups.items()):
        probability *= Fraction(risk - events, risk)
        if age <= horizon:
            at_horizon = probability
        curve.append({'age': age, 'risk': risk, 'picked': events, 'censored': censored, 'remaining_bp': int(probability*10000)})
        risk -= events + censored
    return measured(int(at_horizon*10000), expected, '基点', {'curve': curve, 'horizon': horizon}, horizon > max(groups))
