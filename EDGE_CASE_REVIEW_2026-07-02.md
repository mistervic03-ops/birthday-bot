# Bigxday Edge Case Review - 2026-07-02

배포 직전 동작 엣지케이스 리뷰입니다. 이번 라운드는 코드 수정 없이 코드/문서/테스트 근거만 정리했습니다.

## 요약

| 분류 | 항목 |
| --- | --- |
| 확인된 문제 | 서버가 스케줄 시간대에 내려가 있으면 그날 sync/send 만회 로직이 없음 |
| 확인된 문제 | 08:50 sync가 길어지면 09:00 발송과 겹쳐 partial/mixed 데이터 기준으로 발송될 수 있음 |
| 확인된 문제 | `/birthday admin sync`와 스케줄 sync가 동시에 실행될 수 있고 sync 전역 lock/transaction이 없음 |
| 확인된 문제 | channel post 전후 프로세스 종료 시 `sending` row가 남아 자동 재발송을 영구 차단할 수 있음 |
| 확인된 문제 | channel post 성공 후 DM 실패는 `sent` 상태로 남고 DM 재시도/상태 기록이 없음 |
| 확인된 문제 | DM에서 `SlackApiError`가 아닌 예외가 나면 이후 생일자 처리가 중단될 수 있음 |
| 정상 처리됨 | 2월 29일생은 평년 2월 28일 대상에 포함됨 |
| 정상 처리됨 | 생일자 0명인 날은 no-op으로 종료됨 |
| 확인 필요 | HR sync 실패 시 기존 DB 기준 발송이 의도인지 |
| 확인 필요 | admin manual birthday가 다음 HR sync에서 HR 값으로 덮이는 것이 의도인지 |

## 확인된 문제

### 1. 서버가 스케줄 시간대에 내려가 있으면 그날 sync/send 만회 로직이 없음

- 상태: 확인된 문제
- 근거:
  - scheduler는 메모리 기반 `AsyncIOScheduler`만 생성합니다. 별도 persistent job store나 missed-run replay 로직이 없습니다: `scheduler.py:39-45`.
  - sync job은 매일 `08:50`, send job은 매일 `09:00` cron으로만 등록됩니다: `scheduler.py:47-69`.
  - 앱 시작 시 scheduler를 만들고 `start()`만 호출합니다: `main.py:54-55`.
  - README도 `08:50`, `09:00` 스케줄만 설명하고 만회 실행은 설명하지 않습니다: `README.md:103-108`.
- 재현/시나리오:
  - 프로세스가 08:50~09:05 동안 내려가 있으면 HR sync가 실행되지 않습니다.
  - 프로세스가 09:00~09:15 동안 내려가 있으면 생일 발송이 실행되지 않습니다.
  - 코드상 “오늘 못 보낸 생일을 다음 기동/다음날 찾아서 보낸다”는 루프가 없습니다.
- 영향:
  - 배포/재시작이 스케줄 시간과 겹치면 그날 생일 공지가 조용히 누락될 수 있습니다.

### 2. 08:50 sync가 길어지면 09:00 발송과 겹쳐 partial/mixed 데이터 기준으로 발송될 수 있음

- 상태: 확인된 문제
- 근거:
  - sync와 send는 별도 job이고 `max_instances=1`은 각 job별 중복만 막습니다: `scheduler.py:47-69`.
  - sync는 row마다 Slack lookup 후 즉시 DB upsert합니다: `sync.py:62-87`.
  - sync 마지막에 HR에서 빠진 기존 row를 inactive 처리합니다: `sync.py:89-95`, `db.py:167-184`.
  - send는 09:00에 현재 DB의 active birthday를 조회합니다: `birthday.py:101-109`, `db.py:228-255`.
  - sync 전체를 감싸는 transaction이나 sync/send 전역 lock이 없습니다.
- 재현/시나리오:
  - Slack lookup rate limit으로 sync가 09:00 이후까지 지연됩니다. rate limit 시 sleep 후 1회 재시도합니다: `sync.py:166-174`.
  - 09:00 send가 먼저 실행되면 이미 upsert된 일부 신규/수정 row와 아직 inactive 처리되지 않은 기존 row가 섞인 상태로 조회될 수 있습니다.
