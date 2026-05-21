from typing import Any

from app.parsers.base import ParseError, ParseResult, as_date, cell_value_with_merge
from app.services.people import normalize_name


class MedicalParser:
    name = "medical"

    def detect(self, workbook: Any) -> bool:
        ws = workbook.worksheets[0]
        header = " ".join(str(cell_value_with_merge(ws, row, col) or "") for row in (3, 4) for col in range(1, min(ws.max_column, 8) + 1))
        return "Мед. комиссия" in header

    def parse(self, workbook: Any) -> ParseResult:
        ws = workbook.worksheets[0]
        rows: list[dict[str, Any]] = []
        for row_num in range(5, ws.max_row + 1):
            full_name = ws.cell(row_num, 2).value
            next_date = as_date(ws.cell(row_num, 5).value)
            if not full_name or not next_date:
                continue
            rows.append(
                {
                    "row_number": row_num,
                    "kind": self.name,
                    "full_name": normalize_name(full_name),
                    "position": str(ws.cell(row_num, 3).value or ""),
                    "previous_date": as_date(ws.cell(row_num, 4).value),
                    "next_date": next_date,
                }
            )
        errors = self.validate(rows)
        return ParseResult(parser_name=self.name, rows=rows, errors=errors)

    def validate(self, rows: list[dict[str, Any]]) -> list[ParseError]:
        return [ParseError(row["row_number"], "Не найдена дата медкомиссии", row) for row in rows if not row.get("next_date")]
