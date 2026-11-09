// 병상 흐름 현장 질문지: 구글 설문지 자동 생성 (scripts/make_google_form.py가 만든 파일)
// 1) script.google.com → 새 프로젝트 → 이 내용을 전부 붙여 넣기
// 2) 위쪽 함수 목록에서 createSurvey 선택 → 실행 → 권한 허용
// 3) 아래 "실행 로그"에 나오는 "보낼 링크"를 상대에게 보내기
const SURVEY = {
  "title": "퇴원부터 다음 입원까지, 병상 흐름 설문",
  "description": "퇴원부터 다음 환자 입원까지 병상이 비고 다시 준비되는 흐름을 돕는 AI를 개인 프로젝트로 만들고 있어요. 가상 데이터로만 만들지만, 현장과 동떨어지지 않도록 실제로 어떻게 일하시는지 여쭤보고 싶어요. 답해 주신 내용은 이 프로젝트의 설계를 고치는 데만 써요.\n\n답하시기 전에 부탁드려요.\n• 환자 이름, 호실, 등록번호, 병원 이름은 적지 말아 주세요. 예가 필요하면 숫자나 이름을 바꿔서 적어 주세요.\n• 지금 일하시는 병원과 병동을 기준으로 답해 주세요.\n• 잘 모르시는 문항은 짐작하지 말고 '모름'을 골라 주세요. '모름'도 저에게는 중요한 답이에요.\n• 필수 문항(1~11번)만 답해 주셔도 큰 도움이 돼요. 15분 정도 걸려요. 여유가 있으시면 선택 문항(S1~S10)도 부탁드려요.\n• 문항 설명 앞의 [간호] [원무] 표시는 그 직무에서 답하기 쉬운 문항이라는 뜻이에요. 맡지 않는 일이라 모르시면 건너뛰셔도 돼요.",
  "confirmation": "답해 주셔서 정말 감사합니다. 답을 보고 더 여쭤볼 것이 생기면 한 번만 짧게 연락드릴게요.",
  "sections": [
    {
      "title": "답하시는 분",
      "description": "답을 어떤 병원, 어떤 직무의 이야기로 볼지 정하는 데만 써요.",
      "items": [
        {
          "id": "q0_1",
          "title": "0-1. 지금 하시는 일은 무엇인가요?",
          "help": "",
          "type": "one",
          "choices": [
            "병동 간호사",
            "병동 수간호사·책임간호사",
            "원무과 입원 담당(병상 배정)",
            "원무과 수납·퇴원 담당"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q0_2",
          "title": "0-2. 일하시는 병원은 어느 종류인가요?",
          "help": "",
          "type": "one",
          "choices": [
            "상급종합병원",
            "종합병원",
            "병원·요양병원",
            "밝히고 싶지 않아요"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "q0_3",
          "title": "0-3. 일하시는 병동은 어느 쪽인가요?",
          "help": "",
          "type": "one",
          "choices": [
            "내과계 병동",
            "외과계 병동",
            "내과·외과 혼합 병동",
            "병동 근무가 아니에요(원무과 등)"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q0_4",
          "title": "0-4. 다른 병원에서도 일해 보신 적이 있나요?",
          "help": "있으시면 필수 문항 끝에서 전에 일하신 병원과 다른 점을 한 번 더 여쭤볼게요.",
          "type": "one",
          "choices": [
            "있어요",
            "없어요"
          ],
          "other": false,
          "required": false
        }
      ]
    },
    {
      "title": "필수 문항",
      "description": "가장 여쭤보고 싶은 문항이에요. 답하지 않아도 다음으로 넘어갈 수 있지만, 모르시면 비워 두기보다 '모름'을 골라 주세요.",
      "items": [
        {
          "id": "q1_1",
          "title": "1-1. 평일 낮에는 병상 배정을 누가 하나요?",
          "help": "[공통] 다음 입원 환자의 병실과 침대(몇 호 몇 번)를 정하는 사람을 골라 주세요.",
          "type": "one",
          "choices": [
            "원무과 입원 담당",
            "간호부 병상관리 담당",
            "병동 수간호사·책임간호사",
            "응급실 원무(수납) 담당",
            "간호부 당직 감독",
            "이 시간에는 배정하지 않고 다음 근무일로 넘겨요",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q1_2",
          "title": "1-2. 밤, 주말, 공휴일에는 병상 배정을 누가 하나요?",
          "help": "[공통] 다음 입원 환자의 병실과 침대(몇 호 몇 번)를 정하는 사람을 골라 주세요.",
          "type": "one",
          "choices": [
            "원무과 입원 담당",
            "간호부 병상관리 담당",
            "병동 수간호사·책임간호사",
            "응급실 원무(수납) 담당",
            "간호부 당직 감독",
            "이 시간에는 배정하지 않고 다음 근무일로 넘겨요",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q2",
          "title": "2. 가장 최근에 보신 퇴원 한 건을 떠올려 주세요. 환자가 병실을 나간 뒤 다음 환자가 그 침대에 들어오기까지 어떤 일이 어떤 순서로 있었나요?",
          "help": "[공통] 누가 누구에게 무엇으로(전화, 전산, 메신저, 직접 말로) 알렸는지 생각나는 대로 3~5줄 적어 주세요. 원무과에 계시면 '퇴원 수납부터 다음 환자 병실 안내까지'로 생각해 주세요.",
          "type": "text",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "q3",
          "title": "3. 퇴원 환자의 침대가 전산에서 '빈 병상'으로 바뀌는 때는 언제인가요?",
          "help": "[간호] 가장 가까운 것 하나를 골라 주세요.",
          "type": "one",
          "choices": [
            "퇴원 처리(수납)가 끝났을 때(환자가 아직 병실에 있어도)",
            "환자가 실제로 나간 뒤 간호사가 퇴실을 따로 입력할 때",
            "퇴실 청소가 끝났을 때",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q4_1",
          "title": "4-1. 퇴실 청소는 주로 어떻게 요청하나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "전산으로 요청해요",
            "전화로 요청해요",
            "메신저(단톡방 포함)로 요청해요",
            "따로 요청하지 않아요(청소 담당이 퇴원 명단을 보고 와요)",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q4_2",
          "title": "4-2. 퇴실 청소가 끝난 것은 주로 어떻게 아나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "전산에 완료 표시가 떠요",
            "청소 담당이 전화나 말로 알려 줘요",
            "청소 담당이 메신저로 알려 줘요",
            "간호사가 직접 가서 확인해요",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q5",
          "title": "5. 다음 환자가 병동에 올라오기 전에, 병동이 받을 수 있다고 따로 확인해 주는 단계가 있나요?",
          "help": "[공통]",
          "type": "one",
          "choices": [
            "있어요. 배정 담당(원무과 등)이 전화하면 병동이 답해요",
            "있어요. 병동이 전산에서 확인(승인)을 눌러요",
            "없어요. 배정되면 병동은 통보만 받아요",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q6_1",
          "title": "6-1. 격리 환자가 퇴실한 병실은 다음 환자를 받기 전에 비워 두는 시간이 정해져 있나요?",
          "help": "[간호] 접촉·비말·공기주의 격리 등이요. 최근에 있었던 경우를 떠올려 주세요.",
          "type": "one",
          "choices": [
            "격리 종류와 상관없이 정해진 시간만큼 비워 둬요",
            "격리 종류에 따라 비워 두는 시간이 달라요",
            "감염관리실이 그때그때 정해 줘요",
            "따로 비워 두지 않고, 소독과 청소가 끝나면 바로 받아요",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "q6_2",
          "title": "6-2. 비워 두는 시간이 정해져 있다면 얼마인가요?",
          "help": "[간호] 6-1에서 앞의 두 가지 중 하나를 고르신 경우에만 적어 주세요. 격리 종류마다 다르면 종류별로 적어 주세요. 형식 예: ○○주의 ○분",
          "type": "line",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "q7",
          "title": "7. 밤이나 주말에 격리 환자를 받을 병실이 마땅치 않으면, 누구와 상의해서 정하나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "감염관리실 당직 간호사",
            "감염내과 당직 의사",
            "간호부 당직 감독",
            "주치의나 당직 의사",
            "병동에서 정하고, 다음 날 감염관리실에 보고해요",
            "그런 일이 거의 없어요",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q8",
          "title": "8. 퇴원 환자의 보호자가 몇 시에 오는지는 주로 언제 처음 알게 되나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "퇴원 전날이나 그 전",
            "퇴원 당일 아침",
            "퇴원 당일 수납할 즈음",
            "따로 묻지 않아요(보호자가 와야 알아요)",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "q9",
          "title": "9. 최근 1~2주 사이에 예정보다 늦게 나간 퇴원이 있었다면, 무엇 때문이었나요?",
          "help": "[공통] 해당하는 것을 모두 골라 주세요. 여러 건이 떠오르면 가장 최근 한 건을 기준으로 해 주세요. 목록에 없는 이유는 '기타'에 적어 주세요.",
          "type": "many",
          "choices": [
            "퇴원약 대기",
            "퇴원 처방이 늦게 남",
            "보호자가 늦게 오거나 연락이 안 됨",
            "퇴원 교육",
            "퇴원 심사·수납",
            "진단서 등 서류 발급",
            "검사 결과 대기",
            "전원 갈 병원의 자리나 회신 대기",
            "이송 차량(사설 구급차 등) 대기",
            "환자 상태가 바뀌어 퇴원 보류",
            "늦게 나간 퇴원이 없었음",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q10_1",
          "title": "10-1. 다음 날 퇴원할 환자의 퇴원 예정은 주로 언제 전산에 입력되나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "퇴원 이틀 전이나 그 전",
            "퇴원 전날 오전",
            "퇴원 전날 오후",
            "퇴원 전날 저녁이나 밤",
            "퇴원 당일 아침",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "q10_2",
          "title": "10-2. 퇴원 예정이 입력되지 않으면 전산이 알려 주는 기능이 있나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "있어요",
            "없어요",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "q11_1",
          "title": "11-1. 다음 입원 환자에게 병실과 오실 시각을 알리는 연락은 주로 누가 하나요?",
          "help": "[원무] '병실이 정해졌으니 몇 시까지 오세요' 같은 연락이요. 간호사분은 아시는 경우에만 답해 주세요.",
          "type": "one",
          "choices": [
            "원무과 입원 담당",
            "병동 간호사",
            "외래",
            "문자 자동 발송",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "q11_2",
          "title": "11-2. 다음 입원 환자에게 병실과 오실 시각을 알리는 연락은 주로 언제 하나요?",
          "help": "[원무] 간호사분은 아시는 경우에만 답해 주세요.",
          "type": "one",
          "choices": [
            "입원 전날이나 그 전",
            "입원 당일 오전",
            "입원 당일 오후",
            "그때그때 달라요",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "q0_5",
          "title": "0-5. 1~11번 답 가운데 전에 일하신 병원과 달랐던 것이 있나요?",
          "help": "다른 병원에서 일해 보신 분만 답해 주세요. 문항 번호와 함께 어떻게 달랐는지 짧게 적어 주세요. 없으면 비워 두셔도 돼요.",
          "type": "text",
          "choices": [],
          "other": false,
          "required": false
        }
      ]
    },
    {
      "title": "선택 문항",
      "description": "여유가 있으실 때만 답해 주세요. 아시는 문항만 골라 답하셔도 돼요.",
      "items": [
        {
          "id": "s1",
          "title": "S1. 보호자가 몇 시에 온다는 내용은 주로 어디에 남기나요?",
          "help": "[간호] 최근 퇴원 한 건을 기준으로 골라 주세요.",
          "type": "one",
          "choices": [
            "전산에 전용 입력 칸이 있어요",
            "간호기록에 글로 써요",
            "인계장이나 메모지에 적어요",
            "따로 적지 않고 말로 인계해요",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "s2",
          "title": "S2. 보호자 도착이나 퇴원 지연을 기록할 때 실제로 쓰시는 표현을 2~3개 적어 주시겠어요?",
          "help": "[간호] 이름과 호실은 바꿔 주시고, 줄임말은 평소 쓰시는 그대로 적어 주세요. 형식 예: ○○님 보호자 ___ 내원 예정",
          "type": "text",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "s3",
          "title": "S3. 퇴원이 늦어진 이유는 주로 어떻게 기록하나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "전산 목록에서 골라요",
            "간호기록 등에 글로 써요",
            "따로 기록하지 않아요",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s4_1",
          "title": "S4-1. 원무과와는 주로 무엇으로 연락하나요?",
          "help": "[공통] 원무과에 계시면 병동과 연락하는 방법을 골라 주세요.",
          "type": "one",
          "choices": [
            "전산(쪽지·요청 화면)",
            "전화",
            "메신저(단톡방 포함)",
            "직접 만나서",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s4_2",
          "title": "S4-2. 약제부와는 주로 무엇으로 연락하나요?",
          "help": "[공통]",
          "type": "one",
          "choices": [
            "전산(쪽지·요청 화면)",
            "전화",
            "메신저(단톡방 포함)",
            "직접 만나서",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s4_3",
          "title": "S4-3. 청소 담당과는 주로 무엇으로 연락하나요?",
          "help": "[공통]",
          "type": "one",
          "choices": [
            "전산(쪽지·요청 화면)",
            "전화",
            "메신저(단톡방 포함)",
            "직접 만나서",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s4_4",
          "title": "S4-4. 감염관리실과는 주로 무엇으로 연락하나요?",
          "help": "[공통]",
          "type": "one",
          "choices": [
            "전산(쪽지·요청 화면)",
            "전화",
            "메신저(단톡방 포함)",
            "직접 만나서",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s5_1",
          "title": "S5-1. 최근 퇴원 몇 건을 떠올려 보면, 환자를 데리러 온 사람은 주로 누구였나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "가족",
            "간병인",
            "요양병원 직원(차량 포함)",
            "데리러 온 사람 없이 환자 혼자",
            "어느 한쪽이 많지 않고 비슷해요",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s5_2",
          "title": "S5-2. 간병인이 데리러 왔을 때, 퇴원 진행(설명, 서명, 수납)이 가족이 왔을 때와 달랐던 점이 있나요?",
          "help": "[간호] 있으면 한 줄로 적어 주세요.",
          "type": "line",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "s6",
          "title": "S6. 동료나 원무과와 이야기할 때 환자를 주로 어떻게 부르나요?",
          "help": "[공통] 예: 호실과 침대 번호, 이름, 등록번호 등. 실제 이름이나 번호 대신 부르는 방식만 적어 주세요.",
          "type": "line",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "s7_1",
          "title": "S7-1. 이름이 같은 환자를 같은 병실에 배치하나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "규칙상 같은 병실에 두지 않아요",
            "되도록 피하지만, 표시를 해 두고 같은 병실에 둔 적이 있어요",
            "따로 신경 쓰지 않아요",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s7_2",
          "title": "S7-2. 다인실에 남녀 환자를 함께 배치한 적이 있나요?",
          "help": "[간호]",
          "type": "one",
          "choices": [
            "없어요",
            "드물게 있어요",
            "모름"
          ],
          "other": false,
          "required": false
        },
        {
          "id": "s7_3",
          "title": "S7-3. 남녀 환자를 함께 배치한 적이 있다면 어떤 경우였나요?",
          "help": "[간호] S7-2에서 '드물게 있어요'를 고르신 경우에만 적어 주세요.",
          "type": "line",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "s8",
          "title": "S8. 퇴원이 취소되거나 날짜가 바뀌면, 일하시는 부서에서는 그 사실을 어떻게 알게 되나요?",
          "help": "[공통] 해당하는 것을 모두 골라 주세요.",
          "type": "many",
          "choices": [
            "전산에서 퇴원 예정이 바뀐 것을 보고 알아요",
            "주치의나 다른 부서가 전화나 말로 알려 줘요",
            "모름"
          ],
          "other": true,
          "required": false
        },
        {
          "id": "s9",
          "title": "S9. 퇴원부터 다음 입원까지 과정에서 시간이 가장 많이 새거나 손이 많이 가는 순간은 언제인가요?",
          "help": "[공통] 제가 여쭤보지 않은 것이어도 좋아요. 한두 줄이면 충분해요.",
          "type": "text",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "s10_1",
          "title": "S10-1. 주말 퇴원은 평일 퇴원과 무엇이 다른가요?",
          "help": "[공통] 퇴원약, 수납 등에서 다른 점이 있으면 아시는 만큼 적어 주세요.",
          "type": "line",
          "choices": [],
          "other": false,
          "required": false
        },
        {
          "id": "s10_2",
          "title": "S10-2. 퇴실 청소는 요청부터 끝날 때까지 보통 얼마나 걸리나요?",
          "help": "[공통] 아시는 경우에만 골라 주세요.",
          "type": "one",
          "choices": [
            "30분 미만",
            "30분 이상 1시간 미만",
            "1시간 이상 2시간 미만",
            "2시간 이상",
            "모름"
          ],
          "other": false,
          "required": false
        }
      ]
    }
  ]
};

function createSurvey() {
  const form = FormApp.create(SURVEY.title);
  form.setDescription(SURVEY.description);
  form.setConfirmationMessage(SURVEY.confirmation);
  form.setCollectEmail(false);
  form.setProgressBar(true);
  form.setAllowResponseEdits(true);
  form.setShowLinkToRespondAgain(false);

  SURVEY.sections.forEach(function (sec, i) {
    if (i > 0) {
      form.addPageBreakItem().setTitle(sec.title).setHelpText(sec.description || "");
    } else if (sec.title) {
      form.addSectionHeaderItem().setTitle(sec.title).setHelpText(sec.description || "");
    }
    sec.items.forEach(function (q) {
      var item;
      if (q.type === "one") {
        item = form.addMultipleChoiceItem().setChoiceValues(q.choices);
        if (q.other) item.showOtherOption(true);
      } else if (q.type === "many") {
        item = form.addCheckboxItem().setChoiceValues(q.choices);
        if (q.other) item.showOtherOption(true);
      } else if (q.type === "line") {
        item = form.addTextItem();
      } else {
        item = form.addParagraphTextItem();
      }
      item.setTitle(q.title).setHelpText(q.help || "").setRequired(!!q.required);
    });
  });

  const sheet = SpreadsheetApp.create(SURVEY.title + " (응답)");
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());
  Logger.log("보낼 링크: " + form.getPublishedUrl());
  Logger.log("설문 수정: " + form.getEditUrl());
  Logger.log("응답 시트: " + sheet.getUrl());
}