- 영향:
  - 입사/퇴사/오탈자 수정이 있는 날, 09:00 발송 기준 데이터가 “완료된 HR sync 결과”가 아닐 수 있습니다.

### 3. 수동 sync와 스케줄 sync가 동시에 실행될 수 있음

- 상태: 확인된 문제
- 근거:
  - `/birthday admin sync`는 같은 `sync_hr_sheet()`를 직접 실행합니다: `commands.py:260-272`.
  - 스케줄 sync도 같은 `sync_hr_sheet()`를 실행합니다: `scheduler.py:24-27`, `scheduler.py:47-58`.
  - 두 경로를 막는 DB lock, app lock, scheduler pause, idempotency key가 없습니다.
  - sync는 row 단위 upsert 후 마지막에 missing HR row를 inactive 처리합니다: `sync.py:78-90`.
- 재현/시나리오:
  - 운영자가 08:49~08:50 사이 `/birthday admin sync`를 실행하고, 08:50 스케줄 sync가 겹칩니다.
  - 두 sync가 같은 파일이면 최종 결과는 대체로 같겠지만 Slack API/DB 호출이 중복됩니다.
  - 실행 중 HR 파일이 교체되거나 한쪽이 partial failure를 만나면 마지막으로 끝난 sync의 inactive 처리 결과가 최종 상태가 됩니다.
- 영향:
  - 발송 직전 DB 상태가 실행 순서에 따라 달라질 수 있습니다.

### 4. 예약 후 프로세스 종료 시 stale `sending` row가 자동 재발송을 영구 차단할 수 있음

- 상태: 확인된 문제
- 근거:
  - 발송 전 `reserve_birthday_post()`가 먼저 `birthday_posts`에 `status='sending'` row를 만듭니다: `birthday.py:134`, `db.py:281-293`.
  - 그 뒤 Slack channel post를 호출합니다: `birthday.py:139-144`.
  - 다음 실행에서는 status와 관계없이 기존 row가 있으면 skip합니다: `birthday.py:123-124`, `db.py:258-272`.
  - README도 existing row가 있으면 status와 관계없이 자동 재발송을 막는다고 설명합니다: `README.md:184-194`.
- 재현/시나리오:
  - 프로세스가 `reserve_birthday_post()` 성공 직후, Slack API 호출 전 종료됩니다.
  - DB에는 `sending` row가 남지만 Slack 메시지는 아직 전송되지 않았습니다.
  - 이후 같은 `(slack_user_id, birthday_date)`는 `has_birthday_post()` 때문에 계속 skip됩니다.
- 영향:
  - 실제 공지는 나가지 않았는데 발송된 것으로 간주되어 조용히 누락될 수 있습니다.

### 5. Channel post 성공 후 DM 실패는 `sent`로 남고 재시도/상태 기록이 없음

- 상태: 확인된 문제
- 근거:
  - channel post 성공 후 `mark_birthday_post_sent()`로 `status='sent'`가 기록됩니다: `birthday.py:160-166`, `db.py:296-316`.
  - 그 뒤 DM을 보냅니다: `birthday.py:173`.
  - DM 실패 처리 함수는 warning만 남기고 DB 상태를 바꾸지 않습니다: `birthday.py:176-180`.
  - `birthday_posts` schema에는 channel post 중심의 `channel_ts`, `error`, `status`만 있고 DM 상태 필드는 없습니다: `db.py:30-39`.
- 재현/시나리오:
  - channel post는 성공합니다.
  - DM이 Slack API error로 실패합니다.
  - DB는 이미 `sent`라 다음 실행에서 자동 재시도되지 않습니다.
- 영향:
  - 공개 채널 공지는 나갔지만 당사자 DM은 누락될 수 있고, 운영자가 `/birthday admin log`만 보면 channel 기준 `sent`로 보입니다.

### 6. DM에서 `SlackApiError`가 아닌 예외가 나면 이후 생일자 처리가 중단될 수 있음

