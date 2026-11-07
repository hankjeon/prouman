"""정수 흐름으로 정확한 균등 배정을 먼저 찾고 제약 충돌을 조정한다."""
from collections import deque


class Flow:
    def __init__(self, size):
        self.graph = [[] for _ in range(size)]

    def add(self, source, target, capacity, cost=0, cell=None):
        forward = [target, capacity, None, cost, cell]
        backward = [source, 0, forward, -cost, cell]
        forward[2] = backward
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    @staticmethod
    def augment(parents, source, sink, limit):
        node = sink
        delta = limit
        while node != source:
            previous, edge = parents[node]
            delta = min(delta, edge[1])
            node = previous
        node = sink
        while node != source:
            previous, edge = parents[node]
            edge[1] -= delta
            edge[2][1] += delta
            node = previous
        return delta

    def shortest_path(self, source, permitted=None):
        # 정수 비용에는 음수 간선도 있으므로 Bellman-Ford를 쓴다.
        distances = [None] * len(self.graph)
        distances[source] = 0
        parents = {source: None}
        for _ in range(len(self.graph) - 1):
            changed = False
            for node, edges in enumerate(self.graph):
                if distances[node] is None:
                    continue
                for edge in edges:
                    if edge[1] <= 0 or permitted is not None and not permitted(node, edge):
                        continue
                    target = edge[0]
                    distance = distances[node] + edge[3]
                    if distances[target] is None or distance < distances[target]:
                        distances[target] = distance
                        parents[target] = (node, edge)
                        changed = True
            if not changed:
                break
        return parents, distances

    def run(self, source, sink, costed=False):
        total = 0
        while True:
            if costed:
                parents, _ = self.shortest_path(source)
            else:
                parents = {source: None}
                queue = deque([source])
                while queue and sink not in parents:
                    node = queue.popleft()
                    for edge in self.graph[node]:
                        if edge[1] > 0 and edge[0] not in parents:
                            parents[edge[0]] = (node, edge)
                            queue.append(edge[0])
            if sink not in parents:
                return total, set(parents)
            limit = sum(edge[1] for edge in self.graph[source])
            total += self.augment(parents, source, sink, limit)


def _month_order(month):
    return (0 if (month + 1) % 3 == 0 else 1, month)


def _solve(targets, allowed, capacities, minimum, deviation=None, mode='feasible'):
    """셀 하한·상한을 예약한 뒤 잔여 수량을 사업→월 흐름으로 채운다."""
    aliases = sorted(targets, key=lambda alias: (len(allowed[alias]), alias))
    count = len(aliases)
    source, sink = count + len(capacities), count + len(capacities) + 1
    flow = Flow(sink + 1)
    lower = {}
    upper = {}
    reserved_month = [0] * len(capacities)
    for alias in aliases:
        months = allowed[alias]
        if not months:
            if targets[alias]:
                return None
            continue
        quotient, _ = divmod(targets[alias], len(months))
        for month in months:
            if mode == 'exact':
                low, high = quotient, quotient + (targets[alias] % len(months) > 0)
            else:
                floor = minimum if mode in ('floor', 'balance') and targets[alias] else 0
                low = floor if deviation is None else max(floor, quotient - deviation)
                high = targets[alias] if deviation is None else min(targets[alias], quotient + 1 + deviation)
            if low > high:
                return None
            lower[alias, month], upper[alias, month] = low, high
            reserved_month[month] += low
    if any(reserved > capacity for reserved, capacity in zip(reserved_month, capacities)):
        return None

    cell_count = len(lower)
    floor_weight = cell_count + 1
    balance_weight = floor_weight * floor_weight
    # 1단위의 최소치 부족이 가능한 모든 균등 비용·동률 비용보다 우선한다.
    shortage_weight = (2 * sum(len(allowed[a]) ** 2 * targets[a] for a in aliases)
                       * balance_weight + cell_count * (floor_weight + 1) + 1)
    edges = {}
    remaining = 0
    for p, alias in enumerate(aliases):
        months = sorted(allowed[alias], key=_month_order)
        supply = targets[alias] - sum(lower[alias, month] for month in months)
        if supply < 0:
            return None
        remaining += supply
        flow.add(source, p, supply)
        if not months:
            continue
        quotient, remainder = divmod(targets[alias], len(months))
        for month in months:
            low, high = lower[alias, month], upper[alias, month]
            cell_edges = []
            if high > low:
                if mode in ('feasible', 'floor'):
                    cell_edges.append(flow.add(p, count + month, high - low))
                elif mode == 'exact':
                    cost = -1 if (month + 1) % 3 == 0 else 0
                    cell_edges.append(flow.add(p, count + month, high - low, cost))
                else:
                    cuts = sorted({low, high} | {point for point in (minimum, minimum + 1,
                                                                    quotient, quotient + 1)
                                                 if low < point < high})
                    for start, end in zip(cuts, cuts[1:]):
                        first = start + 1
                        cost = 0
                        if mode == 'shortage':
                            cost = -1 if first <= minimum else 0
                        else:
                            cost = (abs(len(months) * first - targets[alias])
                                    - abs(len(months) * (first - 1) - targets[alias])) * balance_weight
                            if mode == 'soft' and first <= minimum:
                                cost -= shortage_weight
                            if minimum and first == minimum + 1:
                                cost -= floor_weight
                            if remainder and first == quotient + 1 and (month + 1) % 3 == 0:
                                cost -= 1
                        cell_edges.append(flow.add(p, count + month, end - start, cost))
            edges[alias, month] = cell_edges
    for month, capacity in enumerate(capacities):
        flow.add(count + month, sink, capacity - reserved_month[month])
    costed = mode in ('exact', 'balance', 'soft', 'shortage')
    sent, _ = flow.run(source, sink, costed=costed)
    if sent != remaining:
        return None
    values = {alias: [0] * len(capacities) for alias in aliases}
    for (alias, month), cell_edges in edges.items():
        values[alias][month] = lower[alias, month] + sum(edge[2][1] for edge in cell_edges)
    return values


