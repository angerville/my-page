#!/usr/bin/env python3
"""
KTX 취소표 감시 + 자동 예약 스크립트 (안드로이드 Termux용)

- 지정한 출발역들 -> 도착역 열차를 주기적으로 조회
- 도착 시각 조건에 맞는 열차에 좌석이 나오면 즉시 "예약"(결제 전 좌석 확보)
- 폰 알림/진동으로 알려줌 -> 코레일톡 앱에서 기한 내 직접 결제

결제는 자동으로 하지 않는다. 예약만 잡고 결제는 사람이 한다.

코레일 모바일 API 부분은 korail2 (carpedm20, BSD) / srtgo (lapis42, MIT)
코드를 참고해 필요한 부분만 옮겨 왔다.
"""

import argparse
import base64
import getpass
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime

import requests

# ------------------------------------------------------------------
# 설정 (필요하면 여기만 고치면 됨)
# ------------------------------------------------------------------
DATE = "20260927"                  # 탑승일 YYYYMMDD
DEPARTURES = ["김천구미", "동대구"]  # 출발역 후보
ARRIVAL = "서울"                    # 도착역
ARRIVE_BY = "2010"                 # 이 시각(HHMM)까지 도착하는 열차만
SEARCH_FROM = "060000"             # 이 시각(HHMMSS) 이후 출발 열차부터 조회
SEAT_OPTION = "GENERAL_FIRST"      # GENERAL_FIRST / GENERAL_ONLY / SPECIAL_FIRST / SPECIAL_ONLY
INTERVAL = (25, 45)                # 한 바퀴 조회 후 쉬는 시간(초) 범위, 너무 줄이지 말 것

# ------------------------------------------------------------------
# 코레일 모바일 API
# ------------------------------------------------------------------
KORAIL_MOBILE = "https://smart.letskorail.com:443/classes/com.korail.mobile"
API = {
    "login": f"{KORAIL_MOBILE}.login.Login",
    "search": f"{KORAIL_MOBILE}.seatMovie.ScheduleView",
    "reserve": f"{KORAIL_MOBILE}.certification.TicketReservation",
    "reservation_view": f"{KORAIL_MOBILE}.reservation.ReservationView",
    "code": f"{KORAIL_MOBILE}.common.code.do",
}
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; SM-S912N Build/UP1A.231005.007)",
    "Host": "smart.letskorail.com",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
}
DEVICE = "AD"
VERSION = "240531001"
KEY = "korail1234567890"
TRAIN_TYPE_ALL = "109"

NEED_LOGIN_CODES = {"P058"}
NO_RESULT_CODES = {"P100", "WRG000000", "WRD000061", "WRT300005"}
SOLD_OUT_CODES = {"IRT010110", "ERR211161"}


class KorailError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(f"{msg} ({code})")
        self.code = code


class NeedToLogin(KorailError):
    pass


class NoResults(KorailError):
    pass


class SoldOut(KorailError):
    pass


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """pycryptodome 또는 cryptography 중 설치된 쪽을 사용."""
    pad_len = 16 - len(data) % 16
    padded = data + bytes([pad_len]) * pad_len
    try:
        from Crypto.Cipher import AES

        return AES.new(key, AES.MODE_CBC, iv).encrypt(padded)
    except ImportError:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return enc.update(padded) + enc.finalize()


class Train:
    def __init__(self, d):
        self.train_type = d.get("h_trn_clsf_cd")
        self.train_type_name = d.get("h_trn_clsf_nm") or ""
        self.train_group = d.get("h_trn_gp_cd")
        self.train_no = d.get("h_trn_no")
        self.dep_name = d.get("h_dpt_rs_stn_nm")
        self.dep_code = d.get("h_dpt_rs_stn_cd")
        self.dep_date = d.get("h_dpt_dt")
        self.dep_time = d.get("h_dpt_tm") or "000000"
        self.arr_name = d.get("h_arv_rs_stn_nm")
        self.arr_code = d.get("h_arv_rs_stn_cd")
        self.arr_date = d.get("h_arv_dt")
        self.arr_time = d.get("h_arv_tm") or "000000"
        self.run_date = d.get("h_run_dt")
        self.special_seat = d.get("h_spe_rsv_cd")
        self.general_seat = d.get("h_gen_rsv_cd")

    def has_general_seat(self):
        return self.general_seat == "11"

    def has_special_seat(self):
        return self.special_seat == "11"

    def has_seat(self):
        return self.has_general_seat() or self.has_special_seat()

    def __str__(self):
        seat = []
        seat.append("일반O" if self.has_general_seat() else "일반X")
        seat.append("특실O" if self.has_special_seat() else "특실X")
        return (
            f"[{self.train_type_name[:4]} {self.train_no}] "
            f"{self.dep_name} {hhmm(self.dep_time)} -> {self.arr_name} {hhmm(self.arr_time)} "
            f"({', '.join(seat)})"
        )


