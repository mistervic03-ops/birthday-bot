import sys
import types
from datetime import datetime

from openpyxl import Workbook


errors = types.ModuleType("slack_sdk.errors")


class SlackApiError(Exception):
    pass


errors.SlackApiError = SlackApiError
async_client = types.ModuleType("slack_sdk.web.async_client")
async_client.AsyncWebClient = object
sys.modules["slack_sdk"] = types.ModuleType("slack_sdk")
sys.modules["slack_sdk.errors"] = errors
sys.modules["slack_sdk.web"] = types.ModuleType("slack_sdk.web")
sys.modules["slack_sdk.web.async_client"] = async_client

from sync import _excel_birthday_mm_dd, read_excel_rows


def test_excel_birthday_mm_dd_yy_uses_month_day_prefix() -> None:
    assert _excel_birthday_mm_dd("03-21-91") == "03-21"


def test_read_excel_rows_detects_employee_roster_headers(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([None, "이름", "생년월일", "이메일"])
    sheet.append([None, "홍길동", datetime(1991, 3, 21), "hong@example.com"])
    file_path = tmp_path / "employees.xlsx"
    workbook.save(file_path)

    rows, skipped = read_excel_rows(str(file_path))

    assert skipped == 0
    assert rows[0].email == "hong@example.com"
    assert (rows[0].birth_month, rows[0].birth_day) == (3, 21)
