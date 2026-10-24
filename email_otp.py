"""
email_otp.py
------------
Pembuatan kode OTP dan pengiriman email verifikasi.

Konfigurasi SMTP diambil dari environment variable. Untuk Gmail, gunakan
**App Password** (bukan password akun biasa) karena Google memblokir login
SMTP dengan password utama:

    MAIL_USERNAME  = alamat@gmail.com
    MAIL_PASSWORD  = app password 16 karakter
    MAIL_FROM      = Tim 3 Kalkulator Pajak <alamat@gmail.com>
    MAIL_SERVER    = smtp.gmail.com
    MAIL_PORT      = 587
    MAIL_STARTTLS  = True
    MAIL_SSL_TLS   = False

Port 587 + STARTTLS dipakai secara sengaja: port 465 (SMTPS/implicit TLS)
sering diblokir pada hosting gratis seperti Render, sehingga email tidak
pernah terkirim walau kredensial benar.

Bila konfigurasi email belum diisi (mis. saat pengembangan lokal), kode OTP
tetap dibuat dan dicetak ke log server agar alur registrasi bisa diuji tanpa
SMTP. Di produksi, isi env agar OTP benar-benar dikirim.
"""

from __future__ import annotations

import os
import re
import secrets
import traceback
from email.utils import parseaddr
from typing import Optional

from dotenv import load_dotenv

# Muat .env bila ada (tidak menimpa env yang sudah diset sistem).
load_dotenv()

MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "").strip()
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "").strip()
MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com").strip() or "smtp.gmail.com"

# Server kadang diisi "smtp.gmail.com:465" atau berisi skema URL. Ambil host
# saja agar koneksi tidak menembak hostname yang tidak valid.
if "://" in MAIL_SERVER:
    MAIL_SERVER = MAIL_SERVER.split("://", 1)[1]
MAIL_SERVER = MAIL_SERVER.split("/")[0].split(":")[0] or "smtp.gmail.com"

# --- Port: SELALU STARTTLS di 587 -------------------------------------------
# Bila env MAIL_PORT diisi 465 (atau MAIL_SSL_TLS=True), paksa kembali ke 587
# + STARTTLS. Ini mencegah deploy lama/Render yang masih menyimpan 465 membuat
# email gagal terkirim tanpa jejak yang jelas.
_port_env = (os.environ.get("MAIL_PORT") or "587").strip()
try:
    MAIL_PORT = int(_port_env)
except ValueError:
    MAIL_PORT = 587

if MAIL_PORT != 587:
    print(
        f"[EMAIL-WARN] MAIL_PORT={MAIL_PORT} diabaikan; dipaksa 587 (STARTTLS) "
        "karena port 465 sering diblokir hosting gratis seperti Render."
    )
    MAIL_PORT = 587

MAIL_STARTTLS = True
MAIL_SSL_TLS = False

APP_NAME = "Tim 3 Kalkulator Pajak"


def _normalisasi_mail_from(nilai: str, fallback: str) -> tuple[str, str]:
    """
    Pastikan MAIL_FROM memakai format "Nama Aplikasi <email@domain>".

    Menerima tiga bentuk masukan:
      * "email@gmail.com"                 -> "Tim 3 Kalkulator Pajak <email>"
      * "Nama <email@gmail.com>"          -> dipakai apa adanya
      * '"Nama, Inc" <email@domain.com>'  -> nama diberi tanda kutip bila perlu
    """
    nilai = (nilai or "").strip()
    if not nilai:
        return APP_NAME, fallback

    nama, alamat = parseaddr(nilai)
    if not alamat:
        # Bukan alamat email yang valid; jangan kirim sampah ke SMTP.
        return APP_NAME, fallback

    nama = (nama or "").strip()
    if not nama:
        nama = APP_NAME
    # Kutip nama yang mengandung karakter khusus agar header tetap valid.
    if re.search(r'[",<>;:@]', nama) and not nama.startswith('"'):
        nama = '"' + nama.replace('"', "") + '"'
    return nama, alamat


_MAIL_FROM_NAME, _MAIL_FROM_ADDRESS = _normalisasi_mail_from(
    os.environ.get("MAIL_FROM", ""), MAIL_USERNAME
)
# Alamat lengkap, dipakai bila library butuh MAIL_FROM mentah.
MAIL_FROM = f"{_MAIL_FROM_NAME} <{_MAIL_FROM_ADDRESS}>" if _MAIL_FROM_ADDRESS else ""


def email_terkonfigurasi() -> bool:
    """True bila kredensial SMTP sudah cukup untuk mengirim email."""
    return bool(MAIL_USERNAME and MAIL_PASSWORD and _MAIL_FROM_ADDRESS)


