# KTX 취소표 감시 (안드로이드 Termux)

9/27 **김천구미 또는 동대구 → 서울**, **20:10 이전 도착** 열차에 빈 좌석이 나오면
자동으로 **예약**하고 폰 알림을 보냅니다. **결제는 코레일톡 앱에서 직접** 하면 됩니다.

## 1. 앱 설치 (F-Droid에서 두 개 모두)

플레이스토어 버전 Termux는 오래돼서 동작하지 않습니다. 반드시 F-Droid에서 받으세요.

1. 브라우저에서 <https://f-droid.org> 에 들어가 F-Droid 앱을 설치합니다.
2. F-Droid에서 **Termux**와 **Termux:API**를 설치합니다.
3. 폰 설정 > 애플리케이션 > Termux > 배터리에서 **제한 없음**(최적화 안 함)으로 바꿉니다.
4. Termux:API 앱의 알림 권한을 켭니다.

## 2. Termux에서 설치 (한 번만)

Termux를 열고 아래 내용을 **통째로 복사해서 붙여넣고** 엔터를 누르세요.

```sh
pkg update -y && pkg install -y python python-cryptography termux-api && pip install requests && curl -LO https://raw.githubusercontent.com/angerville/my-page/claude/train-ticket-booking-k462p2/ktx-watch/ktx_watch.py
```

- 중간에 `[Y/n]` 같은 질문이 나오면 엔터를 누르면 됩니다.
- `python-cryptography` 설치가 실패하면 대신 `pip install pycryptodome`을 실행하세요.

## 3. 먼저 테스트 (예약 안 함)

```sh
python ktx_watch.py --test
```

코레일 ID(멤버십번호, 이메일, `010-xxxx-xxxx` 중 하나)와 비밀번호를 입력하면
조건에 맞는 열차 목록이 나옵니다. 목록이 정상적으로 뜨면 준비 끝입니다.

## 4. 실제 감시 시작

```sh
python ktx_watch.py
```

- 25~45초 간격으로 조회하고, 좌석이 나오면 **1장 예약 후 자동 종료**합니다.
- 예약되면 진동, 알림, 음성으로 알려줍니다.
  → **코레일톡 > 예약승차권 조회**에서 결제기한 안에 결제하세요.
  명절에는 결제기한이 짧으니 알림이 오면 바로 결제하세요.
- 충전기를 꽂아두고, Termux 알림의 **Acquire wakelock**을 누르면 더 안정적입니다.
  스크립트도 자동으로 wakelock을 겁니다.
- 멈추려면 `Ctrl` + `C`를 누르세요. Termux 키보드 위 줄의 CTRL 버튼을 쓰면 됩니다.

### 옵션

```sh
python ktx_watch.py --arrive-by 2030      # 도착 한계 시각 변경
python ktx_watch.py --interval 40         # 조회 간격(초) 변경, 최소 15
```

출발역, 좌석 등급 같은 나머지 설정은 `ktx_watch.py` 맨 위 설정 부분에서 바꿀 수 있습니다.

## 주의

- 비밀번호는 저장하지 않습니다. 실행할 때마다 입력합니다.
- 코레일은 자동 조회 프로그램을 약관으로 제한합니다. 간격을 너무 줄이면 계정이나 IP가
  제한될 수 있으니 기본값 이상으로 두세요. 사용 책임은 본인에게 있습니다.
- 코레일톡 **예약대기**도 함께 걸어두세요. 둘 다 돌리면 확률이 올라갑니다.
- 코레일이 앱 API를 바꾸면 동작이 멈출 수 있습니다. `--test`가 실패하면 오류 메시지를 알려주세요.