def _shortage(values, allowed, minimum):
    return sum(max(0, minimum - values[alias][month])
               for alias, months in allowed.items() if sum(values[alias]) > 0 for month in months)


def transport(targets, allowed, capacities, minimum=0):
    """완전 균등 → 최저값 안의 균형 → 필요한 최저값 완화 순서로 배분한다."""
    aliases = sorted(targets, key=lambda alias: (len(allowed[alias]), alias))
    count = len(aliases)
    source, sink = count + len(capacities), count + len(capacities) + 1
    feasibility = Flow(sink + 1)
    for p, alias in enumerate(aliases):
        feasibility.add(source, p, targets[alias])
        for month in sorted(allowed[alias]):
            feasibility.add(p, count + month, targets[alias])
    for month, capacity in enumerate(capacities):
        feasibility.add(count + month, sink, capacity)
    sent, reachable = feasibility.run(source, sink)
    if sent != sum(targets.values()):
        return (None, sum(targets.values()) - sent,
                [alias for p, alias in enumerate(aliases) if p in reachable],
                [m + 1 for m in range(len(capacities)) if count + m in reachable])

    exact = _solve(targets, allowed, capacities, minimum, mode='exact')
    if exact is not None:
        return exact, 0, [], []

    floor_possible = minimum == 0 or _solve(targets, allowed, capacities, minimum, mode='floor') is not None
    mode = 'floor' if floor_possible else 'soft'
    best_shortage = None
    if not floor_possible:
        best = _solve(targets, allowed, capacities, minimum, mode='shortage')
        best_shortage = _shortage(best, allowed, minimum)
    low, high = 0, max(targets.values(), default=0)
    while low < high:
        middle = (low + high) // 2
        if floor_possible:
            feasible = _solve(targets, allowed, capacities, minimum, middle, 'floor') is not None
        else:
            candidate = _solve(targets, allowed, capacities, minimum, middle, 'shortage')
            feasible = candidate is not None and _shortage(candidate, allowed, minimum) == best_shortage
        if feasible:
            high = middle
        else:
            low = middle + 1
    values = _solve(targets, allowed, capacities, minimum, low, 'balance' if floor_possible else 'soft')
    return values, 0, [], []


def minimum_can_improve(targets, allowed, values, minimum):
    """합계 보존 재배치로 최소치 부족을 줄일 음수 순환이 있는지 검사한다."""
    aliases = sorted(targets)
    months = len(next(iter(values.values()))) if values else 0
    count = len(aliases)
    edges = []
    for p, alias in enumerate(aliases):
        for month in sorted(allowed[alias]):
            value = values[alias][month]
            if value < targets[alias]:
                edges.append((p, count + month, -1 if value < minimum else 0))
            if value > 0:
                edges.append((count + month, p, 1 if value <= minimum else 0))
    distances = [0] * (count + months)
    for _ in range(count + months):
        changed = False
        for source, target, cost in edges:
            if distances[target] > distances[source] + cost:
                distances[target] = distances[source] + cost
                changed = True
        if not changed:
            return False
    return bool(edges)
