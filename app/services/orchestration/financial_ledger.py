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
    currency: str = 'USD'


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
    match_eligible_categories: frozenset[str] = frozenset()
    max_inkind_match_share: str | None = None
    min_cash_match_share: str | None = None
    currency: str = 'USD'


@dataclass(frozen=True)
class Allocation:
    cost_id: str
    fund_id: str
    amount: str
    match_for: str | None = None
    currency: str = 'USD'


ALLOWED_PAYLOAD_KEYS = frozenset({
    'total', 'currency', 'costs', 'funds', 'allocations', 'verified_evidence_ids', 'project_id', 'quantity',
})
ALLOWED_COST_KEYS = frozenset({'id', 'category', 'amount', 'currency'})
ALLOWED_FUND_KEYS = frozenset({
    'id', 'kind', 'status', 'amount', 'eligible_categories', 'rule_evidence_id',
    'match_rate', 'match_basis', 'accepted_match_kinds', 'category_caps',
    'match_eligible_categories', 'max_inkind_match_share', 'min_cash_match_share', 'currency',
})
ALLOWED_ALLOCATION_KEYS = frozenset({'cost_id', 'fund_id', 'amount', 'match_for', 'currency'})


def reconcile(
    total,
    costs: tuple[Cost, ...],
    funds: tuple[Fund, ...],
    allocations: tuple[Allocation, ...],
    *,
    currency: str = 'USD',
    verified_evidence_ids: set[str] | frozenset[str] | None = None,
    extra_errors: list[str] | None = None,
):
    total = money(total)
    errors = list(extra_errors or [])
    norm_currency = str(currency or 'USD').strip().upper()
    if not costs or len({c.id for c in costs}) != len(costs):
        raise ValueError('Cost IDs must be nonempty and unique')
    if len({f.id for f in funds}) != len(funds):
        raise ValueError('Fund IDs must be unique')
    cm = {c.id: c for c in costs}
    fm = {f.id: f for f in funds}
    if any(not c.id or not c.category for c in costs):
        raise ValueError('Cost identity and category required')
    for c in costs:
        if str(c.currency or 'USD').strip().upper() != norm_currency:
            errors.append('currency_mismatch')
    amounts = {c.id: money(c.amount) for c in costs}
    balances = {f.id: money(f.amount) for f in funds}
    used_cost = {c.id: Decimal(0) for c in costs}
    used_fund = {f.id: Decimal(0) for f in funds}
    matched = {f.id: Decimal(0) for f in funds}
    matched_by_kind: dict[tuple[str, str], Decimal] = {}
    category_used = {}
    for f in funds:
        if (not f.id or f.kind not in {'grant', 'cash', 'inkind'}
                or f.status not in {'proposed', 'committed'} or not f.rule_evidence_id):
            raise ValueError('Invalid or unsourced fund')
        if str(f.currency or 'USD').strip().upper() != norm_currency:
            errors.append('currency_mismatch')
        if verified_evidence_ids is not None and f.rule_evidence_id not in verified_evidence_ids:
            errors.append('unverified_rule_evidence')
        rate = Decimal(f.match_rate)
        if not rate.is_finite() or rate < 0 or f.match_basis not in {'grant', 'total'}:
            raise ValueError('Invalid match rule')
        if f.match_basis == 'total' and rate >= 1:
            raise ValueError('Total-cost match rate must be below one')
    if sum(amounts.values()) != total:
        errors.append('cost_total_mismatch')
    for a in allocations:
        if a.cost_id not in cm or a.fund_id not in fm:
            raise ValueError('Unknown allocation reference')
        if str(a.currency or 'USD').strip().upper() != norm_currency:
            errors.append('currency_mismatch')
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
                allowed_match_cats = (
                    target.match_eligible_categories
                    if target.match_eligible_categories
                    else target.eligible_categories
                )
                if c.category not in allowed_match_cats:
                    errors.append('ineligible_match_category')
                    errors.append('ineligible_match')
                else:
                    matched[target.id] += amount
                    mk = (target.id, f.kind)
                    matched_by_kind[mk] = matched_by_kind.get(mk, Decimal(0)) + amount
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
            match_calculations[f.id] = {
                'grant_allocated': str(used_fund[f.id]),
                'rate': f.match_rate,
                'basis': f.match_basis,
                'required': str(required),
                'eligible_match_allocated': str(matched[f.id]),
                'deficit': str(deficits[f.id]),
            }
            if deficits[f.id]:
                errors.append('match_deficit')
            if f.max_inkind_match_share is not None:
                max_ik = Decimal(str(f.max_inkind_match_share))
                if not max_ik.is_finite() or not 0 <= max_ik <= 1:
                    raise ValueError('Invalid max_inkind_match_share')
                ik_used = matched_by_kind.get((f.id, 'inkind'), Decimal(0))
                if required > 0 and ik_used > (required * max_ik).quantize(Decimal('.01')):
                    errors.append('inkind_match_cap_exceeded')
            if f.min_cash_match_share is not None:
                min_cash = Decimal(str(f.min_cash_match_share))
                if not min_cash.is_finite() or not 0 <= min_cash <= 1:
                    raise ValueError('Invalid min_cash_match_share')
                cash_used = matched_by_kind.get((f.id, 'cash'), Decimal(0))
                if required > 0 and cash_used < (required * min_cash).quantize(Decimal('.01')):
                    errors.append('cash_match_minimum_unmet')
            for category, fraction in f.category_caps:
                frac = Decimal(fraction)
                if not frac.is_finite() or not 0 <= frac <= 1:
                    raise ValueError('Invalid category cap')
                if category_used.get((f.id, category), Decimal(0)) > used_fund[f.id] * frac:
                    errors.append('category_cap_exceeded')
    # A funding gap is the unallocated cost, not a sum of overlapping match and
    # permitting gaps. Match deficits are reported separately.
    gap = sum(max(Decimal(0), amounts[cid] - amount) for cid, amount in used_cost.items())
    if gap:
        errors.append('funding_gap')
    return {
        'valid': not errors,
        'errors': sorted(set(errors)),
        'funding_gap': str(gap),
        'match_deficits': {k: str(v) for k, v in deficits.items()},
        'derivations': {
            'cost_allocated': {k: str(v) for k, v in used_cost.items()},
            'fund_allocated': {k: str(v) for k, v in used_fund.items()},
            'match': match_calculations,
        },
    }


