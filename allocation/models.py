from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    location: str
    current: str
    expected: str
    hint: str


@dataclass(frozen=True)
class YearBudget:
    limit: int
    excluded: frozenset[int]


@dataclass(frozen=True)
class Project:
    name: str
    alias: str
    start: date
    end: date
    yearly: dict[int, YearBudget]


@dataclass(frozen=True)
class Person:
    name: str
    absences: tuple[tuple[date, date], ...]
    targets: dict[str, int]


@dataclass(frozen=True)
class Inputs:
    year: int
    projects: dict[str, Project]
    people: dict[str, Person]
    project_file: str
    people_file: str


@dataclass
class Result:
    inputs: Inputs
    issues: list[Issue] = field(default_factory=list)
    absence: dict[str, list[int]] = field(default_factory=dict)
    allocations: dict[str, dict[str, list[int]]] = field(default_factory=dict)

    @property
    def has_errors(self):
        return any(issue.severity == 'ERROR' for issue in self.issues)


def amount(units: int) -> str:
    sign = '-' if units < 0 else ''
    value = abs(units)
    return f'{sign}{value // 100000}.{value % 100000:05d}'
