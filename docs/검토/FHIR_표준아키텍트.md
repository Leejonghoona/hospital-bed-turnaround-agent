# 의료정보 표준 아키텍트 검토: "FHIR 기준 온톨로지화" 제안

표기: [확인]은 이번 검토에서 원문·서버로 직접 확인함(2026-11-08). [추정]은 경험에 기반한 판단으로, 따로 검증하지 않음.

## 결론 먼저

**권고: "입력 경계 + 코드 정렬"까지만 한다. 전면 FHIR RDF 정본화는 하지 않는다.**
- 이 서비스 개념의 절반가량(코호트, 선제 격리 상태, 병실 성별, 매칭 제안 점수, 사람 고정, 환기 대기)은 FHIR R4나 KR Core에 자리가 없다. 정본을 FHIR로 바꿔도 결국 로컬 확장과 로컬 CodeSystem 묶음이 된다.
- "새 감염병은 개념 한 줄"이라는 장점은 질환을 주의 유형에 잇는 **로컬 규칙 지식**에서 나온다. SNOMED is-a 계층은 이 연결을 주지 않는다(아래 2절).
- 반면 입력을 FHIR(Bundle, Subscription 알림) 형태로 받고 내부 개념에 표준 코드(SNOMED, v2-0116, HIRA 진료과)를 붙여 두면, "병원 데이터가 FHIR로 온다"는 사용자 가정을 데모에서 실제로 보여줄 수 있다. 비용도 작다.

## 1. 개념 → FHIR R4 / KR Core 매핑

KR Core 1.0.2 패키지를 받아 프로파일 목록을 확인했다[확인]. Patient, Encounter, Condition, Observation, MedicationRequest, Organization, Practitioner(Role) 등 30개가 있다. **Location, Task, Flag, ServiceRequest, Communication, Provenance 프로파일은 없다.** 그래서 아래 리소스 대부분은 R4 기본 리소스를 그대로 쓴다. KR Core Patient는 identifier, name.text, gender, birthDate가 필수(1..1)다[확인]. KR Core Encounter는 status, class, period만 MustSupport로 둔다[확인].

