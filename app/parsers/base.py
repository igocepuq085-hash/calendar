from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol


@dataclass
class ParseError:
    row_number: int | None
    message: str
    raw_data: dict[str, Any] | None = None


@dataclass
class ParseResult:
    parser_name: str
    rows: list[dict[str, Any]]
    errors: list[ParseError] = field(default_factory=list)

    @property
    def rows_found(self) -> int:
        return len(self.rows)

    @property
    def employees_found(self) -> int:
        names = {row.get("full_name") for row in self.rows if row.get("full_name")}
        return len(names)

    @property
    def events_found(self) -> int:
        return sum(int(row.get("events_count", 1)) for row in self.rows)


class ExcelParser(Protocol):
    name: str

    def detect(self, workbook: Any) -> bool: ...

    def parse(self, workbook: Any) -> ParseResult: ...

    def validate(self, rows: list[dict[str, Any]]) -> list[ParseError]: ...


def cell_value_with_merge(ws: Any, row: int, column: int) -> Any:
    cell = ws.cell(row, column)
    if cell.value is not None:
        return cell.value
    coord = cell.coordinate
    for merged_range in ws.merged_cells.ranges:
        if coord in merged_range:
            return ws.cell(merged_range.min_row, merged_range.min_col).value
    return None


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None

