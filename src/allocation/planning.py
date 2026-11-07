"""연간 초안: 공용 월 가용량을 쓰는 정수 최소 비용 흐름과 공용 엔진 검증."""
from dataclasses import dataclass, replace
from heapq import heappop, heappush

from .engine import BASE, absence_units, allowed_months, calculate
from .models import Inputs, Issue, Person, PlanningInputs, amount

MIN_PARTICIPATION = 1000  # 0.01000


@dataclass
class PlanReview:
    issues: list[Issue]
    monthly: object = None

    @property
    def has_errors(self):
        return any(issue.severity == 'ERROR' for issue in self.issues)


@dataclass
class PlanGeneration:
    targets: dict[str, dict[str, int]]
    shortages: dict[str, int]
    review: PlanReview
    precision: str
    complete: bool


class _CostFlow:
    def __init__(self, size):
        self.edges = [[] for _ in range(size)]
        self.balance = [0] * size

    def add(self, source, target, upper, cost=0, lower=0):
        if not 0 <= lower <= upper:
            raise ValueError('잘못된 흐름 경계')
        forward = [target, upper - lower, cost, None]
        backward = [source, 0, -cost, forward]
        forward[3] = backward
        self.edges[source].append(forward)
        self.edges[target].append(backward)
        self.balance[source] -= lower
        self.balance[target] += lower
        return forward, lower

    def run(self, source, sink, need=None):
        """음의 초기 비용을 허용하는 잠재치 + 최단 증분 경로."""
        size = len(self.edges)
        infinity = 10**40
        potential = [infinity] * size
        potential[source] = 0
        for _ in range(size - 1):
            changed = False
            for node, edges in enumerate(self.edges):
                if potential[node] == infinity:
                    continue
                for edge in edges:
                    if edge[1] > 0 and potential[edge[0]] > potential[node] + edge[2]:
                        potential[edge[0]] = potential[node] + edge[2]
                        changed = True
            if not changed:
                break
        potential = [value if value != infinity else 0 for value in potential]
        sent = 0
        while need is None or sent < need:
            distances = [infinity] * size
            distances[source] = 0
            parent = [None] * size
            queue = [(0, source)]
            while queue:
                distance, node = heappop(queue)
                if distance != distances[node]:
                    continue
                for edge in self.edges[node]:
                    if edge[1] <= 0:
                        continue
                    next_node = edge[0]
                    candidate = distance + edge[2] + potential[node] - potential[next_node]
                    if candidate < distances[next_node]:
                        distances[next_node] = candidate
                        parent[next_node] = (node, edge)
                        heappush(queue, (candidate, next_node))
            if parent[sink] is None:
                break
            for node, distance in enumerate(distances):
                if distance < infinity:
                    potential[node] += distance
            delta = need - sent if need is not None else infinity
            node = sink
            while node != source:
                previous, edge = parent[node]
                delta = min(delta, edge[1])
                node = previous
            node = sink
            while node != source:
                previous, edge = parent[node]
                edge[1] -= delta
                edge[3][1] += delta
                node = previous
            sent += delta
        return sent


def _weights(priorities):
    ranks = sorted(set(priorities.values()))
    if len(ranks) == 2:
        return {alias: 3 if rank == ranks[0] else 2 for alias, rank in priorities.items()}
    return {alias: len(ranks) - ranks.index(rank) for alias, rank in priorities.items()}


def _segments(capacity, weight, weight_sum):
    desired, remainder = divmod(capacity * weight, weight_sum)
    boundaries = {0, capacity, desired, min(capacity, desired + (remainder > 0))}
    boundaries.update(range(5000, capacity, 5000))
    bounds = sorted(boundaries)
    for left, right in zip(bounds, bounds[1:]):
        # 정규화한 제곱편차의 구간 평균 기울기. 정수만 사용한다.
        cost = ((left + right) * weight_sum - 2 * capacity * weight) * 10**11 // (capacity * capacity * weight_sum)
        yield right - left, cost


def _network(plan, capacities, full):
    names = sorted(plan.people)
    aliases = sorted(plan.projects)
    source, sink = 0, 1
    next_node = 2
    month_nodes, pair_nodes, project_nodes = {}, {}, {}
    for name in names:
        for month in range(12):
            month_nodes[name, month] = next_node
            next_node += 1
        for alias in sorted(plan.people[name].priorities):
            pair_nodes[name, alias] = next_node
            next_node += 1
    for alias in aliases:
        project_nodes[alias] = next_node
        next_node += 1
    super_source, super_sink = next_node, next_node + 1
    graph = _CostFlow(next_node + 2)
    total = 0
    tracked = {}
    for name in names:
        person = plan.people[name]
        annual = sum(capacities[name])
        total += annual
        weights = _weights(person.priorities)
        weight_sum = sum(weights.values())
        for month, capacity in enumerate(capacities[name]):
            graph.add(source, month_nodes[name, month], capacity, lower=capacity if full else 0)
        for alias in sorted(person.priorities):
            node = pair_nodes[name, alias]
            allowed = allowed_months(plan.projects[alias], plan.year)
            for month in sorted(allowed):
                graph.add(month_nodes[name, month], node, capacities[name][month])
            minimum = MIN_PARTICIPATION if full and annual else 0
            tracked[name, alias] = []
            for segment, cost in _segments(annual, weights[alias], weight_sum) if annual else ():
                lower = min(minimum, segment)
                minimum -= lower
                tracked[name, alias].append(graph.add(node, project_nodes[alias], segment, cost, lower))
            if minimum:
                # C < 0.01이면 최소 참여가 불가능하다.
                return None, None, total
    for alias in aliases:
        budget = plan.projects[alias].yearly.get(plan.year)
        graph.add(project_nodes[alias], sink, budget.limit if budget else 0)
    if full:
        graph.add(sink, source, total, lower=total)
        demand = 0
        for node, balance in enumerate(graph.balance[:next_node]):
            if balance > 0:
                graph.add(super_source, node, balance)
                demand += balance
            elif balance < 0:
                graph.add(node, super_sink, -balance)
        sent = graph.run(super_source, super_sink, demand)
    else:
        sent = graph.run(source, sink)
        demand = total
    if full and sent != demand:
        return None, None, total
    targets = {name: {} for name in names}
    for (name, alias), edges in tracked.items():
        targets[name][alias] = sum(lower + edge[3][1] for edge, lower in edges)
    return targets, sent, total