| 개념 | 리소스·요소 | 코드 | 표준 자리 |
|---|---|---|---|
| 환자 | Patient (KR Core) | gender (administrative-gender) | 있음. 합성 identifier는 `urn:` 계열 가상 MRN system 사용. 주민번호 형식은 만들지 않는다 |
| 입원, 진료과 | Encounter (KR Core), serviceType 또는 KR Core HIRA 진료과목 CodeSystem | KR Core `hira-medical-department`[확인] | 있음 |
| 병동, 병실, 병상 | Location, physicalType = wa / ro / bd, partOf로 계층 | location-physical-type[확인 예제] | 있음 |
| 1인실, 다인실, 음압격리실 | Location.type | 로컬 CodeSystem 필요. v3-RoleCode에는 음압실 코드가 없다고 본다[추정] | **로컬 코드** |
| 병실 성별, 정원 | 없음 | | **확장**(정원은 partOf로 병상 수를 세면 됨) |
| 코호트실(병원체 키) | 없음 | 병원체는 SNOMED 생물체 코드 | **확장** `cohortOrganism` (CodeableConcept) |
| 병상 상태 | Location.operationalStatus (Coding, 0..1) | v2-0116: C, H, I, K, O, U. 바인딩 강도는 Preferred[확인] | 있음. 예약은 0116에 없으므로 Encounter.location.status = reserved로 표현 |
| 환자 위치·병상 배정 | Encounter.location (status: planned, active, reserved, completed. Required 바인딩[확인]) + period | | 있음. 확정 = active, 사전 배정 = planned 또는 reserved |
| 퇴원 예정일 | R4에는 요소가 없음. R5의 Encounter.plannedEndDate를 R4로 가져오는 교차버전 확장 | | **확장**(표준 교차버전 확장 URL)[추정: R5 요소명은 기억으로 적음] |
| 퇴원 단계(퇴원약, 교육, 수납, 보호자) | Task (focus = Encounter, code = 로컬 단계 코드), status (ready, in-progress, on-hold, completed) | 로컬 CodeSystem | 틀은 있고 코드는 **로컬** |
| 퇴원 지연 사유 | Task.statusReason | 로컬 CodeSystem(설계 문서의 6종) | **로컬 코드** |
| 보호자, 도착 예정 | RelatedPerson. 예정 시각은 보호자 Task의 restriction.period | | 있음(용도를 넓혀 해석) |
| 퇴원 확정, 취소 | Encounter.hospitalization.dischargeDisposition(Example 바인딩)[확인], Task.status = cancelled | | 있음 |
| 입원 대기, 입원 취소 | ServiceRequest(입원 요청) 또는 Encounter status = planned → cancelled | | 있음 |
| 청소, 종말소독, 아포 소독 | Task (focus = Location, code = general / terminal / sporicidal) | 로컬 CodeSystem. SNOMED 소독 시술 코드 매핑은 [추정] | 틀은 있고 코드는 **로컬** |
| 환기 대기 | 청소 Task.restriction.period.start(이 시각 전 시작 금지) | | 있음(용도를 넓혀 해석) |
| 청소 완료 신호 | Task.status = completed + Location.operationalStatus H → U | | 있음 |
| 격리 오더(주치의, 감염관리실) | **ServiceRequest** (code = SNOMED 주의, requester = PractitionerRole) | SNOMED | 있음. 설계의 "오더 값만 신뢰"에 맞는 정본은 Flag가 아니라 ServiceRequest다 |
| 전파경로 주의 표시 | Flag (code SNOMED). 근거는 표준 확장 `flag-detail`로 ServiceRequest나 Condition을 가리킴[추정: 확장 이름] | 409524006 Airborne, 409529001 Contact, 409525007 [확인] | 있음. 다만 409525007의 FSN은 "Respiratory secretion precautions"이고 "Droplet precautions"는 동의어다[확인]. 설계 문서 표기를 보완할 것 |
| 병원체 보균, 확진·의심 | Condition (code = 감염·보균, verificationStatus = confirmed / provisional) | SNOMED 생물체: MRSA 115329001, VRE 113727004, MTB 113861009, C. difficile 5933001 [확인] | 있음 |
| 선제 격리 | 없음. Flag 또는 ServiceRequest에 상태를 다는 로컬 코드 | | **로컬 코드**(confirmed/suspected/preemptive 중 preemptive는 FHIR verificationStatus에 대응 없음) |
| 선별검사 음성 2회 | Observation (LOINC 배양·PCR) | LOINC | 있음. 해제 규칙은 로컬 |
| 면역저하, 개방창상 | Condition 또는 Flag | SNOMED | 있음 |
| 매칭 제안 | Task (intent = proposal, focus = Encounter, input = 후보 Location과 점수) | 로컬 Task 코드 `bed-assignment` | 틀은 있음. 점수와 근거 순번은 **확장** 또는 Task.input |
| 병동 수용 회신 | 같은 Task의 owner = 책임간호사 PractitionerRole, status = accepted / rejected, statusReason | | 있음 |
| 원무 HIS 확정 | Encounter.location 갱신 PUT + If-Match: W/"versionId" | | 있음(R4 RESTful 버전 검사) |
| 환자 연락(되돌릴 수 없는 분기점) | Communication (about = Encounter) | | 있음 |
| 사람 고정(관리자 직접 수정) | 없음. Provenance(activity = 로컬 `manual-override`, reason) + 매칭 Task에 meta.tag | | **로컬 코드 + 태그** |
| 게이트웨이 판단, 감사 로그 | AuditEvent 또는 Provenance | | 있음. 정책 버전과 근거 순번은 **확장** |

요약: 위치, 상태, 업무 흐름의 뼈대는 FHIR가 잘 받는다. 감염관리 판단에 쓰는 속성(코호트 키, 선제 상태, 병실 성별·유형)은 확장이나 로컬 코드다. 국내 표준 쪽 공백은 KR Core에 Location, Task, Flag 프로파일이 없다는 점이다.

