# Pop Up Events 기능 추가 플랜

- **작성:** Opus 4.7
- **날짜:** 2026-05-09
- **대상:** kproxy-go (mateinkorea-go) 프로젝트
- **목적:** 팝업 이벤트(예: "Cortis - Greengreen pop up")를 등록하고 그에 속한 상품들을 엑셀로 일괄 업로드하여, 메인 고객 페이지(/)에 배너 → 클릭 시 상품 세로 리스트 페이지로 이동하는 기능 구현

## 사용자 결정 사항 (확정)

1. **스키마:** 완전히 새 `popup_events` / `popup_products` 테이블 (기존 go_campaigns와 분리)
2. **표시 위치:** 메인 고객 페이지(/) 상단
3. **Pop Up 필드:** 제목 + 그룹명 + 기간 (배너 이미지 / 외부 링크 / 설명은 미선택 → 미사용)
4. **Products 입력:** 엑셀 일괄 업로드 (양식: KPop_PopUp_Order_Sheet_2.xlsx 기준)

## 컨텍스트

- 현재 admin/dashboard.html은 임시 placeholder ("+ New Pop Up", "+ New Products" 버튼이 `coming soon` 텍스트로 연결)
- 엑셀 양식: 시트 1개, 헤더 row 1, 데이터 row 2~. 컬럼 B(No)/C(GROUP)/D(Product Name)/E(Option)/F(Price KRW)/G(URL)
- openpyxl은 requirements.txt에 이미 포함됨 (3.1.5)
- 기존 admin/import 엔드포인트(app.py:587)에서 openpyxl 사용 패턴 참고 가능
- 기존 home.html은 hero → category tabs → grid 구조 (Pop Up 배너 영역은 hero 아래 / category tabs 위에 추가 권장)

## DB 스키마 (Supabase SQL Editor 직접 실행)

```sql
CREATE SEQUENCE IF NOT EXISTS popup_events_id_seq;
CREATE TABLE IF NOT EXISTS popup_events (
  id          bigint NOT NULL DEFAULT nextval('popup_events_id_seq'),
  title       text   NOT NULL,
  group_name  text   NOT NULL,
  start_date  date,
  end_date    date,
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS popup_events_active_idx ON popup_events (is_active, end_date);

CREATE SEQUENCE IF NOT EXISTS popup_products_id_seq;
CREATE TABLE IF NOT EXISTS popup_products (
  id           bigint NOT NULL DEFAULT nextval('popup_products_id_seq'),
  popup_id     bigint NOT NULL REFERENCES popup_events(id) ON DELETE CASCADE,
  no           integer,
  group_name   text,
  product_name text NOT NULL,
  option_name  text,
  price_krw    numeric,
  url          text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS popup_products_popup_idx ON popup_products (popup_id, no);
```

## 작업 목록

### Task A — DB 스키마 적용

- 위 SQL을 사용자가 https://supabase.com/dashboard/project/zcdwccdgmpbwzccyfeno/sql/new 에서 실행
- 코드 작성 시작 전 선행

### Task B — 백엔드 라우트 (app.py)

**Pop Up 관리:**
- `GET /admin/popup/new` → 폼 (title, group_name, start_date, end_date)
- `POST /admin/popup/new` → insert
- `GET /admin/popups` → 등록된 popup 목록 (관리/편집/삭제)
- `GET /admin/popup/{id}/edit` → 폼 prefill
- `POST /admin/popup/{id}/edit` → update
- `POST /admin/popup/{id}/delete` → delete (cascade)
- `POST /admin/popup/{id}/toggle-active` → is_active 토글

**Products 관리 (엑셀 업로드):**
- `GET /admin/products/new` → popup picker + 엑셀 업로드 폼 (popup 선택 후 파일 업로드)
- `POST /admin/products/new` → 파일 파싱 + insert. 응답 페이지에 등록된 row 수 / 에러 표시
- `GET /admin/popup/{id}/products` → 해당 popup의 상품 목록 (관리/삭제용)
- `POST /admin/popup/{id}/products/{pid}/delete` → 단일 row 삭제

**고객 노출:**
- `GET /` → 활성 popup_events 조회 + home.html 컨텍스트로 전달
- `GET /popup/{id}` → 상품 세로 리스트 페이지 (popup_detail.html)

**기존 admin/popup/new, admin/products/new placeholder 라우트(app.py:457 인근)는 제거.**

### Task C — Excel 파싱 헬퍼

- `_parse_popup_xlsx(file_bytes) -> List[Dict]`: 첫 번째 시트의 row 2 이후를 읽어 `[{no, group_name, product_name, option_name, price_krw, url}]` 반환
- 빈 행(product_name 없음)은 skip
- price 문자열이면 숫자만 추출 후 numeric 변환
- 시트명은 무시 (사용자 결정)
- 헤더 위치 검증: row 1의 D열이 "Product Name" 또는 "상품명" 포함되는지 확인 (가벼운 sanity check)
- 실패 케이스: 시트 0개, 데이터 0행, 모든 행이 빈 product_name → 사용자에게 명확한 에러 메시지