- 상태: 확인된 문제
- 근거:
  - channel post는 `except Exception`으로 잡습니다: `birthday.py:145-158`.
  - DM은 `except SlackApiError`만 잡습니다: `birthday.py:176-180`.
  - `send_today_birthdays()` 안에서 `send_birthday_dm()` 호출은 별도 try/except로 감싸져 있지 않습니다: `birthday.py:173`.
  - scheduler wrapper는 job 단위 예외만 잡습니다: `scheduler.py:17-21`.
- 재현/시나리오:
  - 여러 명 생일자 중 첫 번째의 channel post와 `sent` 기록이 성공합니다.
  - DM 호출에서 network timeout 같은 비-SlackApiError 예외가 발생합니다.
  - 예외가 `send_today_birthdays()` 밖으로 전파되어 job이 실패하고, loop 뒤쪽 생일자는 처리되지 않습니다.
- 영향:
  - 일부 대상은 조용히 누락될 수 있습니다. 앞선 대상은 `sent`로 남아 DM 재시도도 되지 않습니다.

### 7. Channel post 성공 후 DB `sent` 업데이트 실패 시 DM이 보내지지 않고 `sending`이 남음

- 상태: 확인된 문제
- 근거:
  - channel post 성공 후 `mark_birthday_post_sent()`를 호출합니다: `birthday.py:160-166`.
  - 이 DB 업데이트가 실패하면 critical 로그 후 `continue`합니다: `birthday.py:167-171`.
  - DM 호출은 그 뒤에 있으므로 실행되지 않습니다: `birthday.py:173`.
  - 기존 row가 있으면 다음 실행은 skip합니다: `birthday.py:123-124`.
- 재현/시나리오:
  - Slack channel message는 실제로 게시됩니다.
  - DB update가 timeout/failure로 실패합니다.
  - DB row는 `sending`으로 남고 DM은 발송되지 않습니다.
- 영향:
  - 공개 공지는 보이지만 DM은 누락됩니다. 자동 복구는 없습니다.

### 8. HR sync partial abort 후 당일 발송은 부분 반영 DB를 기준으로 돌 수 있음

- 상태: 확인된 문제
- 근거:
  - sync는 row마다 즉시 upsert합니다: `sync.py:78-87`.
  - Slack lookup fatal error가 나면 batch abort를 반환하고 soft-delete는 하지 않습니다: `sync.py:64-70`, `sync.py:176-177`.
  - 이 흐름은 테스트로도 고정되어 있습니다: `tests/test_sync.py:175-219`.
  - send job은 sync 성공 여부를 확인하지 않고 DB를 조회합니다: `birthday.py:101-109`.
- 재현/시나리오:
  - 08:50 sync가 10명 중 3명 upsert 후 fatal Slack API error로 abort합니다.
  - 09:00 send는 그 3명만 새 HR 값이고 나머지는 기존 DB 값인 상태로 실행됩니다.
- 영향:
  - 그날 발송 기준 데이터가 “이전 전체 DB”도 “새 HR 전체 결과”도 아닌 부분 반영 상태가 될 수 있습니다.

### 9. HR Excel에 같은 Slack user로 해석되는 row가 중복되면 마지막 upsert가 조용히 이김

- 상태: 확인된 문제
- 근거:
  - `sync_hr_sheet()`는 각 row를 순서대로 처리하며 중복 email/Slack user 검사를 하지 않습니다: `sync.py:62-87`.
  - `birthdays.slack_user_id`는 primary key입니다: `db.py:14-22`.
  - `upsert_birthday()`는 conflict 시 생일/email/source를 새 값으로 덮습니다: `db.py:147-158`.
- 재현/시나리오:
  - HR Excel에 같은 Slack user로 resolve되는 row가 두 개 있고 생일 값이 다릅니다.
  - 앞 row가 upsert된 뒤 뒤 row가 동일 primary key를 덮습니다.
- 영향:
  - HR 파일 오염/중복 입력이 조용히 마지막 값으로 수렴합니다. 운영자가 sync 결과 count만 보면 중복을 알기 어렵습니다.

