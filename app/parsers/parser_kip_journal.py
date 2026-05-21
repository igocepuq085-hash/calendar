from datetime import date
from typing import Any

from app.parsers.base import ParseError, ParseResult, cell_value_with_merge
from app.services.people import normalize_name, normalize_tab_number


class KipJournalParser:
    name = "kip_journal"

    def detect(self, workbook: Any) -> bool:
        text = " ".join(str(cell_value_with_merge(ws, 1, 1) or "") for ws in workbook.worksheets)
        return "КИП" in text or "График проведения КИП" in text

    def parse(self, workbook: Any) -> ParseResult:
        ws = workbook.worksheets[0]
        records: list[dict[str, Any]] = []
        errors: list[ParseError] = []
        year_blocks = self._year_blocks(ws)
        for row_num in range(4, ws.max_row + 1):
            full_name = ws.cell(row_num, 1).value
            if not full_name:
                continue
            if str(full_name).strip().lower() == "итого":
                break
            tab_number = normalize_tab_number(ws.cell(row_num, 3).value)
            for start_col, end_col, year in year_blocks:
                for col in range(start_col, min(end_col, ws.max_column) + 1):
                    month_value = ws.cell(3, col).value
                    raw = ws.cell(row_num, col).value
                    if raw in (None, "") or not isinstance(month_value, int):
                        continue
                    day = self._extract_day(raw)
                    if day is None:
                        errors.append(ParseError(row_num, "Не удалось определить день КИП", {"value": raw, "col": col}))
                        continue
                    try:
                        kip_date = date(year, int(month_value), day)
                    except ValueError:
                        errors.append(ParseError(row_num, "Некорректная дата КИП", {"value": raw, "month": month_value, "year": year}))
                        continue
                    records.append(
                        {
                            "row_number": row_num,
                            "kind": self.name,
                            "full_name": normalize_name(full_name),
                            "tab_number": tab_number,
                            "last_kip_date": kip_date,
                            "raw_value": str(raw),
                        }
                    )
        rows = self._latest_per_employee(records)
        errors.extend(self.validate(rows))
        return ParseResult(parser_name=self.name, rows=rows, errors=errors)

    def validate(self, rows: list[dict[str, Any]]) -> list[ParseError]:
        return [ParseError(row["row_number"], "Не найден работник", row) for row in rows if not row.get("full_name")]

    def _year_blocks(self, ws: Any) -> list[tuple[int, int, int]]:
        blocks: list[tuple[int, int, int]] = []
        for merged in ws.merged_cells.ranges:
            value = ws.cell(merged.min_row, merged.min_col).value
            if merged.min_row <= 1 <= merged.max_row and value and "КИП" in str(value):
                year = self._extract_year(value)
                if year:
                    blocks.append((merged.min_col, merged.max_col, year))
        if blocks:
            blocks.sort()
            return blocks
        return [(4, 15, 2023), (16, 27, 2023), (28, 39, 2023), (40, 51, 2023)]

    def _extract_year(self, value: Any) -> int | None:
        text = str(value)
        for year in range(2020, 2035):
            if str(year) in text:
                return year
        return None

    def _extract_day(self, value: Any) -> int | None:
        text = str(value)
        digits = ""
        for char in text:
            if char.isdigit():
                digits += char
            elif digits:
                break
        if not digits:
            return None
        day = int(digits)
        return day if 1 <= day <= 31 else None

    def _latest_per_employee(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in records:
            key = record.get("tab_number") or record["full_name"]
            current = latest.get(key)
            if current is None or record["last_kip_date"] > current["last_kip_date"]:
                latest[key] = record
        return list(latest.values())