### Task D — 템플릿

- `templates/admin/popup_form.html` (신규/편집 공용)
- `templates/admin/popups.html` (관리 목록)
- `templates/admin/popup_products.html` (popup별 상품 관리)
- `templates/admin/popup_upload.html` (엑셀 업로드 폼)
- `templates/popup_detail.html` (고객 페이지: 세로 상품 리스트)
- `templates/home.html` 수정: hero 아래 / category tabs 위에 popup 배너 섹션 추가

### Task E — Home 배너 표시

- 활성 popup만 노출 (`is_active = true AND (end_date IS NULL OR end_date >= today)`)
- 배너 카드 클릭 → `/popup/{id}` 이동
- 배너 표시 정보: 제목, 그룹명, 기간 (start_date ~ end_date 또는 "Open now")
- 활성 popup 0건이면 섹션 자체를 hide

### Task F — 대시보드 링크 정리

- 기존 placeholder 라우트(app.py:457 인근) 제거
- dashboard.html의 "+ New Pop Up", "+ New Products" 링크는 유지 (이미 작업됨)
- `+ New Pop Up` → `/admin/popup/new`
- `+ New Products` → `/admin/products/new`
- "Manage Popups" 버튼 추가 검토 (대시보드에 popup 관리 진입점)

## 검증 기준

1. 신규 popup 생성 폼이 정상 동작하고 DB에 row 생성됨
2. 엑셀 업로드 후 popup_products에 정확한 행 수가 insert됨 (예: KPop_PopUp_Order_Sheet_2.xlsx 19행 → 19 row)
3. 메인 고객 페이지(/) 상단에 활성 popup 배너 표시
4. 배너 클릭 시 `/popup/{id}` 페이지로 이동, 상품 세로 리스트 표시
5. Popup 비활성화/삭제 시 메인 페이지에서 사라짐
6. 빈 데이터/잘못된 양식 업로드 시 안전하게 에러 페이지 표시 (500 안 남)
7. 인증되지 않은 사용자가 admin 라우트 접근 시 로그인 페이지로 redirect

## 영향 파일

- `C:\dev\kproxy-go\app.py` (라우트 추가, placeholder 제거)
- `C:\dev\kproxy-go\templates\admin\dashboard.html` (이미 수정됨)
- `C:\dev\kproxy-go\templates\admin\popup_form.html` (신규)
- `C:\dev\kproxy-go\templates\admin\popups.html` (신규)
- `C:\dev\kproxy-go\templates\admin\popup_products.html` (신규)
- `C:\dev\kproxy-go\templates\admin\popup_upload.html` (신규)
- `C:\dev\kproxy-go\templates\popup_detail.html` (신규)
- `C:\dev\kproxy-go\templates\home.html` (배너 섹션 추가)

## Out of Scope

- 배너 이미지 / 외부 링크 / 설명 필드 (사용자 미선택)
- popup 상품에 대한 구매/장바구니 기능 (현재 GO 카트는 go_campaigns 기반, popup은 별도 흐름이며 본 플랜에서는 표시만)
- Search / filter / 페이지네이션 (popup 수가 적을 것으로 가정)
- starphotocard-go(상위) 동기화 (kproxy-go 단독 변경)

## Revision (Codex)

### Excel 파싱 명세 강화

