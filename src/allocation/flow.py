"""정수 용량의 최대 흐름과 결정적인 균등 초안 조정."""
from collections import deque


class Flow:
    def __init__(self, size):
        self.graph = [[] for _ in range(size)]

    def add(self, source, target, capacity):
        forward = [target, capacity, None]
        backward = [source, 0, forward]
        forward[2] = backward
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def run(self, source, sink):
        total = 0
        while True:
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
            node = sink
            delta = None
            while node != source:
                previous, edge = parents[node]
                delta = edge[1] if delta is None else min(delta, edge[1])
                node = previous
            node = sink
            while node != source:
                previous, edge = parents[node]
                edge[1] -= delta
                edge[2][1] += delta
                node = previous
            total += delta


def transport(targets, allowed, capacities):
    """반환: (해 또는 None, 부족량, 최소 컷의 사업, 최소 컷의 월)."""
    aliases = sorted(targets, key=lambda alias: (len(allowed[alias]), alias))
    count = len(aliases)
    source, sink = count + len(capacities), count + len(capacities) + 1
    flow = Flow(sink + 1)
    edges = {}
    for p, alias in enumerate(aliases):
        flow.add(source, p, targets[alias])
        for month in sorted(allowed[alias]):
            edges[p, month] = flow.add(p, count + month, targets[alias])
    for month, capacity in enumerate(capacities):
        flow.add(count + month, sink, capacity)
    total, reachable = flow.run(source, sink)
    if total != sum(targets.values()):
        return (None, sum(targets.values()) - total,
                [alias for p, alias in enumerate(aliases) if p in reachable],
                [m + 1 for m in range(len(capacities)) if count + m in reachable])
    values = [[0] * len(capacities) for _ in aliases]
    for (p, month), edge in edges.items():
        values[p][month] = edge[2][1]
    # 각 셀을 초안에 가장 가까운 실행 가능한 값으로 조정한 뒤 고정한다.
    # 잔여 그래프의 순환으로만 이동하므로 행·열 합계가 항상 보존된다.
    locked = set()
    for p, alias in enumerate(aliases):
        months = sorted(allowed[alias])
        if not months:
            continue
        quotient, remainder = divmod(targets[alias], len(months))
        for position, month in enumerate(months):
            wanted = quotient + (position < remainder)
            direction = 1 if wanted > values[p][month] else -1
            start, finish = (count + month, p) if direction == 1 else (p, count + month)
            while values[p][month] != wanted:
                parents = {start: None}
                queue = deque([start])
                while queue and finish not in parents:
                    node = queue.popleft()
                    if node < count:
                        neighbors = [(count + m, node, m, 1, targets[aliases[node]] - values[node][m])
                                     for m in sorted(allowed[aliases[node]])]
                    else:
                        m = node - count
                        neighbors = [(other, other, m, -1, values[other][m]) for other in range(count)]
                    for neighbor, row, col, sign, capacity in neighbors:
                        if (capacity <= 0 or (row, col) in locked or (row, col) == (p, month)
                                or neighbor in parents):
                            continue
                        parents[neighbor] = (node, row, col, sign, capacity)
                        queue.append(neighbor)
                if finish not in parents:
                    break
                delta = abs(wanted - values[p][month])
                node = finish
                while node != start:
                    previous, row, col, sign, capacity = parents[node]
                    delta = min(delta, capacity)
                    node = previous
                node = finish
                while node != start:
                    previous, row, col, sign, capacity = parents[node]
                    values[row][col] += sign * delta
                    node = previous
                values[p][month] += direction * delta
            locked.add((p, month))
    return {alias: values[p] for p, alias in enumerate(aliases)}, 0, [], []
