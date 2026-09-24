#!/usr/bin/env python3
"""
취소표 확인 알람 (Termux용) - 코레일에는 접속하지 않음

취소표가 잘 풀리는 시각마다 폰을 울려서, 코레일톡에서 직접 조회하게 해준다.
화면이 꺼져도 동작하도록 wake lock을 잡는다.
"""

import subprocess
import time
from datetime import datetime, timedelta

END = datetime(2026, 9, 27, 18, 30)  # 이 시각 이후엔 알람 종료 (마지막 열차 출발 무렵)


def schedule(now):
    """알람 시각 목록.
    - 평소: 07:00~24:00 매시 00분, 30분 (결제기한 만료로 미결제 표가 풀리는 시각)
    - 9/26 18시 이후 ~ 9/27: 15분 간격 (환불표가 가장 많이 나오는 구간)
    """
    times = []
    t = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while t <= END:
        dense = t >= datetime(2026, 9, 26, 18, 0)
        step_ok = t.minute % 15 == 0 if dense else t.minute % 30 == 0
        awake = 7 <= t.hour or (dense and t.hour < 1)
        if step_ok and awake:
            times.append(t)
        t += timedelta(minutes=1)
    return times


def run(cmd):
    try:
        subprocess.run(cmd, timeout=15, check=False)
    except Exception as e:
        print("알림 오류:", e)


def ring(t):
    msg = "지금 코레일톡에서 9/27 김천구미·동대구 → 서울 (20:10 이전 도착) 조회하세요!"
    print(f"\a[{t:%m/%d %H:%M}] 🔔 {msg}", flush=True)
    run([
        "termux-notification", "--id", "korail-alarm",
        "--title", f"🚄 취소표 확인 시간 ({t:%H:%M})",
        "--content", msg,
        "--priority", "max", "--sound",
        "--vibrate", "1000,500,1000,500,1000",
    ])
    run(["termux-vibrate", "-d", "1500", "-f"])


def main():
    run(["termux-wake-lock"])
    now = datetime.now()
    times = schedule(now)
    print(f"알람 {len(times)}개 예약됨. 다음: {times[0]:%m/%d %H:%M}" if times else "남은 알람 없음", flush=True)
    for t in times:
        # 30초씩 나눠 자면서 시각 확인 (절전 때문에 sleep이 밀려도 바로잡힘)
        while datetime.now() < t:
            time.sleep(min(30, max(1, (t - datetime.now()).total_seconds())))
        late = (datetime.now() - t).total_seconds()
        if late < 600:  # 10분 넘게 밀렸으면 그 알람은 건너뜀
            ring(t)
    print("알람 종료. 무사히 표 구하시길!", flush=True)
    run(["termux-wake-unlock"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        run(["termux-wake-unlock"])
        print("\n알람을 종료합니다.")