## 2. 온톨로지 정렬: 두 방식 비교

**A. FHIR RDF 위에 SHACL**
- R4 RDF 경로는 길다[확인: R4 location-example-room.ttl]. 현재 `ta:assignedTo/ta:inRoom`인 한 단계가 `^fhir:Encounter.subject / fhir:Encounter.location / fhir:Encounter.location.location / fhir:link / fhir:Location.partOf / fhir:link`가 된다. 리소스마다 `fhir:Location.status [ fhir:value "..." ]`처럼 값이 빈 노드에 싸여 있다. 상태 필터(Encounter.location.status = active, period.end 없음)도 SPARQL에 들어가야 한다. 지금 SHACL 14개 형상이 각각 3~5배 길어진다고 본다[추정].
- R5 RDF(build 판)는 `fhir:v`, `fhir:l`로 줄였다[확인]. 그래도 R4와 R5 RDF가 서로 달라 판 선택 위험이 생긴다.
- 격리 판정은 `Flag.subject ← Flag.code.coding`에서 개념 IRI(`a sct:409524006`)를 읽어야 한다. R4 RDF는 Coding에 개념 IRI 타입을 다는 것을 "매우 유용"하다고 권할 뿐 필수로 두지 않는다[확인]. 변환기가 직접 붙여야 한다.
- Python 생태계에는 FHIR JSON을 R4 RDF로 바꾸는 성숙한 라이브러리가 없다고 본다[추정]. 자체 변환기를 써야 한다.
- 장점: 병원 데이터를 그대로 그래프로 올린다는 서사, 표준 경로로 검증한다는 점.

**B. 자체 경량 온톨로지 유지 + FHIR → 내부 매핑(권고)**
- concepts.ttl과 shapes.ttl은 그대로 둔다. 내부 클래스에 `skos:exactMatch sct:409524006` 같은 표준 코드 매핑 트리플만 더한다.
- 매핑 계층(FHIR 리소스를 내부 사실로 바꾸는 변환기)이 한 곳에 모인다. "FHIR 경로가 바뀌면 고칠 곳"이 그 변환기 하나다.
- "새 감염병은 한 줄" 장점이 그대로 남는다.

**SNOMED 계층을 끌어올 수 있는가**
- 확인한 is-a: 409524006, 409529001, 409525007은 모두 409583003 Transmission-based precautions의 자식이다[확인]. 409525007은 409523000 Standard respiratory precautions의 자식이기도 하다. MRSA는 S. aureus의 하위다[확인].
- 그러나 **"결핵 → 공기주의"는 SNOMED is-a가 아니다.** 주의 코드는 시술(procedure) 계층이고 결핵은 질환·생물체 계층이다. 두 축을 잇는 것은 질병관리청 지침 부록1의 규칙표다. SNOMED를 가져와도 현재 `SuspectedTuberculosisPatient ⊑ AirborneIsolationPatient` 한 줄을 대신하지 못한다. 생물체 하위 계층(예: 어떤 CRE 종도 CRE 코호트로 묶기)에만 쓸모가 있다.
- 라이선스: 대한민국은 SNOMED International 회원국 목록에 있다[확인: snomed.org/members]. 국내 사용은 국가 배포 센터를 거친 Affiliate 라이선스 등록(MLDS)으로 무상이라고 본다[추정: 국내 등록 절차와 배포 센터 기관명은 확인하지 못함]. 공개 저장소에 SNOMED 릴리스 파일이나 추출한 계층 부분집합을 커밋하면 재배포 조건에 걸릴 수 있다. 데모에서는 **코드 10여 개를 식별자로만** 쓰고, 계층은 내부 concepts.ttl에 직접 적는 편이 안전하다.

## 3. 이벤트 입력

