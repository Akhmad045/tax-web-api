"""
email_otp.py
------------
Pembuatan kode OTP dan pengiriman email verifikasi.

Pengiriman email memakai **HTTP API Brevo** (api.brevo.com), bukan SMTP.
Alasannya: server Render memblokir port SMTP (587 maupun 465) sehingga email
tidak pernah terkirim. HTTP lewat port 443 selalu terbuka.

Konfigurasi diambil dari environment variable:

    BREVO_API_KEY   = kunci API dari dashboard Brevo (SMTP & API -> API Keys)
    BREVO_SENDER    = alamat email pengirim yang SUDAH diverifikasi di Brevo
    BREVO_SENDER_NAME = (opsional) nama pengirim

``BREVO_SENDER`` harus berupa alamat yang sudah terdaftar/terverifikasi sebagai
sender di Brevo, kalau tidak API akan menolak dengan 400
"unrecognised IP address" / "sender not valid".

Bila kunci API belum diisi (mis. saat pengembangan lokal), kode OTP tetap
dibuat dan dicetak ke log server agar alur registrasi bisa diuji tanpa Brevo.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from dotenv import load_dotenv

# Muat .env bila ada (tidak menimpa env yang sudah diset sistem).
load_dotenv()

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
# Alamat pengirim WAJIB alamat yang sudah diverifikasi sebagai sender di Brevo.
BREVO_SENDER = (
    os.environ.get("BREVO_SENDER", "").strip()
    or "akhmadfitriyanramadhan23@gmail.com"
)
BREVO_SENDER_NAME = (
    os.environ.get("BREVO_SENDER_NAME", "").strip() or "Tim 3 Kalkulator Pajak"
)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"

# Timeout HTTP (detik). API Brevo biasanya merespons < 2 detik; 20 detik sudah
# sangat longgar dan mencegah request menggantung selamanya.
BREVO_TIMEOUT = 20.0


def email_terkonfigurasi() -> bool:
    """True bila kunci API Brevo sudah tersedia."""
    return bool(BREVO_API_KEY)


def _peringatan_konfigurasi() -> None:
    """
    Cetak potensi salah konfigurasi sekali saat modul dimuat.

    Ditampilkan lebih awal agar langsung terlihat di Logs Render setelah
    restart, bukan menunggu percobaan registrasi gagal.
    """
    if not BREVO_API_KEY:
        print(
            "[EMAIL-WARN] BREVO_API_KEY belum diisi. Email OTP tidak akan "
            "dikirim; kode hanya dicetak ke log ([OTP-DEV])."
        )
        return
    if not BREVO_SENDER:
        print(
            "[EMAIL-WARN] BREVO_SENDER belum diisi. Pengiriman akan gagal "
            "dengan 400 dari Brevo (sender tidak dikenal)."
        )


_peringatan_konfigurasi()


def buat_kode_otp(panjang: int = 6) -> str:
    """
    Buat kode OTP numerik acak.

    Memakai ``secrets`` (CSPRNG), bukan ``random``, agar kode tidak bisa
    diprediksi dari keluaran sebelumnya.
    """
    return "".join(str(secrets.randbelow(10)) for _ in range(panjang))


def kirim_otp_email(
    email_tujuan: str,
    kode_otp: str,
    username: str = "",
    menit: int = 5,
) -> bool:
    """
    Kirim kode OTP lewat HTTP API Brevo (``POST /v3/smtp/email``).

    Fungsi ini SINKRON (memakai ``httpx.post``). Pemanggil menjalankannya di
    ``BackgroundTasks``, jadi request pengguna tetap tidak menunggu jaringan.

    Return True bila Brevo menerima (HTTP 2xx). Bila ``BREVO_API_KEY`` belum
    diisi, kode dicetak ke log dan False dikembalikan (bukan exception) agar
    registrasi tetap bisa diuji di lokal.

    Bila pengiriman gagal, respons/kesalahan asli dicetak ke terminal
    (terlihat di Render -> Logs) lalu exception dilempar ulang.
    """
    if not email_terkonfigurasi():
        print(
            f"[OTP-DEV] BREVO_API_KEY belum diisi. Kode untuk {email_tujuan}: "
            f"{kode_otp} (berlaku {menit} menit). Email TIDAK dikirim."
        )
        return False

    import httpx

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    payload = {
        "sender": {
            "name": "Tim 3 Kalkulator Pajak",
            "email": BREVO_SENDER,
        },
        "to": [{"email": email_tujuan}],
        "subject": "Kode OTP Anda",
        "htmlContent": f"<p>Kode OTP Anda adalah: <b>{kode_otp}</b></p>",
    }

    try:
        with httpx.Client(timeout=BREVO_TIMEOUT) as client:
            respon = client.post(BREVO_URL, headers=headers, json=payload)
            respon.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        # Log selengkap mungkin: status HTTP + body balasan Brevo biasanya
        # langsung menyebut penyebabnya (mis. "sender not valid",
        # "unrecognised IP address", API key salah).
        print("[EMAIL-ERROR] ==================== GAGAL KIRIM EMAIL ====================")
        print("[EMAIL-ERROR] Provider : Brevo HTTP API")
        print(f"[EMAIL-ERROR] URL      : {BREVO_URL}")
        print(f"[EMAIL-ERROR] Ke       : {email_tujuan}")
        print(f"[EMAIL-ERROR] Dari     : {BREVO_SENDER_NAME} <{BREVO_SENDER}>")
        print(f"[EMAIL-ERROR] API key  : {'(terisi)' if BREVO_API_KEY else '(KOSONG!)'}")
        print(f"[EMAIL-ERROR] Tipe     : {type(exc).__name__}")
        print(f"[EMAIL-ERROR] Pesan    : {exc}")

        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is not None:
            print(f"[EMAIL-ERROR] Status   : {status}")
            try:
                print(f"[EMAIL-ERROR] Balasan  : {exc.response.text}")
            except Exception:  # noqa: BLE001
                pass

        print("[EMAIL-ERROR] =======================================================")
        raise

    pesan_id = ""
    try:
        pesan_id = respon.json().get("messageId", "")
    except Exception:  # noqa: BLE001
        pass

    print(
        f"[EMAIL-OK] OTP terkirim ke {email_tujuan} via Brevo"
        + (f" (messageId={pesan_id})" if pesan_id else "")
    )
    return True


def validasi_password_kuat(password: str) -> Optional[str]:
    """Return pesan galat bila password terlalu lemah, else None."""
    if len(password) < 6:
        return "Password minimal 6 karakter"
    if len(password) > 72:
        return "Password maksimal 72 karakter"
    return None