## 정상 처리됨

### 10. 2월 29일생은 평년 2월 28일에 포함됨

- 상태: 정상 처리됨
- 근거:
  - 평년 2월 28일이면 target에 `(2, 29)`를 추가합니다: `birthday_dates.py:19-23`.
  - sender는 이 target list로 DB를 조회합니다: `birthday.py:45-61`, `birthday.py:101-109`.
  - 테스트가 있습니다: `tests/test_birthday.py:20-25`.
- 동작:
  - 2025-02-28 같은 평년 2월 28일에는 2월 28일생과 2월 29일생이 모두 대상입니다.
  - 윤년 2월 28일에는 2월 29일생을 포함하지 않고, 2월 29일 당일에 처리됩니다.

### 11. 금요일에는 토/일 생일을 미리 발송하고 중복 방지 날짜는 실제 생일 날짜를 사용함

- 상태: 정상 처리됨
- 근거:
  - 금요일이면 target date에 토요일/일요일을 추가합니다: `birthday.py:45-49`.
  - weekend message는 target date 기준 요일 문구를 사용합니다: `birthday.py:64-80`.
  - reservation key에는 실제 `birthday_date`를 사용합니다: `birthday.py:116-134`.
  - 테스트가 있습니다: `tests/test_birthday_sending.py:38-84`.
- 동작:
  - 금요일 조기 발송 후 토/일 실제 날짜 실행에서는 같은 `(slack_user_id, birthday_date)` row 때문에 중복 발송이 차단됩니다.

### 12. 생일자가 0명인 날은 정상 no-op

- 상태: 정상 처리됨
- 근거:
  - DB 조회 결과 `rows`를 순회하는 구조라 빈 list면 아무 Slack 호출 없이 종료됩니다: `birthday.py:106-114`.
  - 테스트에서 빈 결과일 때 Slack message가 없음을 확인합니다: `tests/test_birthday_sending.py:87-101`.
- 동작:
  - 대상자가 없으면 상태 row도 만들지 않고 정상 종료됩니다.

### 13. Channel post 실패는 `failed`로 기록하고 DM은 보내지 않음

- 상태: 정상 처리됨
- 근거:
  - channel post 예외 시 `mark_birthday_post_failed()`를 호출합니다: `birthday.py:145-158`, `db.py:319-337`.
  - 해당 경로에서는 DM을 호출하지 않고 다음 row로 넘어갑니다: `birthday.py:145-158`, `birthday.py:173`.
  - 테스트가 있습니다: `tests/test_birthday_sending.py:135-172`.
- 동작:
  - channel 공지가 실패한 경우 당사자 DM만 나가는 불일치는 방지됩니다.
  - 다만 기존 row가 있으면 재시도는 차단됩니다. 이 정책은 README에 수동 복구 절차로 설명되어 있습니다: `README.md:194-206`.

### 14. Slack users_not_found는 해당 HR row만 skip하고 batch는 계속 진행

- 상태: 정상 처리됨
- 근거:
  - `users_not_found`는 empty `SlackLookupResult()`로 처리합니다: `sync.py:161-164`.
  - caller는 `slack_user_id is None`이면 skipped count를 올리고 계속 진행합니다: `sync.py:72-76`.
  - 문서에도 같은 정책이 있습니다: `ARCHITECTURE.md:79-83`.
- 동작:
  - 퇴사/미가입 이메일 1개 때문에 전체 sync가 멈추지는 않습니다.

## 확인 불가 / 질문

### Q1. sync 실패 당일 발송 기준은 “기존 DB로 계속 발송”이 맞습니까?

- 코드상 사실:
  - sync job 실패는 `safe_job()`에서 job 실패 로그로 끝나고 send job 실행을 막지 않습니다: `scheduler.py:17-35`.
  - send job은 sync 성공 여부나 sync timestamp를 확인하지 않고 DB를 조회합니다: `birthday.py:101-109`.
- 확인 불가 이유:
  - 코드/문서만으로는 “sync가 실패한 날은 기존 DB라도 발송해야 한다”가 제품 의도인지, “발송을 중단해야 한다”가 의도인지 판단할 수 없습니다.

