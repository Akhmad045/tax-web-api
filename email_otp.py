"""
email_otp.py
------------
Pembuatan kode OTP dan pengiriman email verifikasi.

Konfigurasi SMTP diambil dari environment variable. Untuk Gmail, gunakan
**App Password** (bukan password akun biasa) karena Google memblokir login
SMTP dengan password utama:

    MAIL_USERNAME  = alamat@gmail.com
    MAIL_PASSWORD  = app password 16 karakter
    MAIL_FROM      = alamat@gmail.com
    MAIL_SERVER    = smtp.gmail.com
    MAIL_PORT      = 587
    MAIL_STARTTLS  = True
    MAIL_SSL_TLS   = False

Bila konfigurasi email belum diisi (mis. saat pengembangan lokal), kode OTP
tetap dibuat dan dicetak ke log server agar alur registrasi bisa diuji tanpa
SMTP. Di produksi, isi env agar OTP benar-benar dikirim.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from dotenv import load_dotenv

# Muat .env bila ada (tidak menimpa env yang sudah diset sistem).
load_dotenv()

MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "") or MAIL_USERNAME
MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_STARTTLS = os.environ.get("MAIL_STARTTLS", "True").lower() in ("1", "true", "yes")
MAIL_SSL_TLS = os.environ.get("MAIL_SSL_TLS", "False").lower() in ("1", "true", "yes")


def email_terkonfigurasi() -> bool:
    """True bila kredensial SMTP sudah cukup untuk mengirim email."""
    return bool(MAIL_USERNAME and MAIL_PASSWORD and MAIL_FROM)


def buat_kode_otp(panjang: int = 6) -> str:
    """
    Buat kode OTP numerik acak.

    Memakai ``secrets`` (CSPRNG), bukan ``random``, agar kode tidak bisa
    diprediksi dari keluaran sebelumnya.
    """
    return "".join(str(secrets.randbelow(10)) for _ in range(panjang))


def _konfigurasi():
    """Bangun ConnectionConfig fastapi-mail dari environment."""
    from fastapi_mail import ConnectionConfig

    return ConnectionConfig(
        MAIL_USERNAME=MAIL_USERNAME,
        MAIL_PASSWORD=MAIL_PASSWORD,
        MAIL_FROM=MAIL_FROM,
        MAIL_PORT=MAIL_PORT,
        MAIL_SERVER=MAIL_SERVER,
        MAIL_STARTTLS=MAIL_STARTTLS,
        MAIL_SSL_TLS=MAIL_SSL_TLS,
        MAIL_FROM_NAME="Tim 3 Kalkulator Pajak",
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
    )


def _html_otp(kode: str, username: str, menit: int) -> str:
    """Template HTML sederhana untuk email OTP."""
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;
            padding:24px;border:1px solid #e2e8f0;border-radius:12px">
  <h2 style="color:#1d4ed8;margin:0 0 12px">Verifikasi Email</h2>
  <p style="color:#334155;margin:0 0 16px">
    Hai <strong>{username}</strong>, gunakan kode berikut untuk menyelesaikan
    pendaftaran akun Tim 3 Kalkulator Pajak:
  </p>
  <p style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#0f172a;
            text-align:center;background:#f1f5f9;padding:16px;border-radius:8px;
            margin:0 0 16px">{kode}</p>
  <p style="color:#64748b;font-size:13px;margin:0">
    Kode berlaku {menit} menit. Abaikan email ini bila Anda tidak mendaftar.
  </p>
</div>"""


async def kirim_otp_email(email: str, kode: str, username: str, menit: int = 5) -> bool:
    """
    Kirim kode OTP ke email tujuan.

    Return True bila terkirim. Bila SMTP belum dikonfigurasi, kode dicetak
    ke log dan fungsi mengembalikan False (bukan melempar error) supaya
    endpoint registrasi tetap bisa diuji di lokal.
    """
    if not email_terkonfigurasi():
        print(
            f"[OTP-DEV] SMTP belum dikonfigurasi. Kode untuk {email}: {kode} "
            f"(berlaku {menit} menit)"
        )
        return False

    from fastapi_mail import FastMail, MessageSchema, MessageType

    pesan = MessageSchema(
        subject="Kode Verifikasi - Tim 3 Kalkulator Pajak",
        recipients=[email],
        body=_html_otp(kode, username, menit),
        subtype=MessageType.html,
    )

    fm = FastMail(_konfigurasi())
    await fm.send_message(pesan)
    return True


def validasi_password_kuat(password: str) -> Optional[str]:
    """Return pesan galat bila password terlalu lemah, else None."""
    if len(password) < 6:
        return "Password minimal 6 karakter"
    if len(password) > 72:
        return "Password maksimal 72 karakter"
    return None