def reconcile_grant_budget_ledger(payload: dict) -> dict:
    """Validate structured dictionary payload (rejecting unknown rule fields and unverified evidence IDs) and run reconcile()."""
    extra_errors: list[str] = []
    if not isinstance(payload, dict):
        raise ValueError('Ledger payload must be a dict')
    if set(payload.keys()) - ALLOWED_PAYLOAD_KEYS:
        extra_errors.append('unknown_rule_field')

    costs_list: list[Cost] = []
    for c in payload.get('costs', []):
        if not isinstance(c, dict):
            raise ValueError('Cost entry must be a dict')
        if set(c.keys()) - ALLOWED_COST_KEYS:
            extra_errors.append('unknown_rule_field')
        costs_list.append(
            Cost(
                id=str(c['id']),
                category=str(c['category']),
                amount=str(c['amount']),
                currency=str(c.get('currency', payload.get('currency', 'USD'))),
            )
        )

    funds_list: list[Fund] = []
    for f in payload.get('funds', []):
        if not isinstance(f, dict):
            raise ValueError('Fund entry must be a dict')
        if set(f.keys()) - ALLOWED_FUND_KEYS:
            extra_errors.append('unknown_rule_field')
        funds_list.append(
            Fund(
                id=str(f['id']),
                kind=str(f['kind']),
                status=str(f['status']),
                amount=str(f['amount']),
                eligible_categories=frozenset(f.get('eligible_categories', [])),
                rule_evidence_id=str(f.get('rule_evidence_id', '')),
                match_rate=str(f.get('match_rate', '0')),
                match_basis=str(f.get('match_basis', 'grant')),
                accepted_match_kinds=frozenset(f.get('accepted_match_kinds', [])),
                category_caps=tuple(tuple(p) for p in f.get('category_caps', [])),
                match_eligible_categories=frozenset(f.get('match_eligible_categories', [])),
                max_inkind_match_share=str(f['max_inkind_match_share']) if f.get('max_inkind_match_share') is not None else None,
                min_cash_match_share=str(f['min_cash_match_share']) if f.get('min_cash_match_share') is not None else None,
                currency=str(f.get('currency', payload.get('currency', 'USD'))),
            )
        )

    allocs_list: list[Allocation] = []
    for a in payload.get('allocations', []):
        if not isinstance(a, dict):
            raise ValueError('Allocation entry must be a dict')
        if set(a.keys()) - ALLOWED_ALLOCATION_KEYS:
            extra_errors.append('unknown_rule_field')
        allocs_list.append(
            Allocation(
                cost_id=str(a['cost_id']),
                fund_id=str(a['fund_id']),
                amount=str(a['amount']),
                match_for=a.get('match_for'),
                currency=str(a.get('currency', payload.get('currency', 'USD'))),
            )
        )

    verified_ev: set[str] | None = None
    if payload.get('verified_evidence_ids') is not None:
        verified_ev = {str(x) for x in payload['verified_evidence_ids']}

    return reconcile(
        payload['total'],
        tuple(costs_list),
        tuple(funds_list),
        tuple(allocs_list),
        currency=str(payload.get('currency', 'USD')),
        verified_evidence_ids=verified_ev,
        extra_errors=extra_errors,
    )
