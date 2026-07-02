# Bigxday Security Audit - 2026-07-02

배포 직전 보안 감사 결과입니다. 비밀값, 토큰, 직원명, 생일 원문은 리포트에 포함하지 않았습니다.

## 요약

| Severity | Status | Finding |
| --- | --- | --- |
| Critical | 없음 | 즉시 악용 가능한 Critical 코드 취약점은 확인되지 않음 |
| High | 패치 완료 | PII/HR 데이터가 로그와 예외 문자열에 남을 수 있는 경로 제거 |
| High | 패치 완료 | `BIRTHDAY_CHANNEL_ID` 오설정으로 DM/잘못된 대상에 공지될 수 있는 경로 차단 |
| High | 패치 완료 | `pip-audit`가 탐지한 `starlette`, `python-dotenv` 취약 조합 업데이트 |
| Medium | 패치 완료 | Excel/CSV formula-like 셀 입력 방어 추가 |
| Medium | 조치 필요 | 서버 `.env`, `.env` 백업, HR Excel 파일 권한이 과도하게 열려 있음 |
| Low | 확인 완료 | Slack request signature 검증은 Socket Mode 구조상 해당 HTTP 엔드포인트 없음 |

## High

### H-1. PII/HR 데이터 로그 노출 가능성

- 위치:
  - `commands.py:170-186`
  - `commands.py:310`
  - `birthday.py:87`
  - `birthday.py:120-180`
  - `scheduler.py:17-21`
  - `utils.py:4-18`
- 근거:
  - 기존 `status` 명령 로그가 `birthday_record=%r`, `response=%r`, Slack user id, 생일 상태를 남길 수 있었습니다.
  - 생일 발송/DM 실패 로그가 Slack user id와 예외 원문을 포함할 수 있었습니다.
  - 스케줄러 실패 로그가 `str(error)`와 `exc_info=True`를 함께 기록해, 예외 메시지에 포함된 HR 데이터가 로그에 남을 수 있었습니다.
- 패치:
  - 로그는 trace id, boolean 상태, 예외 클래스/Slack error code 수준으로 축소했습니다.
  - Slack structured error가 없으면 `str(error)` 대신 예외 클래스명만 저장하도록 바꿨습니다.
- 검증:
  - `/opt/homebrew/bin/python3.11 -m pytest -q` -> `60 passed`

### H-2. Slack 채널 오설정으로 개인정보가 의도치 않은 대상에게 발송될 가능성

- 위치:
  - `config.py:29`
  - `config.py:50-54`
- 근거:
  - 기존에는 `BIRTHDAY_CHANNEL_ID`를 단순 필수 env로만 읽어 `D...` DM ID나 채널 이름 오설정을 시작 시점에 막지 못했습니다.
- 패치:
  - `BIRTHDAY_CHANNEL_ID`를 Slack 채널/프라이빗 채널 ID인 `C...` 또는 `G...` 패턴으로 제한했습니다.
- 검증:
  - `tests/test_config.py` 추가
  - `/opt/homebrew/bin/python3.11 -m pytest -q` -> `60 passed`

### H-3. Known CVE 의존성

- 위치:
  - `requirements.txt:2-6`
- 근거:
  - 초기 `pip-audit` 결과:
    - `python-dotenv==1.0.1`: `GHSA-mf9w-mj56-hr94`
    - FastAPI 경유 `starlette==0.41.3`: 7건
  - 서버 venv도 `fastapi==0.115.6`, `starlette==0.41.3`, `python-dotenv==1.0.1` 상태였습니다.
- 패치:
  - `FastAPI==0.139.0`
  - `python-dotenv==1.2.2`
  - `aiohttp==3.14.1` 명시 추가 (`slack_sdk.web.async_client` 런타임 import 재현성 보강)
- 검증:
  - `/opt/homebrew/bin/python3.11 -m pip_audit -r requirements.txt --cache-dir /private/tmp/pip-audit-cache` -> `No known vulnerabilities found`
  - `/opt/homebrew/bin/python3.11 -m pytest -q` -> `60 passed`

## Medium

### M-1. HR Excel/CSV formula-like 셀 방어 부재

- 위치:
  - `sync.py:230`
  - `sync.py:249-264`
  - `sync.py:281-293`
  - `sync.py:314-319`
- 근거:
  - 기존 Excel 로딩은 `data_only=True`라 수식 자체를 숨길 수 있었습니다.
  - email/birthday/기타 row 값이 `=`, `+`, `-`, `@`로 시작하는 경우 방어 없이 파싱 경로에 들어갈 수 있었습니다.
- 패치:
  - `data_only=False`로 수식 원문을 확인합니다.
  - row 전체에서 formula-like 텍스트를 감지해 해당 row를 스킵합니다.
  - invalid birthday 로그에서 원문 셀 값을 제거했습니다.
- 검증:
  - `tests/test_sync.py`에 Excel/CSV formula-like 방어 테스트 추가
  - `/opt/homebrew/bin/python3.11 -m pytest -q` -> `60 passed`

### M-2. 서버 파일 권한이 과도하게 열려 있음

- 위치:
  - `/home/bigxdata/birthday-bot/.env`: `664`
  - `/home/bigxdata/birthday-bot/.env.backup-20260622-135804`: `664`
  - `/home/bigxdata/birthday-bot/data/hr_birthdays.xlsx`: `644`
