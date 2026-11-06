"""정수 흐름: 월별 최소 배정 부족을 먼저 줄인 뒤 균등 초안을 조정한다."""
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
        # 비용은 -1, 0, 1의 정수다. 음수 간선을 포함하므로 Bellman-Ford를 쓴다.
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


def transport(targets, allowed, capacities, minimum=0):
    """(해 또는 None, 부족량, 최소 컷 사업, 최소 컷 월)을 반환한다.

    minimum은 허용 셀별 소프트 하한(정수 단위)이다. 하한 부족량 합계를
    최소화한 뒤, 그 최적값을 유지하면서 기존 균등 초안의 우선순위를 따른다.
    """
    aliases = sorted(targets, key=lambda alias: (len(allowed[alias]), alias))
    count = len(aliases)
    source, sink = count + len(capacities), count + len(capacities) + 1
    flow = Flow(sink + 1)
    edges = {}
    for p, alias in enumerate(aliases):
        flow.add(source, p, targets[alias])
        for month in sorted(allowed[alias]):
            cell_edges = []
            if minimum and targets[alias] > 0:
                # 최초 minimum 단위에만 비용 -1: 최대 흐름 안에서 하한 충족을 우선한다.
                cell_edges.append(flow.add(p, count + month, min(minimum, targets[alias]), -1, (p, month)))
            cell_edges.append(flow.add(p, count + month, targets[alias], cell=(p, month)))
            edges[p, month] = cell_edges
    for month, capacity in enumerate(capacities):
        flow.add(count + month, sink, capacity)
    total, reachable = flow.run(source, sink, costed=minimum > 0)
    if total != sum(targets.values()):
        return (None, sum(targets.values()) - total,
                [alias for p, alias in enumerate(aliases) if p in reachable],
                [m + 1 for m in range(len(capacities)) if count + m in reachable])

    # 최소 비용 해를 유지하는 순환 경로로만 균등 초안에 접근한다.
    # 한 달의 하한이 불가능해도 다른 달의 하한을 함께 해제하지 않는다.
    locked = set()
    for p, alias in enumerate(aliases):
        months = sorted(allowed[alias])
        if not months:
            continue
        quotient, remainder = divmod(targets[alias], len(months))
        for position, month in enumerate(months):
            cell = (p, month)
            cell_edges = edges[cell]
            wanted = quotient + (position < remainder)
            current = sum(edge[2][1] for edge in cell_edges)
            while current != wanted:
                increase = wanted > current
                candidates = cell_edges if increase else [edge[2] for edge in reversed(cell_edges)]
                selected = next((edge for edge in candidates if edge[1] > 0), None)
                if selected is None:
                    break
                start, finish = (count + month, p) if increase else (p, count + month)
                def permitted(node, edge):
                    return edge[4] is not None and edge[4] != cell and edge[4] not in locked
                parents, distances = flow.shortest_path(start, permitted)
                if distances[finish] is None or distances[finish] + selected[3] != 0:
                    break  # 양의 비용 순환은 월별 최소 배정 부족을 늘리므로 허용하지 않는다.
                delta = flow.augment(parents, start, finish, min(abs(wanted - current), selected[1]))
                selected[1] -= delta
                selected[2][1] += delta
                current += delta if increase else -delta
            locked.add(cell)
    values = {alias: [0] * len(capacities) for alias in aliases}
    for (p, month), cell_edges in edges.items():
        values[aliases[p]][month] = sum(edge[2][1] for edge in cell_edges)
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
