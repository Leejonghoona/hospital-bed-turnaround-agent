"""설문 문구 JSON → 구글 설문지를 만드는 Apps Script(.gs).

사용: python scripts/make_google_form.py docs/검토/현직자_설문_문구.json docs/검토/현직자_설문_구글폼.gs
만든 .gs를 script.google.com 새 프로젝트에 붙여 넣고 createSurvey를 실행하면
설문지와 응답 시트가 만들어지고, 실행 기록(로그)에 설문 링크가 나온다.
"""

import json
import sys

TEMPLATE = r"""// 병상 흐름 현장 질문지: 구글 설문지 자동 생성 (scripts/make_google_form.py가 만든 파일)
// 1) script.google.com → 새 프로젝트 → 이 내용을 전부 붙여 넣기
// 2) 위쪽 함수 목록에서 createSurvey 선택 → 실행 → 권한 허용
// 3) 아래 "실행 로그"에 나오는 "보낼 링크"를 상대에게 보내기
const SURVEY = __JSON__;

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
"""


def main(src: str, out: str) -> None:
    data = json.load(open(src, encoding="utf-8"))
    for sec in data["sections"]:
        for q in sec["items"]:
            assert q["type"] in ("one", "many", "line", "text"), q
            if q["type"] in ("one", "many"):
                assert q["choices"], q["id"]
    js = json.dumps(data, ensure_ascii=False, indent=2)
    open(out, "w", encoding="utf-8").write(TEMPLATE.replace("__JSON__", js))
    n = sum(len(s["items"]) for s in data["sections"])
    print(f"{out}: 섹션 {len(data['sections'])}, 질문 {n}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
