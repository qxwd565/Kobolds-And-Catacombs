# Kobolds-And-Catacombs

HSdeck schema 1 카드 API입니다. 최초 추가일 당시의 스펙은 `data/<카드ID>.json`의 기본 `card` 값에, 이후 변경은 날짜와 변경 필드만 `card.patches`에 저장했습니다.

`cards.json`: 현재 정보 및 기존 원화 경로 보존. `render.json`: 현재 값과 과거 날짜별 오버라이드. `history.json`: 구버전 호환. `release-policy.json`: 한국 시간(Asia/Seoul) 기준 출시일.

원본 ZIP에는 최신 스펙만 있으므로 공식 패치로 검증하지 못한 최초 값은 `initialSpecStatus`를 통해 잠정 표시합니다. 기존 원화·황금 파일은 유지했습니다.