def _peringatan_konfigurasi() -> None:
    """
    Cetak potensi salah konfigurasi sekali saat modul dimuat.

    Kesalahan yang paling sering terjadi pada deploy Render, dan sengaja
    ditampilkan lebih awal (bukan menunggu SMTP gagal) agar langsung terlihat
    di Logs setelah restart:
      * App Password Gmail bukan 16 karakter (masih pakai password akun).
      * MAIL_FROM berbeda domain/akun dari MAIL_USERNAME (Gmail menolak
        pengirim yang bukan milik akun yang login -> "Sender address
        rejected").
    """
    if not email_terkonfigurasi():
        return

    if MAIL_SERVER.endswith("gmail.com"):
        bersih = MAIL_PASSWORD.replace(" ", "")
        if len(bersih) != 16:
            print(
                f"[EMAIL-WARN] MAIL_PASSWORD panjangnya {len(bersih)} karakter; "
                "App Password Gmail seharusnya 16 karakter. Password akun biasa "
                "TIDAK bisa dipakai untuk SMTP."
            )
        if "@" in MAIL_USERNAME:
            domain_login = MAIL_USERNAME.rsplit("@", 1)[1].lower()
            domain_from = _MAIL_FROM_ADDRESS.rsplit("@", 1)[-1].lower()
            if domain_login != domain_from:
                print(
                    f"[EMAIL-WARN] MAIL_FROM ({MAIL_FROM}) memakai domain berbeda "
                    f"dari MAIL_USERNAME ({MAIL_USERNAME}). Gmail akan menolak "
                    "pengirim tersebut."
                )


_peringatan_konfigurasi()



def buat_kode_otp(panjang: int = 6) -> str:
    """
    Buat kode OTP numerik acak.

    Memakai ``secrets`` (CSPRNG), bukan ``random``, agar kode tidak bisa
    diprediksi dari keluaran sebelumnya.
    """
    return "".join(str(secrets.randbelow(10)) for _ in range(panjang))


def _konfigurasi():
    """
    Bangun ConnectionConfig fastapi-mail dari environment.

    Selalu STARTTLS di port 587. Nilai MAIL_PORT/MAIL_STARTTLS/MAIL_SSL_TLS
    sengaja tidak dibaca dari env agar konfigurasi 465 yang tersisa di hosting
    tidak lagi membuat pengiriman gagal.
    """
    from fastapi_mail import ConnectionConfig

    return ConnectionConfig(
        MAIL_USERNAME=MAIL_USERNAME,
        MAIL_PASSWORD=MAIL_PASSWORD,
        MAIL_FROM=_MAIL_FROM_ADDRESS,
        MAIL_PORT=587,
        MAIL_SERVER=MAIL_SERVER,
        MAIL_STARTTLS=True,
        MAIL_SSL_TLS=False,
        MAIL_FROM_NAME=_MAIL_FROM_NAME,
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

    Bila SMTP dikonfigurasi tetapi pengiriman gagal, error asli dicetak ke
    terminal/log (terlihat di dashboard Render -> Logs) lalu exception
    dilempar ulang agar pemanggil bisa menampilkan pesan yang tepat.
    """
    if not email_terkonfigurasi():
        # Bukan silent failure: penyebabnya dicetak eksplisit ke log, termasuk
        # variabel mana yang kosong, supaya salah konfigurasi di Render
        # langsung kelihatan di tab Logs.
        kosong = [
            nama
            for nama, nilai in (
                ("MAIL_USERNAME", MAIL_USERNAME),
                ("MAIL_PASSWORD", MAIL_PASSWORD),
                ("MAIL_FROM", _MAIL_FROM_ADDRESS),
            )
            if not nilai
        ]
        print(
            f"[OTP-DEV] SMTP belum dikonfigurasi (env kosong: {', '.join(kosong)}). "
            f"Kode untuk {email}: {kode} (berlaku {menit} menit). "
            "Email TIDAK dikirim."
        )
        return False

    from fastapi_mail import FastMail, MessageSchema, MessageType

    pesan = MessageSchema(
        subject="Kode Verifikasi - Tim 3 Kalkulator Pajak",
        recipients=[email],
        body=_html_otp(kode, username, menit),
        subtype=MessageType.html,
    )

    try:
        fm = FastMail(_konfigurasi())
        await fm.send_message(pesan)
    except Exception as exc:  # noqa: BLE001
        # Log detail selengkap mungkin supaya penyebabnya jelas di Render:
        # kredensial salah, App Password belum diset, port diblokir, timeout,
        # sertifikat TLS ditolak, dsb.
        print("[EMAIL-ERROR] ==================== GAGAL KIRIM EMAIL ====================")
        print(f"[EMAIL-ERROR] Ke       : {email}")
        print(f"[EMAIL-ERROR] Dari     : {MAIL_FROM}")
        print(f"[EMAIL-ERROR] Server   : {MAIL_SERVER}:587 (STARTTLS)")
        print(f"[EMAIL-ERROR] Username : {MAIL_USERNAME or '(kosong)'}")
        print(f"[EMAIL-ERROR] Password : {'(terisi)' if MAIL_PASSWORD else '(KOSONG!)'}")
        print(f"[EMAIL-ERROR] Tipe     : {type(exc).__name__}")
        print(f"[EMAIL-ERROR] Pesan    : {exc}")
        # Beberapa error SMTP menyimpan kode balasan server.
        kode_smtp = getattr(exc, "code", None) or getattr(exc, "errno", None)
        if kode_smtp is not None:
            print(f"[EMAIL-ERROR] Kode SMTP: {kode_smtp}")
        print("[EMAIL-ERROR] Traceback:")
        traceback.print_exc()
        print("[EMAIL-ERROR] =======================================================")
        raise

    print(f"[EMAIL-OK] OTP terkirim ke {email} via {MAIL_SERVER}:587 (STARTTLS)")
    return True


def validasi_password_kuat(password: str) -> Optional[str]:
    """Return pesan galat bila password terlalu lemah, else None."""
    if len(password) < 6:
        return "Password minimal 6 karakter"
    if len(password) > 72:
        return "Password maksimal 72 karakter"
    return None