def hhmm(t):
    return f"{t[:2]}:{t[2:4]}"


class Korail:
    def __init__(self, korail_id, korail_pw):
        self.korail_id = korail_id
        self.korail_pw = korail_pw
        self.membership_number = None
        self.name = None
        self.errors = 0
        self._new_session()

    def _new_session(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)

    @staticmethod
    def _check(j):
        if j.get("strResult") == "FAIL":
            code, msg = j.get("h_msg_cd"), j.get("h_msg_txt")
            if code in NEED_LOGIN_CODES:
                raise NeedToLogin(msg, code)
            if code in NO_RESULT_CODES:
                raise NoResults(msg, code)
            if code in SOLD_OUT_CODES:
                raise SoldOut(msg, code)
            raise KorailError(msg, code)
        return j

    def _enc_password(self):
        r = self.s.post(API["code"], data={"code": "app.login.cphd"}, timeout=15)
        j = r.json()
        info = j.get("app.login.cphd")
        if j.get("strResult") != "SUCC" or not info:
            raise KorailError("비밀번호 암호화 키를 못 받음", j.get("h_msg_cd"))
        key = info["key"]
        enc = aes_cbc_encrypt(key.encode(), key[:16].encode(), self.korail_pw.encode())
        return info["idx"], base64.b64encode(base64.b64encode(enc)).decode()

    def login(self):
        self._new_session()
        kid = self.korail_id
        if "@" in kid:
            flg = "5"  # 이메일
        elif kid.count("-") == 2:
            flg = "4"  # 전화번호 010-xxxx-xxxx
        else:
            flg = "2"  # 멤버십 번호
        idx, pw = self._enc_password()
        data = {
            "Device": DEVICE,
            "Version": VERSION,
            "Key": KEY,
            "txtMemberNo": kid,
            "txtPwd": pw,
            "txtInputFlg": flg,
            "idx": idx,
        }
        j = self.s.post(API["login"], data=data, timeout=15).json()
        if j.get("strResult") == "SUCC" and j.get("strMbCrdNo"):
            self.membership_number = j["strMbCrdNo"]
            self.name = j.get("strCustNm")
            return True
        raise KorailError(j.get("h_msg_txt") or "로그인 실패", j.get("h_msg_cd"))

    def search(self, dep, arr, date, from_time):
        params = {
            "Device": DEVICE,
            "Version": VERSION,
            "Sid": "",
            "txtMenuId": "11",
            "radJobId": "1",
            "selGoTrain": TRAIN_TYPE_ALL,
            "txtTrnGpCd": TRAIN_TYPE_ALL,
            "txtGoStart": dep,
            "txtGoEnd": arr,
            "txtGoAbrdDt": date,
            "txtGoHour": from_time,
            "txtPsgFlg_1": 1,
            "txtPsgFlg_2": 0,
            "txtPsgFlg_3": 0,
            "txtPsgFlg_4": 0,
            "txtPsgFlg_5": 0,
            "txtSeatAttCd_2": "000",
            "txtSeatAttCd_3": "000",
            "txtSeatAttCd_4": "015",
            "ebizCrossCheck": "N",
            "srtCheckYn": "N",
            "rtYn": "N",
            "adjStnScdlOfrFlg": "N",
            "mbCrdNo": self.membership_number,
        }
        j = self._check(self.s.get(API["search"], params=params, timeout=15).json())
        return [Train(t) for t in j.get("trn_infos", {}).get("trn_info", [])]

    def reserve(self, train, option):
        special = {
            "GENERAL_ONLY": False,
            "SPECIAL_ONLY": True,
            "GENERAL_FIRST": not train.has_general_seat(),
            "SPECIAL_FIRST": train.has_special_seat(),
        }[option]
        if special and not train.has_special_seat():
            raise SoldOut("특실 없음")
        if not special and not train.has_general_seat():
            raise SoldOut("일반실 없음")
        params = {
            "Device": DEVICE,
            "Version": VERSION,
            "Key": KEY,
            "txtMenuId": "11",
            "txtJobId": "1101",
            "txtGdNo": "",
            "hidFreeFlg": "N",
            "txtTotPsgCnt": 1,
            "txtSeatAttCd1": "000",
            "txtSeatAttCd2": "000",
            "txtSeatAttCd3": "000",
            "txtSeatAttCd4": "015",
            "txtSeatAttCd5": "000",
            "txtStndFlg": "N",
            "txtSrcarCnt": "0",
            "txtJrnyCnt": "1",
            "txtJrnySqno1": "001",
            "txtJrnyTpCd1": "11",
            "txtDptDt1": train.dep_date,
            "txtDptRsStnCd1": train.dep_code,
            "txtDptTm1": train.dep_time,
            "txtArvRsStnCd1": train.arr_code,
            "txtTrnNo1": train.train_no,
            "txtRunDt1": train.run_date,
            "txtTrnClsfCd1": train.train_type,
            "txtTrnGpCd1": train.train_group,
            "txtPsrmClCd1": "2" if special else "1",
            "txtChgFlg1": "",
            "txtJrnySqno2": "",
            "txtJrnyTpCd2": "",
            "txtDptDt2": "",
            "txtDptRsStnCd2": "",
            "txtDptTm2": "",
            "txtArvRsStnCd2": "",
            "txtTrnNo2": "",
            "txtRunDt2": "",
            "txtTrnClsfCd2": "",
            "txtPsrmClCd2": "",
            "txtChgFlg2": "",
            # 어른 1명
            "txtPsgTpCd1": "1",
            "txtDiscKndCd1": "000",
            "txtCompaCnt1": 1,
            "txtCardCode_1": "",
            "txtCardNo_1": "",
            "txtCardPw_1": "",
        }
        j = self._check(self.s.get(API["reserve"], params=params, timeout=15).json())
        return j.get("h_pnr_no"), ("특실" if special else "일반실")

    def buy_deadline(self, pnr_no):
        """예약 목록에서 해당 예약의 결제 기한을 찾는다. 실패해도 예약 자체엔 영향 없음."""
        try:
            params = {"Device": DEVICE, "Version": VERSION, "Key": KEY}
            j = self._check(self.s.get(API["reservation_view"], params=params, timeout=15).json())
            for jr in j.get("jrny_infos", {}).get("jrny_info", []):
                for t in jr.get("train_infos", {}).get("train_info", []):
                    if t.get("h_pnr_no") == pnr_no:
                        d, tm = t.get("h_ntisu_lmt_dt", ""), t.get("h_ntisu_lmt_tm", "")
                        if len(d) == 8 and len(tm) >= 4:
                            return f"{int(d[4:6])}/{int(d[6:])} {hhmm(tm)}"
        except Exception:
            pass
        return None


