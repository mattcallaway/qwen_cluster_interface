"""Typed reconciliation prototype; rules must come from verified source records.

Allocations are expenditures. Match is a designation on an expenditure, never
an additional pool of money. This validator does not select a funding solution.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


def money(value):
    if not isinstance(value, (str, Decimal)):
        raise ValueError('Money must be a decimal string or Decimal')
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0 or result != result.quantize(Decimal('.01')):
            raise ValueError('Money must be finite, nonnegative and exact to cents')
        return result
    except InvalidOperation as exc:
        raise ValueError('Invalid money') from exc


@dataclass(frozen=True)
class Cost:
    id: str
    category: str
    amount: str


@dataclass(frozen=True)
class Fund:
    id: str
    kind: str  # grant, cash, inkind
    status: str  # proposed, committed
    amount: str
    eligible_categories: frozenset[str]
    rule_evidence_id: str
    match_rate: str = '0'
    match_basis: str = 'grant'  # grant or total (grant + required match)
    accepted_match_kinds: frozenset[str] = frozenset()
    # (category, fraction of allocated grant), aggregate across all cost lines
    category_caps: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Allocation:
    cost_id: str
    fund_id: str
    amount: str
    match_for: str | None = None


def reconcile(total, costs: tuple[Cost, ...], funds: tuple[Fund, ...],
              allocations: tuple[Allocation, ...]):
    total = money(total)
    errors = []
    if not costs or len({c.id for c in costs}) != len(costs):
        raise ValueError('Cost IDs must be nonempty and unique')
    if len({f.id for f in funds}) != len(funds):
        raise ValueError('Fund IDs must be unique')
    cm = {c.id: c for c in costs}
    fm = {f.id: f for f in funds}
    if any(not c.id or not c.category for c in costs):
        raise ValueError('Cost identity and category required')
    amounts = {c.id: money(c.amount) for c in costs}
    balances = {f.id: money(f.amount) for f in funds}
    used_cost = {c.id: Decimal(0) for c in costs}
    used_fund = {f.id: Decimal(0) for f in funds}
    matched = {f.id: Decimal(0) for f in funds}
    category_used = {}
    for f in funds:
        if (not f.id or f.kind not in {'grant','cash','inkind'}
                or f.status not in {'proposed','committed'} or not f.rule_evidence_id):
            raise ValueError('Invalid or unsourced fund')
        rate = Decimal(f.match_rate)
        if not rate.is_finite() or rate < 0 or f.match_basis not in {'grant','total'}:
            raise ValueError('Invalid match rule')
        if f.match_basis == 'total' and rate >= 1:
            raise ValueError('Total-cost match rate must be below one')
    if sum(amounts.values()) != total:
        errors.append('cost_total_mismatch')
    for a in allocations:
        if a.cost_id not in cm or a.fund_id not in fm:
            raise ValueError('Unknown allocation reference')
        amount = money(a.amount)
        c, f = cm[a.cost_id], fm[a.fund_id]
        used_cost[c.id] += amount
        used_fund[f.id] += amount
        key = (f.id, c.category)
        category_used[key] = category_used.get(key, Decimal(0)) + amount
        if c.category not in f.eligible_categories:
            errors.append('ineligible_expenditure')
        if a.match_for is not None:
            target = fm.get(a.match_for)
            if (not target or target.kind != 'grant' or f.kind == 'grant'
                    or f.kind not in target.accepted_match_kinds):
                errors.append('ineligible_match')
            elif f.status != 'committed':
                errors.append('uncommitted_match')
            else:
                matched[target.id] += amount
    for cid, amount in used_cost.items():
        if amount > amounts[cid]:
            errors.append('cost_overallocated')
    deficits = {}
    match_calculations = {}
    for f in funds:
        if used_fund[f.id] > balances[f.id]:
            errors.append('fund_overallocated')
        if used_fund[f.id] and f.status != 'committed':
            errors.append('uncommitted_funding')
        if f.kind == 'grant':
            rate = Decimal(f.match_rate)
            required = used_fund[f.id] * rate
            if f.match_basis == 'total':
                required /= 1 - rate
            required = required.quantize(Decimal('.01'), rounding='ROUND_CEILING')
            deficits[f.id] = max(Decimal(0), required - matched[f.id])
            match_calculations[f.id] = {'grant_allocated':str(used_fund[f.id]),
                'rate':f.match_rate,'basis':f.match_basis,'required':str(required),
                'eligible_match_allocated':str(matched[f.id]),'deficit':str(deficits[f.id])}
            if deficits[f.id]:
                errors.append('match_deficit')
            for category, fraction in f.category_caps:
                frac = Decimal(fraction)
                if not frac.is_finite() or not 0 <= frac <= 1:
                    raise ValueError('Invalid category cap')
                if category_used.get((f.id,category), Decimal(0)) > used_fund[f.id] * frac:
                    errors.append('category_cap_exceeded')
    # A funding gap is the unallocated cost, not a sum of overlapping match and
    # permitting gaps. Match deficits are reported separately.
    gap = sum(max(Decimal(0), amounts[cid] - amount) for cid,amount in used_cost.items())
    if gap:
        errors.append('funding_gap')
    return {'valid': not errors, 'errors': sorted(set(errors)),
            'funding_gap': str(gap), 'match_deficits': {k:str(v) for k,v in deficits.items()},
            'derivations':{'cost_allocated':{k:str(v) for k,v in used_cost.items()},
                'fund_allocated':{k:str(v) for k,v in used_fund.items()},'match':match_calculations}}
