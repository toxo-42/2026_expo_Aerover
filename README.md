# 2026_expo_Aerover

재난 현장을 드론으로 훑어 **요구조자를 찾고 지형을 3D 로 복원하는** 시스템. 2026 엑스포 출품작.

기체의 라즈베리파이가 영상을 쏘고, 지상국 노트북이 그것을 받아 띄우고 · 저장하고 · 분석한다.

| 폴더 | 어디서 도나 | 무엇을 하나 |
|---|---|---|
| [`apps/aerover/`](apps/aerover/README.md) | 지상국 노트북 (PySide6 GUI) | 드론 상태 · 3D 매핑 · 요구조자 탐지 |
| [`apps/pi_code/`](apps/pi_code/README.md) | 기체의 라즈베리파이 | 카메라 스트림 · 녹화 · 웹 미리보기 |

```bash
cd apps/aerover && uv sync && uv run python app.py     # 지상국
cd ~/drone && droneenv/bin/python app.py stream        # 파이
```

**→ 전체 구조 · 통신 경로 · 환경변수 · 코드 규칙은 [`apps/README.md`](apps/README.md) 에 있다.**
변경 이력은 [`apps/CHANGELOG.md`](apps/CHANGELOG.md).