# ------------------------------------------------------------------
# 알림
# ------------------------------------------------------------------
def has_cmd(name):
    return shutil.which(name) is not None


def notify(title, content, loud=False):
    print(f"\n🔔 {title}\n{content}\n\a", flush=True)
    if has_cmd("termux-notification"):
        args = ["termux-notification", "--title", title, "--content", content, "--id", "ktxwatch"]
        if loud:
            args += ["--priority", "max", "--vibrate", "500,300,500,300,500", "--sound"]
        subprocess.run(args, check=False)
    if loud:
        if has_cmd("termux-vibrate"):
            for _ in range(3):
                subprocess.run(["termux-vibrate", "-d", "800", "-f"], check=False)
                time.sleep(0.4)
        if has_cmd("termux-tts-speak"):
            subprocess.run(["termux-tts-speak", "기차표 예약됐어요. 코레일톡에서 결제하세요."], check=False)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------
# 감시 루프
# ------------------------------------------------------------------
def candidate_trains(korail, dep):
    """dep -> ARRIVAL 열차 중 ARRIVE_BY 이전 도착 열차를 모두 모은다(페이지 넘기며)."""
    limit = ARRIVE_BY + "00"
    result, seen = [], set()
    from_time = SEARCH_FROM
    for _ in range(6):  # 한 번 조회에 약 10편씩 나오므로 몇 페이지면 충분
        try:
            trains = korail.search(dep, ARRIVAL, DATE, from_time)
        except NoResults:
            break
        new = [t for t in trains if t.train_no not in seen and t.dep_date == DATE]
        if not new:
            break
        for t in new:
            seen.add(t.train_no)
            if t.arr_date == DATE and t.arr_time <= limit:
                result.append(t)
        last = max(trains, key=lambda t: t.dep_time)
        if last.arr_time > limit or last.arr_date != DATE:
            break
        # 마지막 열차 출발 1분 뒤부터 다음 페이지 조회
        h, m = int(last.dep_time[:2]), int(last.dep_time[2:4]) + 1
        if m == 60:
            h, m = h + 1, 0
        if h >= 24:
            break
        from_time = f"{h:02d}{m:02d}00"
        time.sleep(random.uniform(1.0, 2.0))
    return result


