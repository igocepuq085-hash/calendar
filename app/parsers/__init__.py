from app.parsers.base import ParseError, ParseResult
from app.parsers.parser_kip_journal import KipJournalParser
from app.parsers.parser_knowledge import KnowledgeParser
from app.parsers.parser_medical import MedicalParser
from app.parsers.parser_work_schedule import WorkScheduleParser

PARSERS = [
    WorkScheduleParser(),
    KipJournalParser(),
    KnowledgeParser(),
    MedicalParser(),
]

__all__ = [
    "KipJournalParser",
    "KnowledgeParser",
    "MedicalParser",
    "PARSERS",
    "ParseError",
    "ParseResult",
    "WorkScheduleParser",
]
