"""단계별 소요시간 분포 기본값(분). 설계값(가정)이며 실제 병원 측정치가 아니다."""

from .models import StageDistribution

DEFAULT_STAGE_DISTRIBUTIONS: dict[str, StageDistribution] = {
    # 퇴실 전
    "order_after_round": StageDistribution(median_min=60, p90_min=150),  # 08:30 회진 시작부터 퇴원 오더 확정
    "medication": StageDistribution(median_min=90, p90_min=180),  # 오더 확정부터 퇴원약 준비
    "education": StageDistribution(median_min=30, p90_min=60),  # 오더 확정부터 퇴원 교육 완료
    "billing": StageDistribution(median_min=40, p90_min=90),  # 퇴원약 준비부터 심사와 수납 완료
    "exit_prep": StageDistribution(median_min=15, p90_min=30),  # 마지막 조건 충족부터 퇴실
    "guardian_delay": StageDistribution(median_min=90, p90_min=180),  # 보호자 지연 시 예정보다 늦어지는 시간
    # 병상 준비
    "baseline_cleaning_request_delay": StageDistribution(median_min=20, p90_min=50),  # 기존 방식: 퇴실부터 청소 요청
    "cleaning_response": StageDistribution(median_min=25, p90_min=60),  # 요청부터 청소 시작
    "cleaning_extra_delay": StageDistribution(median_min=60, p90_min=120),  # 청소 지연 시 추가 대기
    "cleaning_general": StageDistribution(median_min=35, p90_min=55),  # 일반 청소
    "cleaning_terminal": StageDistribution(median_min=75, p90_min=110),  # 종말소독
    "bed_make": StageDistribution(median_min=10, p90_min=20),  # 침상 정리
}


# 현실 기준선 프리셋 덮어쓰기 (제안요청서_QI_통계.md 4.1). 등급 B·C
REALISTIC_STAGE_OVERRIDES: dict[str, StageDistribution] = {
    "order_after_round": StageDistribution(median_min=30, p90_min=120),  # C: 회진 뒤 오더는 추가 퇴원뿐
    "medication": StageDistribution(median_min=60, p90_min=150),  # B: 보라매·양산부산대 불출 시각
    "billing": StageDistribution(median_min=30, p90_min=75),  # B: 인천성모 심사 29 + 연락 6 + 수납 9
}