- 근거:
  - `.env`와 백업 파일은 토큰/DB credential을 담는 파일입니다.
  - HR Excel 파일은 직원 HR 데이터입니다.
  - 현재 권한은 group write/read 또는 world read 비트가 열려 있습니다. 홈 디렉터리가 `750`이라 완전 공개는 아니지만, 같은 group 또는 경로 접근 권한이 있는 사용자/프로세스에는 노출될 수 있습니다.
- 수정 제안:
  - 배포 사용자가 직접 실행:
    ```bash
    chmod 600 /home/bigxdata/birthday-bot/.env
    chmod 600 /home/bigxdata/birthday-bot/.env.backup-20260622-135804
    chmod 600 /home/bigxdata/birthday-bot/data/hr_birthdays.xlsx
    ```
  - 백업 파일이 더 필요 없으면 삭제 또는 안전한 비밀 저장소로 이동을 권장합니다. 삭제는 운영 변경이라 별도 승인 후 진행해야 합니다.

## Low / 확인 완료

### L-1. Slack request signature verification

- 위치:
  - `main.py:68-71`
  - Slack 등록은 `AsyncSocketModeHandler` 기반
- 근거:
  - 앱은 Slack HTTP 이벤트/커맨드 엔드포인트를 직접 열지 않고 Socket Mode를 사용합니다.
  - FastAPI HTTP endpoint는 `/health`만 확인되었습니다.
- 판단:
  - Slack request signature/timestamp 검증은 현재 구조에서는 적용 대상 엔드포인트가 없습니다.
  - HTTP Slack endpoint를 추가할 경우 `SigningSecret` 기반 verification과 timestamp replay 방어를 필수로 추가해야 합니다.

### L-2. 접근 제어

- 위치:
  - `commands.py:75-89`
  - `commands.py:222-224`
  - `home.py:101-115`
- 근거:
  - admin 명령은 `ADMIN_USER_IDS` 또는 Slack `is_admin`/`is_owner`로 게이트됩니다.
  - 일반 사용자 명령은 `status`, `optin`, `optout`, help만 실행합니다.
  - admin 응답은 `response_type="ephemeral"`입니다.
- 판단:
  - 우회 경로는 확인되지 않았습니다.

### L-3. DB/쿼리

- 위치:
  - `db.py`
- 근거:
  - 사용자 입력이 들어가는 SQL은 asyncpg placeholder (`$1`, `$2`, `$3`)를 사용합니다.
  - advisory lock은 `async with pool.acquire()`와 `finally` unlock으로 해제됩니다.
- 판단:
  - 문자열 포매팅 기반 SQL injection 경로는 확인되지 않았습니다.

### L-4. 스케줄러 중복 발송 방지

- 위치:
  - `scheduler.py:37-61`
  - `birthday.py:118-157`
  - `db.py:260-292`
  - `db.py:341-360`
- 근거:
  - APScheduler는 `coalesce=True`, `max_instances=1`로 설정되어 있습니다.
  - DB advisory lock과 `birthday_posts` primary key reservation으로 서버 재시작/중복 실행 시 중복 발송을 막습니다.
- 판단:
  - 중복 발송 방어는 확인되었습니다.
  - 단, `failed` 상태도 post record가 남아 자동 재시도를 막습니다. 이는 중복 방지 우선 정책으로 보이며, 실패 재처리 운영 절차는 별도 정의가 필요합니다.

### L-5. 시크릿 git 이력

- 근거:
  - 로컬/서버 모두 `.env` 자체가 git 이력에 커밋된 흔적은 확인되지 않았습니다.
  - 패턴 검색은 `.env.example`과 `README.md`의 placeholder만 매칭했습니다.
- 판단:
  - 실제 토큰/DB credential 커밋 흔적은 확인되지 않았습니다.

## 서버 상태 확인

- SSH: 지정된 명령으로만 접속했고 읽기 전용 작업만 수행했습니다.
- Health check: `http://127.0.0.1:8010/health` -> `{"status":"ok"}`
- Process: `uvicorn main:app --host 0.0.0.0 --port 8010` 실행 중
- Service file:
  - `/etc/systemd/system/birthday-bot.service`: `644 root root`
  - `EnvironmentFile=/home/bigxdata/birthday-bot/.env`
  - 서비스 파일 자체에는 secret 값 없음
- 현재 서버 코드:
  - `HEAD: 19d1e9a Fix manual birthdays being deactivated by HR sync`
  - 이번 보안 패치는 아직 서버에 배포되지 않았습니다.

## 최종 검증 명령

```bash
/opt/homebrew/bin/python3.11 -m pytest -q
/opt/homebrew/bin/python3.11 -m pip_audit -r requirements.txt --cache-dir /private/tmp/pip-audit-cache
```

결과:

- `60 passed in 1.78s`
- `No known vulnerabilities found`

## 배포 전 권장 순서

1. 이 패치를 커밋/배포합니다.
2. 서버에서 dependencies를 갱신합니다.
3. 서버 파일 권한을 `600`으로 조정합니다.
4. 사용자가 직접 서비스를 재시작합니다.
5. 재시작 후 `/health`와 최근 journal 로그를 재확인합니다.
