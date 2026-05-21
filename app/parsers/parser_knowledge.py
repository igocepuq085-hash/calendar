from typing import Any

from app.parsers.base import ParseError, ParseResult, as_date, cell_value_with_merge
from app.services.people import normalize_name


KNOWLEDGE_COLUMNS = [
    ("Фильтр", 6, 7),
    ("Проверка знаний по ОТ", 8, 9),
    ("Проверка знаний по ЭБ", 10, 11),
    ("Проверка знаний Стропальщики", 12, 13),
    ("Проверка знаний Крановщики", 14, 15),
    ("Проверка знаний Люлька", 16, 17),
    ("Проверка знаний Высота", 18, 19),
    ("Противопожарный инструктаж", 20, 21),
    ("Инструктаж по ОТ", 22, 23),
]


class KnowledgeParser:
    name = "knowledge"

    def detect(self, workbook: Any) -> bool:
        ws = workbook.worksheets[0]
        header = " ".join(str(cell_value_with_merge(ws, row, col) or "") for row in (3, 4) for col in range(1, min(ws.max_column, 23) + 1))
        return "Проверка знаний" in header or "Фильтр" in header or "Инструктаж" in header

    def parse(self, workbook: Any) -> ParseResult:
        ws = workbook.worksheets[0]
        rows: list[dict[str, Any]] = []
        for row_num in range(5, ws.max_row + 1):
            full_name = ws.cell(row_num, 2).value
            if not full_name:
                continue
            for check_type, prev_col, next_col in KNOWLEDGE_COLUMNS:
                next_date = as_date(ws.cell(row_num, next_col).value)
                if not next_date:
                    continue
                rows.append(
                    {
                        "row_number": row_num,
                        "kind": self.name,
                        "full_name": normalize_name(full_name),
                        "position": str(ws.cell(row_num, 3).value or ""),
                        "check_type": check_type,
                        "previous_date": as_date(ws.cell(row_num, prev_col).value),
                        "next_date": next_date,
                    }
                )
        errors = self.validate(rows)
        return ParseResult(parser_name=self.name, rows=rows, errors=errors)

    def validate(self, rows: list[dict[str, Any]]) -> list[ParseError]:
        return [ParseError(row["row_number"], "Не найдена следующая дата проверки", row) for row in rows if not row.get("next_date")]
