from typing import Any

from app.parsers.base import ParseError, ParseResult, as_date, cell_value_with_merge
from app.services.people import normalize_name


CHECK_HEADER_MARKERS = (
    "фильтр",
    "проверка",
    "инструктаж",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_check_header(value: Any) -> bool:
    text = _text(value).lower()
    return bool(text) and any(marker in text for marker in CHECK_HEADER_MARKERS) and "мед" not in text


def _find_knowledge_columns(ws: Any) -> list[tuple[str, int, int]]:
    columns: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for col in range(1, ws.max_column):
        header = ws.cell(3, col).value
        if not _is_check_header(header):
            continue
        prev_col = col
        next_col = col + 1
        key = (prev_col, next_col)
        if key in seen:
            continue
        seen.add(key)
        columns.append((_text(header), prev_col, next_col))
    return columns


class KnowledgeParser:
    name = "knowledge"

    def detect(self, workbook: Any) -> bool:
        ws = workbook.worksheets[0]
        header = " ".join(str(cell_value_with_merge(ws, row, col) or "") for row in (3, 4) for col in range(1, ws.max_column + 1))
        return "Проверка знаний" in header or "Фильтр" in header or "Инструктаж" in header

    def parse(self, workbook: Any) -> ParseResult:
        ws = workbook.worksheets[0]
        columns = _find_knowledge_columns(ws)
        rows: list[dict[str, Any]] = []
        for row_num in range(5, ws.max_row + 1):
            full_name = ws.cell(row_num, 2).value
            if not full_name:
                continue
            for check_type, prev_col, next_col in columns:
                next_date = as_date(ws.cell(row_num, next_col).value)
                if not next_date:
                    continue
                rows.append(
                    {
                        "row_number": row_num,
                        "kind": self.name,
                        "full_name": normalize_name(full_name),
                        "position": str(ws.cell(row_num, 3).value or ""),
                        "tab_number": str(ws.cell(row_num, 4).value or "") or None,
                        "check_type": check_type,
                        "previous_date": as_date(ws.cell(row_num, prev_col).value),
                        "next_date": next_date,
                    }
                )
        errors = self.validate(rows)
        return ParseResult(parser_name=self.name, rows=rows, errors=errors)

    def validate(self, rows: list[dict[str, Any]]) -> list[ParseError]:
        return [ParseError(row["row_number"], "Не найдена следующая дата проверки", row) for row in rows if not row.get("next_date")]
