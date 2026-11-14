# Kobolds-And-Catacombs

HSdeck schema 1 카드 API입니다. 최초 추가일 당시의 스펙은 `data/<카드ID>.json`의 기본 `card` 값에, 이후 변경은 날짜와 변경 필드만 `card.patches`에 저장했습니다.

`cards.json`: 현재 정보 및 기존 원화 경로 보존. `render.json`: 현재 값과 과거 날짜별 오버라이드. `history.json`: 구버전 호환. `release-policy.json`: 한국 시간(Asia/Seoul) 기준 출시일.

원본 ZIP에는 최신 스펙만 있으므로 공식 패치로 검증하지 못한 최초 값은 `initialSpecStatus`를 통해 잠정 표시합니다. 기존 원화·황금 파일은 유지했습니다.


## API 경량화 및 편집 지침 (2026-11-14)

- 공개 자료는 `cards.json` 카드 목록, `data/<CARD_ID>.json` 날짜별 카드 정보, `art/`, `golden/` 일반·황금 원화/애니메이션, `frame/` 프레임·셰이더·텍스처입니다. 토큰 카드와 리소스 연결은 보존합니다.
- 루트 `render.json`, `history.json`은 중복 호환 출력으로 다시 커밋하지 않습니다. `legacy.json`은 `tests/test_kac_schema_v2.py`와 `tools/validate-kac-current.py`의 검증 입력이므로 해당 의존성을 제거하기 전에는 유지합니다.
- 현재 `data/`는 schema-v2(메타데이터 및 날짜별 `state` 포함)입니다. 플러그인의 단순 날짜 키 JSON과 무손실 통일되기 전에는 호환된다고 가정하지 않습니다. 카드 원시 태그/출시 한국어 원문 및 증거를 보존합니다.
- 최초 사용일은 한국 시간으로 정리하고 클라이언트 사전 탑재일을 제외합니다. 베타 북미 사용 시각 환산·모험 지구 개방·이벤트 실제 개시·토큰 생성 가능일을 기준으로 합니다. 잘못된 선행 날짜 키는 삭제·통합합니다.
- 후속 날짜는 바뀐 필드만 기록합니다. 표시용 줄바꿈은 `<br></br>`, 원시 증거의 개행과 황금/프레임 바이너리는 그대로 둡니다. 추정값을 검증 완료로 단정하거나 새 호환 래퍼를 만들지 않습니다.
- `sources/`, `tools/`, `tests/`, `.github/`는 재현 가능한 검증에 쓰이는 동안 보존합니다.
