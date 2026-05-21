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

    _replace_imported_employee_events(db, prepared, imported_kinds)

    for row, employee, kind in prepared:
        if kind == "work_schedule":
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
            db.add(
                KnowledgeCheck(
                    employee_id=employee.id,
                    check_type=row["check_type"],
                    previous_date=row.get("previous_date"),
                    next_date=row["next_date"],
                )
            )
            count += 1
        elif kind == "medical":
            db.add(
                MedicalCheck(
                    employee_id=employee.id,
                    previous_date=row.get("previous_date"),
                    next_date=row["next_date"],
                )
            )
            count += 1
    upload.status = UploadedFileStatus.imported
    upload.events_created = count
    upload.errors_count = len(result.errors)
    if "work_schedule" in imported_kinds:
        recalculate_all_kip_records(db)
    if "kip_journal" in imported_kinds:
        cleanup_kip_records(db)
    db.flush()
    return count


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