def _monthly_inputs(plan, targets):
    people = {name: Person(name, person.absences, dict(sorted(targets[name].items())))
              for name, person in plan.people.items()}
    return Inputs(plan.year, plan.projects, people, plan.project_file, plan.plan_file)


def review_plan(plan: PlanningInputs):
    """저장한 초안과 수동 편집값을 같은 함수로 검사한다."""
    issues = []
    targets = {}
    for name, person in plan.people.items():
        if person.allocations is None:
            issues.append(Issue('ERROR', 'P100', f'{plan.plan_file}: {name}', '초안 없음', '초안 생성 또는 직접 입력',
                                '선택 사업에 초안을 입력하세요.'))
        targets[name] = {alias: (person.allocations or {}).get(alias, 0)
                         for alias in person.priorities}
        if person.allocations is not None:
            absence, _ = absence_units(Person(name, person.absences, {}), plan.year, plan.plan_file)
            if sum(BASE) - sum(absence) > 0:
                for alias, value in targets[name].items():
                    if value < MIN_PARTICIPATION:
                        issues.append(Issue('ERROR', 'P102', f'{plan.plan_file}: {name} / {alias}',
                                            amount(value), '최소 0.01000', '선택 사업에 최소 참여율을 배정하세요.'))
    monthly = calculate(_monthly_inputs(plan, targets))
    issues.extend(monthly.issues)
    return PlanReview(issues, monthly)


def _two_decimal_candidate(plan, targets, capacities):
    result = {}
    for name, rows in targets.items():
        annual = sum(capacities[name])
        if annual % 1000:
            return None
        rounded = {alias: value // 1000 * 1000 for alias, value in rows.items()}
        remaining = (annual - sum(rounded.values())) // 1000
        for alias in sorted(rows, key=lambda alias: (-(rows[alias] % 1000), alias))[:remaining]:
            rounded[alias] += 1000
        result[name] = rounded
    return result


def generate_plan(plan: PlanningInputs):
    """완전한 최소 참여 해를 먼저 찾고, 없으면 최대 배정량의 부분 초안을 만든다."""
    capacities = {}
    for name, person in plan.people.items():
        absence, _ = absence_units(Person(name, person.absences, {}), plan.year, plan.plan_file)
        capacities[name] = [base - value for base, value in zip(BASE, absence)]
    targets, _, _ = _network(plan, capacities, True)
    complete = targets is not None
    if not complete:
        targets, assigned, _ = _network(plan, capacities, False)
        if targets is None:
            targets = {name: {alias: 0 for alias in person.priorities} for name, person in plan.people.items()}
            assigned = 0
    precision = '5자리 사용'
    if complete:
        candidate = _two_decimal_candidate(plan, targets, capacities)
        if candidate is not None and not review_plan(_with_targets(plan, candidate)).has_errors:
            targets = candidate
            precision = '2자리 사용'
    shortages = {name: sum(capacities[name]) - sum(rows.values()) for name, rows in targets.items()}
    review = review_plan(_with_targets(plan, targets))
    if not complete:
        for name, shortage in shortages.items():
            if shortage:
                aliases = sorted(plan.people[name].priorities)
                covered = set().union(*(allowed_months(plan.projects[alias], plan.year) for alias in aliases)) if aliases else set()
                uncovered = [month + 1 for month, capacity in enumerate(capacities[name])
                             if capacity and month not in covered]
                details = []
                if uncovered:
                    details.append('허용 사업 없는 월 ' + ', '.join(map(str, uncovered)))
                for alias in aliases:
                    budget = plan.projects[alias].yearly.get(plan.year)
                    limit = budget.limit if budget else 0
                    months = sorted(month + 1 for month in allowed_months(plan.projects[alias], plan.year))
                    details.append(f'{alias}: 상한 {amount(limit)}, 허용 월 {months}')
                review.issues.append(Issue('ERROR', 'P201', f'{plan.plan_file}: {name}',
                                           f'미배정 {amount(shortage)}; ' + '; '.join(details),
                                           '연간 가용량 전부 배정',
                                           '상한·허용 월·휴직을 확인하거나 참여 사업을 조정하세요.'))
    return PlanGeneration(targets, shortages, review, precision, complete and not review.has_errors)


def _with_targets(plan, targets):
    return replace(plan, people={name: replace(person, allocations=targets[name])
                                 for name, person in plan.people.items()})
