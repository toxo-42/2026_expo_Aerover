# 검증용 데이터 (1차-B, uuid `8c13e838-79ab-4ad8-bcd9-717ce94144b7`)

| 파일 | 출처 | 쓰임 |
|---|---|---|
| `shots.geojson` | `all.zip` 안 `odm_report/` | **실제 정합에 쓰는 것.** 장별 카메라 포즈 |
| `cameras.json` | `all.zip` 최상위 | **실제 정합에 쓰는 것.** 내부 파라미터 |
| `reconstruction.json` | 컨테이너 `opensfm/` (44MB) | **검증 전용 정답지.** `all.zip` 에 없다 |

앞의 둘만 있으면 `align.py` 는 돈다. `reconstruction.json` 은 `test_align.py` 가
투영·삼각측량을 대조하는 데만 쓴다.

다시 받으려면:

```bash
U=8c13e838-79ab-4ad8-bcd9-717ce94144b7
docker cp nodeodm_test:/var/www/data/$U/odm_report/shots.geojson      data/
docker cp nodeodm_test:/var/www/data/$U/cameras.json                  data/
docker cp nodeodm_test:/var/www/data/$U/opensfm/reconstruction.json   data/
```

`opensfm/tracks.csv` 도 받아봤지만 v102 는 바이너리라 역공학 비용이 크다.
`reconstruction.json` 의 점 색으로 대신 검증했으므로 **받지 않아도 된다.**