- **방식**: R4 기본 Subscription은 criteria 검색식 기반이다. 주제(topic) 기반은 R4B와 R5에 있다. R4 서버는 Subscriptions R5 Backport IG로 쓴다[확인: IG 1.1.0 페이지]. 알림은 `history` 타입 Bundle이고, 첫 항목 SubscriptionStatus에 eventsSinceSubscriptionStart와 notificationEvent.eventNumber가 들어간다[추정: 요소명은 기억]. 이 번호로 순서와 누락을 잡는다. 데모에서는 서버 없이 **알림 Bundle JSON 파일을 재생**하는 어댑터면 충분하다.
- **occurred_at**: 리소스의 업무 시각을 쓴다. Task.executionPeriod.end(청소 완료), Encounter.location.period.start(입실), Encounter.period.end(퇴실). 리소스마다 다르니 매핑표로 고정한다. 출처가 필요하면 Provenance.occurred[x](발생)와 Provenance.recorded(기록)가 정확히 두 시각을 나눈다.
- **observed_at**: 수신 시각(우리 쪽 기록)이다. meta.lastUpdated는 FHIR 서버가 저장한 시각이라 "HIS가 안 시각"에 가깝다. 둘 다 저장하고, 미래 정보 누수 검사는 수신 시각으로 한다.
- **멱등 키**: `{resourceType}/{id}/_history/{meta.versionId}`. 같은 버전의 중복 알림은 자연스럽게 걸러진다. 한 버전에서 여러 내부 이벤트가 나오면 접미사를 붙인다(`.../_history/3#cleaning_done`). 우리가 HIS에 쓸 때는 조건부 생성(If-None-Exist)과 If-Match를 쓴다.
- **주의**: 버전 기반 키는 "상태 변화"만 준다. 내부 이벤트 종류(예: cleaning_started)는 이전 버전과 비교해 뽑아야 한다. 그래서 어댑터가 리소스별 직전 버전을 기억해야 한다. 이 부분이 어댑터의 실제 작업량이다.

## 4. 합성 데이터

- **Synthea**: Java 기반이다. 미국 US Core 모양의 Patient, Encounter, Condition 묶음을 만든다[추정]. 병상 단위 Location, 청소 Task, 병상 상태, 퇴원 단계 Task, 보호자 지연, 선제 격리 같은 이 서비스의 핵심 사건은 만들지 않는다. 인구·질병 모듈도 미국 통계다. 한국 이름, KR Core 식별자, HIRA 진료과, KCD-8 진단을 쓰려면 모듈을 손봐야 하는데, 이 데모의 30건 규모에 비해 과하다.
- **권고**: 기존 생성기(`sim/generator.py`)를 정본으로 유지한다. 그 결과를 FHIR R4 JSON Bundle로 **내보내는 직렬화기**만 더한다. 한국 맥락은 KR Core Patient·Encounter 프로파일과 HIRA 진료과, KCD-8 CodeSystem으로 채운다. 이 셋은 KR Core 패키지에 들어 있다[확인].
- **검증**: HL7 공식 validator(Java jar)에 `-ig hl7.fhir.kr.core#1.0.2`를 걸어 CI 밖에서 한 번 돌린다. Python 테스트에서는 `fhir.resources` 같은 pydantic 모델로 구조만 검사한다[추정: R4 지원 버전은 확인 필요].
- **한계**: KR Core에 Location, Task, Flag 프로파일이 없어서 "KR Core 준수"는 Patient와 Encounter에만 해당한다. 나머지는 R4 기본과 로컬 프로파일이다. 이 점을 문서에 명시해야 과장이 되지 않는다.

## 5. 권고 범위와 작업 목록

| 범위 | 내용 | 판단 |
|---|---|---|
| 입력 경계만 | FHIR Bundle → 이벤트 어댑터, 생성기 → FHIR 내보내기 | 최소 |
| **+ 코드 정렬(권고)** | 위 + 내부 개념에 표준 코드 매핑, 로컬 CodeSystem 정의, 매핑 문서 | 비용 대비 효과 최대 |
| 전면 | FHIR RDF를 정본 그래프로, SHACL을 fhir: 경로로 재작성 | 하지 않음 |

