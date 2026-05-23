from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ImportErrorRecord,
    KnowledgeCheck,
    KipRecord,
    MedicalCheck,
    UploadedFile,
    UploadedFileStatus,
    WorkShift,
)
from app.parsers import PARSERS, ParseResult
from app.services.calendar_notices import cleanup_expired_calendar_notices, create_calendar_notice
from app.services.kip import recalculate_all_kip_records, upsert_latest_kip_record
from app.services.people import get_or_create_employee


def detect_and_parse(path: str | Path) -> ParseResult:
    workbook = load_workbook(path, data_only=False)
    results: list[ParseResult] = []
    for parser in PARSERS:
        if parser.detect(workbook):
            results.append(parser.parse(workbook))
    if len(results) == 1:
        return results[0]
    if results:
        return ParseResult(
            parser_name="+".join(result.parser_name for result in results),
            rows=[row for result in results for row in result.rows],
            errors=[error for result in results for error in result.errors],
        )
    return ParseResult(parser_name="unknown", rows=[], errors=[])


def create_uploaded_file(db: Session, *, original_filename: str, stored_path: str, result: ParseResult) -> UploadedFile:
    upload = UploadedFile(
        original_filename=original_filename,
        stored_path=stored_path,
        parser_name=result.parser_name,
        status=UploadedFileStatus.previewed,
        rows_found=result.rows_found,
        employees_found=result.employees_found,
        events_created=result.events_found,
        errors_count=len(result.errors),
    )
    db.add(upload)
    db.flush()
    for error in result.errors:
        db.add(
            ImportErrorRecord(
                uploaded_file_id=upload.id,
                row_number=error.row_number,
                message=error.message,
                raw_data=json.dumps(error.raw_data, default=str, ensure_ascii=False) if error.raw_data else None,
            )
        )
    db.flush()
    return upload


def confirm_import(db: Session, upload: UploadedFile) -> int:
    result = detect_and_parse(upload.stored_path)
    count = 0
    imported_kinds: set[str] = set()
    prepared: list[tuple[dict[str, Any], Any, str]] = []
    for row in result.rows:
        employee = get_or_create_employee(
            db,
            full_name=row["full_name"],
            tab_number=row.get("tab_number"),
            position=row.get("position"),
            department="СЛХ",
        )
        kind = row.get("kind", result.parser_name)
        imported_kinds.add(kind)
        prepared.append((row, employee, kind))

    old_knowledge = _knowledge_snapshot(db, prepared) if "knowledge" in imported_kinds else {}
    old_medical = _medical_snapshot(db, prepared) if "medical" in imported_kinds else {}
    old_kip = _kip_snapshot(db) if "work_schedule" in imported_kinds else {}
    schedule_changes: dict[int, list[str]] = {}
    cleanup_expired_calendar_notices(db)
    _replace_imported_employee_events(db, prepared, imported_kinds)

    for row, employee, kind in prepared:
        if kind == "work_schedule":
            existing_shifts = db.scalars(
                select(WorkShift).where(
                    WorkShift.employee_id == employee.id,
                    WorkShift.shift_date == row["shift_date"],
                )
            ).all()
            old_shift = existing_shifts[0] if existing_shifts else None
            for existing in db.scalars(
                select(WorkShift).where(
                    WorkShift.employee_id == employee.id,
                    WorkShift.shift_date == row["shift_date"],
                )
            ):
                db.delete(existing)
            db.flush()
            db.add(
                WorkShift(
                    employee_id=employee.id,
                    shift_date=row["shift_date"],
                    shift_type=row["shift_type"],
                    start_datetime=row.get("start_datetime"),
                    end_datetime=row.get("end_datetime"),
                    raw_value=row.get("raw_value"),
                )
            )
            schedule_change = _work_shift_change_message(old_shift, row)
            if schedule_change:
                schedule_changes.setdefault(employee.id, []).append(schedule_change)
            count += 1
        elif kind == "kip_journal":
            upsert_latest_kip_record(
                db,
                employee_id=employee.id,
                last_kip_date=row["last_kip_date"],
                source=upload.original_filename,
                raw_value=row.get("raw_value"),
            )
            count += 1
        elif kind == "knowledge":
            old_next = old_knowledge.get((employee.id, row["check_type"]))
            db.add(
                KnowledgeCheck(
                    employee_id=employee.id,
                    check_type=row["check_type"],
                    previous_date=row.get("previous_date"),
                    next_date=row["next_date"],
                )
            )
            if old_next and old_next != row["next_date"]:
                create_calendar_notice(
                    db,
                    employee_id=employee.id,
                    title="⚠️ Изменение проверки",
                    description=f"{row['check_type']}: {old_next} -> {row['next_date']}",
                    source="import_knowledge",
                )
            count += 1
        elif kind == "medical":
            old_next = old_medical.get(employee.id)
            db.add(
                MedicalCheck(
                    employee_id=employee.id,
                    previous_date=row.get("previous_date"),
                    next_date=row["next_date"],
                )
            )
            if old_next and old_next != row["next_date"]:
                create_calendar_notice(
                    db,
                    employee_id=employee.id,
                    title="⚠️ Изменение медкомиссии",
                    description=f"Медицинская комиссия: {old_next} -> {row['next_date']}",
                    source="import_medical",
                )
            count += 1
    upload.status = UploadedFileStatus.imported
    upload.events_created = count
    upload.errors_count = len(result.errors)
    _create_schedule_change_notices(db, schedule_changes, upload.original_filename)
    if "work_schedule" in imported_kinds:
        recalculate_all_kip_records(db)
        _notice_kip_changes_after_schedule(db, old_kip)
    if "kip_journal" in imported_kinds:
        cleanup_kip_records(db)
    db.flush()
    return count


