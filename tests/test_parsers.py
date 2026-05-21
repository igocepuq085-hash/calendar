from datetime import datetime

from openpyxl import Workbook

from app.parsers.parser_knowledge import KnowledgeParser


def test_screen_parser_reads_all_non_medical_check_types() -> None:
    wb = Workbook()
    ws = wb.active
    ws.cell(3, 6).value = "Фильтр"
    ws.cell(3, 8).value = "Проверка знаний по ОТ"
    ws.cell(3, 10).value = "Проверка знаний по ЭБ"
    ws.cell(3, 12).value = "Проверка знаний Стропальщики"
    ws.cell(3, 14).value = "Проверка знаний Крановщики"
    ws.cell(3, 16).value = "Проверка знаний Люлька"
    ws.cell(3, 18).value = "Проверка знаний Высота"
    ws.cell(3, 20).value = "Противопожарный инструктаж"
    ws.cell(3, 22).value = "Инструктаж по ОТ"
    ws.cell(5, 2).value = "Иванов И.И."
    ws.cell(5, 3).value = "Машинист тепловоза"
    for col in [7, 9, 11, 13, 15, 17, 19, 21, 23]:
        ws.cell(5, col).value = datetime(2026, 6, 1)

    result = KnowledgeParser().parse(wb)

    assert result.rows_found == 9
    assert {row["check_type"] for row in result.rows} == {
        "Фильтр",
        "Проверка знаний по ОТ",
        "Проверка знаний по ЭБ",
        "Проверка знаний Стропальщики",
        "Проверка знаний Крановщики",
        "Проверка знаний Люлька",
        "Проверка знаний Высота",
        "Противопожарный инструктаж",
        "Инструктаж по ОТ",
    }