### Q2. admin manual birthday가 다음 HR sync에서 HR 값으로 덮이는 것이 의도입니까?

- 코드상 사실:
  - admin set은 `source="manual"`로 upsert합니다: `commands.py:287-318`.
  - HR sync는 같은 `slack_user_id`에 대해 `source="hr"`로 upsert합니다: `sync.py:78-85`.
  - conflict 시 birth month/day/email/source가 모두 EXCLUDED 값으로 덮입니다: `db.py:147-158`.
- 확인 불가 이유:
  - manual set이 “임시 override”인지 “HR보다 우선하는 override”인지 정책 문서가 없습니다.

### Q3. DM 실패를 운영상 실패로 볼지, channel post 성공이면 성공으로 볼지 정책 확인이 필요합니다.

- 코드상 사실:
  - channel post 성공 후 DB는 `sent`가 됩니다: `birthday.py:160-166`.
  - DM 실패는 DB에 남지 않습니다: `birthday.py:176-180`.
- 확인 불가 이유:
  - 문서에는 “channel announcements and DMs”라고 설명하지만, 성공 기준이 channel인지 channel+DM인지 명확하지 않습니다: `README.md:103-106`.

### Q4. stale `sending` row 자동 복구를 하지 않는 정책이 지금도 맞습니까?

- 코드상 사실:
  - 기존 row가 있으면 status와 관계없이 skip합니다: `birthday.py:123-124`, `db.py:258-272`.
  - README는 manual recovery를 안내합니다: `README.md:202-206`.
- 확인 불가 이유:
  - 중복 방지 우선 정책은 문서화되어 있지만, 프로세스 종료가 Slack API 호출 전인지 후인지 구분할 수 없는 stale `sending`까지 계속 수동 처리하는 것이 운영 기대와 맞는지 판단할 수 없습니다.

### Q5. `/birthday admin test-birthday`와 `test-weekend`가 실제 운영 채널에 post하는 것이 의도입니까?

- 코드상 사실:
  - test-birthday는 실제 `settings.birthday_channel_id`와 target user DM에 post합니다: `commands.py:625-639`.
  - test-weekend도 실제 channel과 DM에 post합니다: `commands.py:642-661`.
  - 테스트 명령은 birthday_posts duplicate check/record를 거치지 않는다는 테스트가 있습니다: `tests/test_commands.py:591-622`, `tests/test_commands.py:725-756`.
- 확인 불가 이유:
  - 이름은 test지만 실제 운영 채널 발송입니다. 문서도 “send a test birthday announcement and DM”이라고만 되어 있어, 별도 테스트 채널을 써야 하는 운영 정책인지 알 수 없습니다: `README.md:160-161`.

## 권장 검증 시나리오

패치 여부를 결정하기 전에 아래 시나리오를 로컬 fake client 또는 staging Slack channel에서 재현하는 것을 권장합니다.

1. 08:50 sync가 09:00 이후까지 지연되는 상황:
   - `users_lookupByEmail` fake client에 sleep을 넣고 send job을 동시에 호출합니다.
   - 기대 데이터 기준이 old DB인지 partial DB인지 확인합니다.

2. reserve 직후 프로세스 종료:
   - `reserve_birthday_post()` 성공 후 예외/종료를 주입합니다.
   - 다음 `send_today_birthdays()`가 `sending` row 때문에 skip하는지 확인합니다.

3. channel 성공 후 DM 실패:
   - channel post는 성공, DM post는 `SlackApiError` 또는 timeout을 던지게 합니다.
   - DB status와 `/birthday admin log` 표시가 운영자가 기대하는 수준인지 확인합니다.

4. admin sync와 scheduled sync 동시 실행:
   - 같은 HR 파일과 서로 다른 HR 파일 두 케이스로 실행 순서별 최종 DB 상태를 확인합니다.

5. HR duplicate row:
   - 같은 Slack user로 resolve되는 두 row를 넣고 최종 생일 값과 sync result count를 확인합니다.