def _knowledge_snapshot(db: Session, prepared: list[tuple[dict[str, Any], Any, str]]) -> dict[tuple[int, str], Any]:
    keys = {(employee.id, row["check_type"]) for row, employee, kind in prepared if kind == "knowledge"}
    if not keys:
        return {}
    employee_ids = {employee_id for employee_id, _ in keys}
    rows = db.scalars(select(KnowledgeCheck).where(KnowledgeCheck.employee_id.in_(employee_ids))).all()
    return {(row.employee_id, row.check_type): row.next_date for row in rows if (row.employee_id, row.check_type) in keys}


def _medical_snapshot(db: Session, prepared: list[tuple[dict[str, Any], Any, str]]) -> dict[int, Any]:
    employee_ids = {employee.id for _, employee, kind in prepared if kind == "medical"}
    if not employee_ids:
        return {}
    rows = db.scalars(select(MedicalCheck).where(MedicalCheck.employee_id.in_(employee_ids))).all()
    return {row.employee_id: row.next_date for row in rows}


def _kip_snapshot(db: Session) -> dict[int, tuple[Any, Any]]:
    rows = db.scalars(select(KipRecord)).all()
    return {row.id: (row.planned_date, row.planned_start) for row in rows}


def _work_shift_change_message(old_shift: WorkShift | None, row: dict[str, Any]) -> str | None:
    if old_shift is None:
        return None
    changed = (
        old_shift.shift_type != row["shift_type"]
        or old_shift.start_datetime != row.get("start_datetime")
        or old_shift.end_datetime != row.get("end_datetime")
        or old_shift.raw_value != row.get("raw_value")
    )
    if not changed:
        return None
    return f"{row['shift_date']}: {old_shift.shift_type.value} -> {row['shift_type'].value}"


def _create_schedule_change_notices(db: Session, schedule_changes: dict[int, list[str]], source: str) -> None:
    for employee_id, changes in schedule_changes.items():
        if not changes:
            continue
        preview = "\n".join(changes[:8])
        if len(changes) > 8:
            preview += f"\nи еще {len(changes) - 8}"
        title = "⚠️ Изменение графика"
        if len(changes) == 1:
            title = f"⚠️ Изменение графика: {changes[0].split(':', 1)[0]}"
        create_calendar_notice(
            db,
            employee_id=employee_id,
            title=title,
            description=preview,
            source=f"import:{source}",
        )


def _notice_kip_changes_after_schedule(db: Session, old_kip: dict[int, tuple[Any, Any]]) -> None:
    if not old_kip:
        return
    rows = db.scalars(select(KipRecord)).all()
    for row in rows:
        previous = old_kip.get(row.id)
        if previous is None:
            continue
        old_date, old_start = previous
        if old_date == row.planned_date and old_start == row.planned_start:
            continue
        create_calendar_notice(
            db,
            employee_id=row.employee_id,
            title="⚠️ Изменение КИП",
            description=f"КИП по графику: {old_start or old_date or 'не назначен'} -> {row.planned_start or row.planned_date or 'нет смены'}",
            source="schedule_recalculate",
        )


def _replace_imported_employee_events(db: Session, prepared: list[tuple[dict[str, Any], Any, str]], imported_kinds: set[str]) -> None:
    employee_ids_by_kind: dict[str, set[int]] = {}
    for _, employee, kind in prepared:
        employee_ids_by_kind.setdefault(kind, set()).add(employee.id)

    if "knowledge" in imported_kinds:
        ids = employee_ids_by_kind.get("knowledge", set())
        if ids:
            for existing in db.scalars(select(KnowledgeCheck).where(KnowledgeCheck.employee_id.in_(ids))):
                db.delete(existing)
    if "medical" in imported_kinds:
        ids = employee_ids_by_kind.get("medical", set())
        if ids:
            for existing in db.scalars(select(MedicalCheck).where(MedicalCheck.employee_id.in_(ids))):
                db.delete(existing)
    db.flush()


def cleanup_kip_records(db: Session) -> int:
    deleted = 0
    employee_ids = [row[0] for row in db.execute(select(KipRecord.employee_id).distinct())]
    for employee_id in employee_ids:
        records = db.scalars(
            select(KipRecord)
            .where(KipRecord.employee_id == employee_id)
            .order_by(KipRecord.last_kip_date.desc(), KipRecord.id.desc())
        ).all()
        if len(records) <= 1:
            continue
        keep = records[0]
        for record in records[1:]:
            db.delete(record)
            deleted += 1
        upsert_latest_kip_record(
            db,
            employee_id=employee_id,
            last_kip_date=keep.last_kip_date,
            source=keep.source,
            raw_value=keep.raw_value,
        )
    db.flush()
    return deleted


def cleanup_duplicate_checks(db: Session) -> int:
    deleted = 0
    seen: set[tuple] = set()
    for row in db.scalars(select(KnowledgeCheck).order_by(KnowledgeCheck.employee_id, KnowledgeCheck.check_type, KnowledgeCheck.next_date, KnowledgeCheck.id)):
        key = (row.employee_id, row.check_type, row.previous_date, row.next_date)
        if key in seen:
            db.delete(row)
            deleted += 1
        else:
            seen.add(key)
    seen_medical: set[tuple] = set()
    for row in db.scalars(select(MedicalCheck).order_by(MedicalCheck.employee_id, MedicalCheck.next_date, MedicalCheck.id)):
        key = (row.employee_id, row.previous_date, row.next_date)
        if key in seen_medical:
            db.delete(row)
            deleted += 1
        else:
            seen_medical.add(key)
    db.flush()
    return deleted