def run(korail, test=False):
    cycle = 0
    while True:
        cycle += 1
        found_any = False
        for dep in DEPARTURES:
            try:
                trains = candidate_trains(korail, dep)
            except NeedToLogin:
                log("세션 만료 -> 다시 로그인")
                korail.login()
                trains = candidate_trains(korail, dep)

            if test or cycle == 1:
                log(f"{dep} -> {ARRIVAL}, {hhmm(ARRIVE_BY)} 이전 도착 열차 {len(trains)}편")
                for t in trains:
                    print("    ", t)

            for t in trains:
                if not t.has_seat():
                    continue
                found_any = True
                if test:
                    log(f"(테스트 모드라 예약 안 함) 좌석 있음: {t}")
                    continue
                try:
                    pnr, cls = korail.reserve(t, SEAT_OPTION)
                except SoldOut:
                    log(f"한발 늦음(매진): {t}")
                    continue
                except NeedToLogin:
                    korail.login()
                    try:
                        pnr, cls = korail.reserve(t, SEAT_OPTION)
                    except SoldOut:
                        continue
                deadline = korail.buy_deadline(pnr)
                msg = (
                    f"{t.dep_name} {hhmm(t.dep_time)} -> {t.arr_name} {hhmm(t.arr_time)} "
                    f"{t.train_type_name} {t.train_no} ({cls})\n"
                    f"예약번호 {pnr}"
                    + (f", 결제기한 {deadline}" if deadline else "")
                    + "\n지금 코레일톡 > 예약승차권 조회에서 결제하세요!"
                )
                notify("🚄 KTX 예약 성공!", msg, loud=True)
                return True
            time.sleep(random.uniform(1.0, 3.0))

        if test:
            if not found_any:
                log("지금은 조건에 맞는 빈 좌석 없음. 테스트 끝.")
            return False

        korail.errors = 0  # 한 바퀴 정상 완료
        wait = random.uniform(*INTERVAL)
        log(f"#{cycle} 빈 좌석 없음. {wait:.0f}초 후 다시 조회")
        time.sleep(wait)


def main():
    global DATE, ARRIVE_BY, INTERVAL
    p = argparse.ArgumentParser(description="KTX 취소표 감시 + 자동 예약")
    p.add_argument("--test", action="store_true", help="로그인·조회만 한 번 해보고 예약은 하지 않음")
    p.add_argument("--date", default=DATE, help=f"탑승일 YYYYMMDD (기본 {DATE})")
    p.add_argument("--arrive-by", default=ARRIVE_BY, help=f"도착 한계 시각 HHMM (기본 {ARRIVE_BY})")
    p.add_argument("--interval", type=int, default=None, help="조회 간격(초), 최소 15")
    args = p.parse_args()
    DATE, ARRIVE_BY = args.date, args.arrive_by
    if args.interval:
        n = max(15, args.interval)
        INTERVAL = (n, n + 15)

    korail_id = os.environ.get("KORAIL_ID") or input("코레일 ID (멤버십번호 / 이메일 / 010-xxxx-xxxx): ").strip()
    korail_pw = os.environ.get("KORAIL_PW") or getpass.getpass("코레일 비밀번호 (화면에 안 보임): ")

    korail = Korail(korail_id, korail_pw)
    korail.login()
    log(f"로그인 성공: {korail.name}")
    log(f"조건: {DATE[4:6]}/{DATE[6:]} {' / '.join(DEPARTURES)} -> {ARRIVAL}, {hhmm(ARRIVE_BY)}까지 도착")

    if has_cmd("termux-wake-lock") and not args.test:
        subprocess.run(["termux-wake-lock"], check=False)

    while True:
        try:
            done = run(korail, test=args.test)
            if done or args.test:
                break
        except KeyboardInterrupt:
            log("중단")
            break
        except (requests.RequestException, ValueError, KorailError) as e:
            # 네트워크 오류, 비정상 응답 등: 점점 길게 쉬고 재로그인 후 재시도
            korail.errors += 1
            wait = min(300, 20 * korail.errors)
            log(f"오류: {e} -> {wait}초 후 재시도 ({korail.errors}회째)")
            if korail.errors == 5:
                notify("KTX 감시 오류", f"오류가 계속 나요: {e}")
            time.sleep(wait)
            try:
                korail.login()
            except Exception as e2:
                log(f"재로그인 실패: {e2}")
            continue

    if has_cmd("termux-wake-unlock"):
        subprocess.run(["termux-wake-unlock"], check=False)


if __name__ == "__main__":
    try:
        main()
    except KorailError as e:
        print(f"\n실패: {e}")
        sys.exit(1)