단계별 작업(파일 수준). CLAUDE.md 순서대로 DECISIONS 기록 → 테스트 → 구현 순이다.
1. `docs/DECISIONS.md`: "FHIR는 입력 경계와 코드 정렬까지, 정본 온톨로지는 내부 유지"를 결정과 이유(2절)로 기록한다. 409525007 표기를 보완한다.
2. `src/turnaround/fhir/codes.py`와 `src/turnaround/fhir/codesystems/*.json`: 로컬 CodeSystem 5개를 정의한다(병실 유형, 퇴원 단계, 지연 사유, 청소 유형, 격리 상태·사람 고정). SNOMED와 v2-0116 상수도 여기 둔다.
3. `src/turnaround/fhir/export.py` + `tests/test_fhir_export.py`: SyntheticHospital을 Location 계층, Patient, Encounter, ServiceRequest(격리 오더), Flag, Condition으로 바꿔 R4 Bundle을 만든다. 테스트는 구조 검사와 "실데이터 형식 식별자 없음"을 본다.
4. `src/turnaround/fhir/ingest.py` + `tests/test_fhir_ingest.py`: 알림 Bundle을 내부 `Event`로 바꾼다. 멱등 키 규칙, occurred/observed 매핑, 직전 버전 비교를 넣는다. 핵심 테스트는 **왕복**이다. 생성기 → export → 알림 Bundle 재생 → ingest 결과가 기존 `events/replay.py` 출력과 같아야 한다. 이 테스트가 통과하면 회귀 시나리오 30개를 FHIR 경로로도 돌릴 수 있다.
5. `src/turnaround/ontology/concepts.ttl`: 내부 클래스에 `skos:exactMatch`(SNOMED, v2-0116)만 추가한다. shapes.ttl은 바꾸지 않는다.
6. `docs/` 매핑 문서: 1절 표를 설계 문서 데이터 섹션에 반영하되, 정답 문서 수정 여부는 사용자가 정한다.
7. (선택) `src/turnaround/fhir/server_stub.py`: 설계의 "가상 FHIR 서버 If-Match"를 Location과 Encounter의 versionId 증가로 맞춘다. 게이트웨이의 기존 가상 버전 검사와 통합한다.

## 리스크

1. **범위 팽창**: FHIR 프로파일링 자체가 끝없는 작업이다. 로컬 확장 StructureDefinition을 정식으로 쓰기 시작하면 데모의 본론(턴어라운드 관제)보다 커진다. 확장은 URL과 의미 정의만 두고, StructureDefinition 작성은 하지 않는다.
2. **"KR Core 준수" 과장**: 실제 준수 범위는 Patient와 Encounter뿐이다. "FHIR R4 기반, KR Core 부분 적용"으로 써야 한다.
3. **SNOMED 라이선스**: 계층 파일을 커밋하면 문제가 될 수 있다. 코드는 식별자로만 쓴다.
4. **Flag 오용**: Flag는 표시용(경고 배너)이다. 격리 판단의 정본은 ServiceRequest(오더)와 Condition(보균·확진)으로 둔다. 그래야 "전달사항보다 오더 값만" 원칙과 맞는다.
5. **두 시각의 오해**: meta.lastUpdated를 occurred로 쓰면 늦은 인지 시나리오가 무력해진다. 리소스별 업무 시각 매핑을 테스트로 고정해야 한다.
6. **R4와 R5 차이**: 퇴원 예정일과 Subscription topic은 R5 개념이다. R4 데모에 넣으면 교차버전 확장과 Backport IG 의존이 생긴다. 명시적으로 표기한다.
7. **실제 HIS의 현실**[추정]: 국내 병원 HIS 대부분은 병상·청소 업무를 FHIR로 내보내지 않는다. 운영에서는 HL7 v2 ADT(A01, A02, A03)와 DB 폴링이 주 입력일 가능성이 크다. 그래서 FHIR 어댑터는 "여러 입력 어댑터 중 하나"로 설계해야 한다. 내부 이벤트 모델을 정본으로 두는 B안이 이 점에서도 맞다.
