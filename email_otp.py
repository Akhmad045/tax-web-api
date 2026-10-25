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

import html
import os
import secrets
from typing import Optional

from dotenv import load_dotenv

# Muat .env bila ada (tidak menimpa env yang sudah diset sistem).
load_dotenv()

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


def _api_key() -> str:
    """
    Ambil kunci API Brevo langsung dari environment SAAT INI.

    Sengaja dibaca per-panggilan, bukan hanya sekali saat modul di-import:
    nilai lama yang ter-cache bisa membuat header mengirim kunci kosong/basi
    (penyebab 401 "Key not found") meski Environment di Render sudah diubah.
    Nilai juga dibersihkan dari spasi, newline, atau tanda kutip yang sering
    ikut tersalin saat menempel kunci di dashboard.
    """
    nilai = os.getenv("BREVO_API_KEY") or os.environ.get("BREVO_API_KEY") or ""
    return nilai.strip().strip('"').strip("'").strip()


def email_terkonfigurasi() -> bool:
    """True bila kunci API Brevo sudah tersedia."""
    return bool(_api_key())


def _peringatan_konfigurasi() -> None:
    """
    Cetak potensi salah konfigurasi sekali saat modul dimuat.

    Ditampilkan lebih awal agar langsung terlihat di Logs Render setelah
    restart, bukan menunggu percobaan registrasi gagal.
    """
    if not _api_key():
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


def _html_otp(
    kode_otp: str,
    nama_user: str,
    menit: int = 5,
    konteks: str = "registrasi",
) -> str:
    """
    Template HTML email OTP.

    Semua styling memakai inline CSS karena Gmail dan klien email lain
    membuang tag ``<style>`` di ``<head>``. Nilai yang disisipkan sudah
    di-escape agar username/kode tidak bisa menyuntikkan markup.

    ``konteks`` membedakan email registrasi dan reset password: sebelumnya
    email reset password pun berisi kalimat "menyelesaikan pendaftaran",
    sehingga user bisa mengira email itu salah dan mengabaikannya.
    """
    nama_aman = html.escape(str(nama_user))
    kode_aman = html.escape(str(kode_otp))
    if konteks == "reset password":
        judul = "Reset Password"
        kalimat = (
            "Gunakan kode berikut untuk mereset password akun "
            "Tim 3 Kalkulator Pajak:"
        )
        abaikan = "Abaikan email ini bila Anda tidak meminta reset password."
    else:
        judul = "Verifikasi Email"
        kalimat = (
            "Gunakan kode berikut untuk menyelesaikan pendaftaran akun "
            "Tim 3 Kalkulator Pajak:"
        )
        abaikan = "Abaikan email ini bila Anda tidak mendaftar."
    return f"""\
<div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
    <h2 style="color: #1a56db; text-align: center;">{judul}</h2>
    <p style="color: #333; font-size: 16px;">Hai <strong>{nama_aman}</strong>,</p>
    <p style="color: #333; font-size: 16px;">{kalimat}</p>
    <div style="background-color: #f3f4f6; padding: 20px; text-align: center; border-radius: 8px; margin: 20px 0;">
        <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #111827;">{kode_aman}</span>
    </div>
    <p style="color: #6b7280; font-size: 14px;">Kode berlaku {menit} menit. {abaikan}</p>
</div>"""


def kirim_otp_email(
    email_tujuan: str,
    kode_otp: str,
    nama_user: str = "",
    menit: int = 5,
    konteks: str = "registrasi",
) -> bool:
    """
    Kirim kode OTP lewat HTTP API Brevo (``POST /v3/smtp/email``).

    Fungsi ini SINKRON (memakai ``httpx.post``). Pemanggil menjalankannya di
    ``BackgroundTasks``, jadi request pengguna tetap tidak menunggu jaringan.

    ``nama_user`` diambil dari data pendaftaran (username). Bila kosong,
    dipakai sapaan netral agar email tidak menampilkan "Hai ,".

    Return True bila Brevo menerima (HTTP 2xx). Bila ``BREVO_API_KEY`` belum
    diisi, email tidak dikirim dan False dikembalikan (bukan exception) agar
    registrasi tetap bisa diuji di lokal.

    Bila pengiriman gagal, respons/kesalahan asli dicetak ke terminal
    (terlihat di Render -> Logs) lalu exception dilempar ulang.
    """
    api_key = _api_key()
    if not api_key:
        # Kode OTP TIDAK dicetak: log Render bisa dibaca pihak lain dan siapa
        # pun yang melihatnya bisa memakai kode untuk membajak akun.
        print(
            f"[OTP-DEV] BREVO_API_KEY belum diisi. Email verifikasi untuk "
            f"{email_tujuan} TIDAK dikirim. Set env BREVO_API_KEY."
        )
        return False

    if not api_key.startswith("xkeysib-"):
        print(
            "[EMAIL-WARN] BREVO_API_KEY tidak diawali 'xkeysib-'. Kemungkinan "
            "bukan API key (mis. kunci SMTP) sehingga Brevo membalas 401."
        )

    import httpx

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }

    subject = (
        "Kode Reset Password Anda"
        if konteks == "reset password"
        else "Kode OTP Anda"
    )
    payload = {
        "sender": {
            "name": BREVO_SENDER_NAME,
            "email": BREVO_SENDER,
        },
        "to": [{"email": email_tujuan}],
        "subject": subject,
        "htmlContent": _html_otp(
            kode_otp, nama_user or "Pengguna", menit, konteks=konteks
        ),
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
        print(f"[EMAIL-ERROR] API key  : {'(terisi)' if api_key else '(KOSONG!)'}")
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
        f"[EMAIL-OK] OTP {konteks} terkirim ke {email_tujuan} via Brevo"
        + (f" (messageId={pesan_id})" if pesan_id else "")
    )
    return True


_PASSWORD_UMUM = {
    "password",
    "password1",
    "password123",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty123",
    "admin123",
    "iloveyou",
}


def validasi_password_kuat(password: str) -> Optional[str]:
    """
    Return pesan galat bila password terlalu lemah, else None.

    Aturan disamakan dengan ``Field(min_length=8)`` di ``schemas.py`` agar
    pesan dari skema Pydantic dan dari sini tidak saling bertentangan.
    """
    if not isinstance(password, str):
        return "Password tidak valid"
    if len(password) < 8:
        return "Password minimal 8 karakter"
    if len(password) > 72:
        # bcrypt hanya memakai 72 byte pertama; lebih dari itu terpotong diam-diam.
        return "Password maksimal 72 karakter"
    if password != password.strip():
        return "Password tidak boleh diawali/diakhiri spasi"
    if password.isdigit():
        return "Password tidak boleh hanya berisi angka"
    if len(set(password)) < 4:
        return "Password terlalu mudah ditebak (variasi karakter kurang)"
    if password.lower() in _PASSWORD_UMUM:
        return "Password terlalu mudah ditebak"
    return None
