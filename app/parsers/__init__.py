from app.parsers.base import ParseError, ParseResult
from app.parsers.parser_knowledge import KnowledgeParser
from app.parsers.parser_medical import MedicalParser
from app.parsers.parser_work_schedule import WorkScheduleParser

PARSERS = [
    WorkScheduleParser(),
    KnowledgeParser(),
    MedicalParser(),
]

__all__ = [
    "KnowledgeParser",
    "MedicalParser",
    "PARSERS",
    "ParseError",
    "ParseResult",
    "WorkScheduleParser",
]
