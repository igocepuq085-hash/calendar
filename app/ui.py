from datetime import date

from app.models import KipStatus, ShiftType


def date_state(value: date | None, warning_days: int = 14) -> str:
    if value is None:
        return "muted"
    today = date.today()
    if value < today:
        return "danger"
    if (value - today).days <= warning_days:
        return "warning"
    return "success"


def state_rank(state: str) -> int:
    return {"danger": 3, "warning": 2, "success": 1, "muted": 0, "inactive": -1}.get(state, 0)


def access_card_class(state: str) -> str:
    return {
        "danger": "border-red-300 bg-red-50",
        "warning": "border-amber-300 bg-amber-50",
        "success": "border-emerald-200 bg-white",
        "inactive": "border-slate-200 bg-slate-100 opacity-70",
        "muted": "border-slate-200 bg-white",
    }.get(state, "border-slate-200 bg-white")


def access_badge_class(state: str) -> str:
    return {
        "danger": "bg-red-100 text-red-800 border-red-200",
        "warning": "bg-amber-100 text-amber-800 border-amber-200",
        "success": "bg-emerald-100 text-emerald-800 border-emerald-200",
        "inactive": "bg-slate-200 text-slate-700 border-slate-300",
        "muted": "bg-slate-100 text-slate-700 border-slate-200",
    }.get(state, "bg-slate-100 text-slate-700 border-slate-200")


def access_state_label(state: str) -> str:
    return {
        "danger": "Есть просрочка",
        "warning": "Скоро проверка",
        "success": "Допуск в норме",
        "inactive": "Неактивен",
        "muted": "Нет данных",
    }.get(state, "")


def date_badge_class(value: date | None) -> str:
    state = date_state(value)
    return {
        "danger": "bg-red-50 text-red-800 border-red-200",
        "warning": "bg-amber-50 text-amber-800 border-amber-200",
        "success": "bg-emerald-50 text-emerald-800 border-emerald-200",
        "muted": "bg-slate-50 text-slate-600 border-slate-200",
    }[state]


def date_state_label(value: date | None) -> str:
    state = date_state(value)
    return {
        "danger": "Просрочено",
        "warning": "Скоро",
        "success": "Норма",
        "muted": "Нет даты",
    }[state]


def shift_class(shift_type: ShiftType | str | None) -> str:
    value = shift_type.value if isinstance(shift_type, ShiftType) else shift_type
    return {
        "day": "bg-emerald-100 text-emerald-900 border-emerald-200",
        "night": "bg-sky-100 text-sky-900 border-sky-200",
        "off": "bg-slate-50 text-slate-500 border-slate-200",
        "vacation": "bg-violet-100 text-violet-900 border-violet-200",
        "sick": "bg-red-100 text-red-900 border-red-200",
        "training": "bg-amber-100 text-amber-900 border-amber-200",
        "unknown": "bg-zinc-100 text-zinc-700 border-zinc-200",
    }.get(value or "", "bg-zinc-100 text-zinc-700 border-zinc-200")


def shift_label(shift_type: ShiftType | str | None) -> str:
    value = shift_type.value if isinstance(shift_type, ShiftType) else shift_type
    return {
        "day": "День",
        "night": "Ночь",
        "off": "Вых",
        "vacation": "Отп",
        "sick": "Бол",
        "training": "Учёба",
        "unknown": "?",
    }.get(value or "", "")


def kip_status_class(status: KipStatus | str | None) -> str:
    value = status.value if isinstance(status, KipStatus) else status
    return {
        "planned": "bg-emerald-50 text-emerald-800 border-emerald-200",
        "completed": "bg-slate-50 text-slate-700 border-slate-200",
        "conflict": "bg-amber-50 text-amber-800 border-amber-200",
        "overdue": "bg-red-50 text-red-800 border-red-200",
    }.get(value or "", "bg-slate-50 text-slate-700 border-slate-200")


def kip_status_label(status: KipStatus | str | None) -> str:
    value = status.value if isinstance(status, KipStatus) else status
    return {
        "planned": "Запланирован",
        "completed": "Выполнен",
        "conflict": "Нет смены",
        "overdue": "Просрочен",
    }.get(value or "", "")
