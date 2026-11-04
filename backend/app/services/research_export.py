"""Lossless JSON plus flat CSV/XLSX sheets, with spreadsheet formula protection."""
import csv
import json
import re
from io import StringIO, BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from app.services.research_storage import photo_path


def cell(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    elif not isinstance(value, (str, int, float, bool)):
        value = str(value)
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        value = "'" + value
    return value


def sheets(details):
    output = {key: [] for key in ('runs', 'answers', 'attempts', 'photos', 'validations', 'audit')}
    for run in details:
        output['runs'].append({k: v for k, v in run.items() if k not in ('photos', 'attempts', 'audit', 'validation', 'manual_answers', 'app_answers')})
        for method in ('manual', 'app'):
            for code, answer in run.get(f'{method}_answers', {}).items():
                output['answers'].append({'run_id': run['id'], 'method': method, 'e_code': code, 'answer': answer})
        for kind in ('attempts', 'photos', 'audit'):
            output[kind].extend({'run_id': run['id'], **item} for item in run[kind])
        if run['validation']:
            output['validations'].append(run['validation'])
    return output


def write_export(target: Path, details: list, photos: list, include_photos: bool, excel: bool):
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('data.json', json.dumps(details, ensure_ascii=False, default=str, indent=2))
        tables = sheets(details)
        workbook = None
        if excel:
            from openpyxl import Workbook
            workbook = Workbook(write_only=True)
        for name, rows in tables.items():
            columns = list(dict.fromkeys(key for row in rows for key in row)) or ['run_id']
            stream = StringIO(newline='')
            writer = csv.writer(stream)
            writer.writerow(columns)
            sheet = workbook.create_sheet(name) if workbook else None
            if sheet is not None:
                sheet.append(columns)
            for row in rows:
                values = [cell(row.get(key)) for key in columns]
                writer.writerow(values)
                if sheet is not None:
                    # Excel limits cell text; full values remain in CSV/data.json.
                    sheet.append([re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)[:32767]
                                  if isinstance(value, str) else value for value in values])
            archive.writestr(f'{name}.csv', '\ufeff' + stream.getvalue())
        if workbook:
            stream = BytesIO()
            workbook.save(stream)
            archive.writestr('research.xlsx', stream.getvalue())
        manifest = []
        for photo in photos:
            path = photo_path(photo['storage_key'])
            suffix = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}[photo['mime_type']]
            name = f"photos/{photo['run_id']}/{photo['id']}{suffix}"
            exists = path.is_file()
            manifest.append({'id': str(photo['id']), 'run_id': str(photo['run_id']), 'sha256': photo['sha256'],
                             'file': name, 'available': exists, 'included': include_photos and exists})
            if include_photos and exists:
                archive.write(path, name)
        archive.writestr('photos_manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('README.txt', 'UTF-8 CSV; data.json хранит полные значения без ограничений Excel.\n'
            'NULL времени означает отсутствие замера, а не нулевое время.\n'
            'Recall/Precision рассчитываются вне приложения по подтверждённым эталонам.\n'
            'Порядок comparison: manual → app; времена зависят от повторного знакомства с этикеткой.\n'
            'Значения CSV/XLSX, похожие на формулы, экранированы апострофом.\n')