**컬럼 인덱싱 규약 명시 (Codex #1):** `ws.iter_rows(min_row=2, min_col=2, max_col=7, values_only=True)` 사용. 그러면 `row[0]=No(B), row[1]=GROUP(C), row[2]=Product Name(D), row[3]=Option(E), row[4]=Price KRW(F), row[5]=URL(G)`.

**실측 확인 완료:** 본 플랜 작성 시 inspect_xlsx.py로 `KPop_PopUp_Order_Sheet_2.xlsx` 검증 → row 1 헤더, row 2부터 데이터, B~G 컬럼 사용 일치 확인됨.

**다국어 헤더 매핑 (Codex #2):** 헤더 sanity check를 D열에 한정하지 말고 전체 매핑 허용:
- B = `No|번호`
- C = `GROUP|그룹|그룹명`
- D = `Product Name|상품명`
- E = `Option|옵션`
- F = `Price KRW|Price (KRW)|가격`
- G = `URL|링크`

**Trailing empty rows (Codex #8):** `product_name`이 falsy하면 해당 행 skip. price-only 또는 url-only 행도 무시.

### 중복 / 재업로드 정책 (Codex #3)

**Replace-all 채택:** 같은 popup_id로 재업로드 시 기존 `popup_products` 전부 DELETE 후 신규 일괄 insert. UI 응답에 `deleted_count`, `inserted_count`, `error_count`(skip된 행) 표시. "추가 모드"는 본 플랜 범위 외.

### Price 파싱 강건화 (Codex #4)

- blank/None → `NULL`
- 숫자만 있으면 그대로
- `"₩45,000"`, `"45000원"`, `"KRW 45,000"` 등 → 콤마/통화기호/공백 제거 후 정수 파싱
- 음수 → 해당 row를 error로 집계, insert 제외
- 파싱 실패 → error 집계 (다른 row까지 500 내지 않음)
- 헬퍼: `parse_num` (app.py:_helper에 이미 존재 가능 → 확인 후 재사용 또는 신규 구현)

### 인가 / 소유권 (Codex #5)

- `_admin_or_redirect` 외에 `popup_id` 존재 여부 명시 검증. 미존재 → `404` (HTTPException, raise after redirect check). 잘못된 소유자 개념은 현재 단일 admin이라 생략 (확장 시 `owner_id` 컬럼 검토).

### 시간대 처리 (Codex #6)

- 활성 popup 판정: `is_active = true AND (end_date IS NULL OR end_date >= KST today)`
- KST = `Asia/Seoul`. 파이썬에서 `from zoneinfo import ZoneInfo; date.today()` 대신 `datetime.now(ZoneInfo("Asia/Seoul")).date()` 사용
- DB에 `end_date date` 그대로 두되 비교 시점에서만 KST 변환 (스키마 변경 불필요)

### 트랜잭션 / 원자성 (Codex #7)

- `popup_products` 업로드는 `(popup_id 단위)` 원자성 보장: 같은 사용자 호출 내에서 `delete().eq("popup_id", id)` → `insert(rows)` 2단계
- supabase-py는 client transactions 직접 미지원 → batch insert (rows를 chunk로 자르거나 한번에 100~500행) + try/except로 부분 실패 허용. 부분 실패 시 error_count 집계
- per-row error 집계는 app.py:596-605의 import 패턴 유지

### 스키마 보강 (Codex #8)

원본 스키마에 다음 추가:

```sql
ALTER TABLE popup_products ADD CONSTRAINT popup_products_popup_no_unq UNIQUE (popup_id, no);
-- (no가 NULL이면 unique 무시되므로 중복 No 방지에는 충분)
```

- RLS는 starwms 패턴(disabled)과 동일하게 설정. service_role 키로만 접근하므로 disabled로 두는 것이 일관성 있음. (사용자가 SQL Editor에서 추가 작업 필요 없음 — 디폴트 disabled)
- `popup_events`에 `created_by` 같은 컬럼은 본 플랜 범위 외

### 단순 대안 (Codex #9)

- **홈 배너 컴포넌트:** 기존 `home.html`의 `.card` 클래스 재사용하지 않고 별도 `.popup-banner` 클래스로 분리. 이유: 카트 버튼/장바구니 상호작용 등 기존 카드와 다름
- **Batch insert vs row-by-row:** 행 수가 보통 50건 미만 → row-by-row + per-row error catch가 단순하고 충분. supabase-py의 `.insert([rows])`는 부분 실패 시 한 번에 전부 실패하므로 정상 행과 에러 행 구분이 어려움 → row-by-row 채택
- **이미지 컬럼:** `popup_products.image_url`은 본 플랜 범위 외. 향후 필요 시 ALTER TABLE

### 검증 기준 보강 (Codex #10)

기존 7개 항목에 추가:

8. Popup 삭제 시 popup_products가 CASCADE로 자동 삭제됨 (DB 직접 확인)
9. 같은 파일을 2회 연속 업로드 시 데이터 중복되지 않고 replace-all 동작 (DB row 수 동일)
10. Trailing empty rows가 있는 엑셀 파일도 정상 처리 (skip된 행 수 / inserted 행 수 정확히 표시)
11. 가격 엣지 케이스 5개 샘플(`""`, `"45,000"`, `"₩45000"`, `"-5000"`, `"abc"`) 모두 안전하게 처리되고 error_count로 집계

### 작업 재구성 (Codex #11)

기존 Task B를 분리:

- **B1: Admin popup CRUD** — popup_events 생성/편집/삭제/토글 라우트 + 템플릿 (popup_form.html, popups.html)
- **B2: Excel import endpoint** — products 업로드 라우트 + 파싱 헬퍼 + 응답 템플릿 (popup_upload.html, popup_products.html)
- **B3: Popup public view** — `/popup/{id}` 라우트 + popup_detail.html

Task F (대시보드 정리)는 Task D(템플릿)와 병합 — 작업 단위가 작음.

### 우선순위

`Task A (DB 스키마)` → `B1 (Admin CRUD)` → `B2 (Excel 업로드)` → `B3 (Public view)` → 검증.

A는 사용자 수동 작업, B1~B3는 단일 PR. 분리 커밋 권장.
