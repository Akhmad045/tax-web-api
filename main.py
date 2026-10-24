"""
main.py
-------
Entry point API kalkulator pajak berbasis FastAPI.

Endpoint:
    GET  /                      -> info API
    GET  /health                -> health check
    POST /api/pph21/bulanan     -> hitung PPh 21 pegawai tetap (bulanan)
    POST /api/pph21/tahunan     -> hitung PPh 21 setahun (rekap 12 bulan)
    POST /api/pph-badan         -> hitung PPh Badan (PPh 29) + Pasal 31E
    POST /api/riwayat           -> simpan satu perhitungan ke database
    GET  /api/riwayat           -> daftar riwayat (bisa difilter ?modul=)
    GET  /api/riwayat/{id}      -> detail satu riwayat
    DELETE /api/riwayat/{id}    -> hapus satu riwayat

Menjalankan:
    uvicorn main:app --reload
"""

from __future__ import annotations

import os
import secrets
import traceback
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm

import auth
import database
import email_otp
import services
from schemas import (
    ForgotPasswordRequest,
    HealthResponse,
    PesanResponse,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyOtpRequest,
    PPh21BulananRequest,
    PPh21BulananResponse,
    PPh21TahunanRequest,
    PPh21TahunanResponse,
    PPhBadanRequest,
    PPhBadanResponse,
    RiwayatDaftarResponse,
    RiwayatItem,
    RiwayatSimpanRequest,
    Token,
    UserBuatRequest,
    UserDaftarResponse,
    UserInfo,
)

app = FastAPI(
    title="Tax Web API",
    description=(
        "Backend API kalkulator pajak: PPh 21 (bulanan & tahunan) "
        "dan PPh Badan (PPh 29 dengan fasilitas Pasal 31E)."
    ),
    version="1.0.0",
)

# Izinkan akses dari frontend (CORS).
#
# Daftar origin yang diizinkan diambil dari env CORS_ORIGINS (dipisah koma).
# Bila env tidak diisi, dipakai daftar default domain produksi di bawah.
#
#   CORS_ORIGINS="https://tim3kalkulatorpajak.web.id,https://www.tim3kalkulatorpajak.web.id,http://localhost:5173"
#
# Mengisi env CORS_ORIGINS="*" hanya untuk testing singkat: semua origin
# diizinkan. Jangan dipakai di produksi.
#
# PENTING: allow_credentials TIDAK boleh True selama allow_origins=["*"].
# Spesifikasi CORS melarang wildcard pada kredensial; browser akan menolak
# respons dan aplikasi tampak "gagal koneksi" meski server hidup.
CORS_ORIGINS_DEFAULT = [
    "https://tim3kalkulatorpajak.web.id",
    "https://www.tim3kalkulatorpajak.web.id",
    "http://localhost:5173",
]

_origins_env = os.environ.get("CORS_ORIGINS", "").strip()
if _origins_env:
    _origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
else:
    _origins = list(CORS_ORIGINS_DEFAULT)
_izinkan_semua = "*" in _origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=not _izinkan_semua,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    """
    Siapkan database saat aplikasi pertama kali berjalan.

    - Membuat tabel ``riwayat`` dan ``users`` bila belum ada.
    - Membuat akun admin default HANYA bila tabel users masih kosong.
      Ini membuat restart/redeploy tidak menimpa password yang sudah diubah.

    Akun default bisa dioverride lewat env ADMIN_USERNAME / ADMIN_PASSWORD.
    """
    database.init_db()

    if auth._USING_DEFAULT_SECRET:
        print(
            "[PERINGATAN] JWT_SECRET_KEY belum diset. Token memakai kunci default "
            "pengembangan dan TIDAK AMAN untuk produksi. Set env JWT_SECRET_KEY "
            "di dashboard Render."
        )

    if database.jumlah_user() == 0:
        admin_username = os.environ.get("ADMIN_USERNAME", "akhmad23")
        admin_password = os.environ.get("ADMIN_PASSWORD", "akhmadramadhan23")
        try:
            database.buat_user(
                username=admin_username,
                hashed_password=auth.hash_password(admin_password),
                role="admin",
            )
            print(f"[INFO] Akun admin default dibuat: {admin_username}")
        except ValueError as exc:  # pragma: no cover - balapan start
            print(f"[INFO] Admin default tidak dibuat: {exc}")


# ---------------------------------------------------------------------------
# ROOT & HEALTH
# ---------------------------------------------------------------------------

@app.get("/", tags=["Info"])
def root():
    return {
        "service": "Tax Web API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": [
            "POST /api/login",
            "GET  /api/me",
            "GET  /api/users",
            "POST /api/users",
            "POST /api/pph21/bulanan",
            "POST /api/pph21/tahunan",
            "POST /api/pph-badan",
            "POST /api/riwayat",
            "GET  /api/riwayat  (admin)",
            "GET  /api/riwayat/{id}  (admin)",
            "DELETE /api/riwayat/{id}  (admin)",
        ],
    }


@app.get("/health", response_model=HealthResponse, tags=["Info"])
def health():
    return {"status": "ok", "service": "tax-web-api", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# PPh 21 - BULANAN
# ---------------------------------------------------------------------------

@app.post(
    "/api/pph21/bulanan",
    response_model=PPh21BulananResponse,
    tags=["PPh 21"],
    summary="Hitung PPh 21 pegawai tetap per bulan",
)
def pph21_bulanan(payload: PPh21BulananRequest):
    try:
        return services.hitung_pph21_bulanan(
            gaji_pokok=payload.gaji_pokok,
            tunjangan=payload.tunjangan,
            bpjs=payload.bpjs,
            jht=payload.jht,
            status_ptkp=payload.status_ptkp,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# PPh 21 - TAHUNAN
# ---------------------------------------------------------------------------

@app.post(
    "/api/pph21/tahunan",
    response_model=PPh21TahunanResponse,
    tags=["PPh 21"],
    summary="Hitung PPh 21 setahun dari rekap 12 bulan & status Kurang/Lebih bayar",
)
def pph21_tahunan(payload: PPh21TahunanRequest):
    try:
        bulanan_dicts = [item.model_dump() for item in payload.bulanan]
        return services.hitung_pph21_tahunan(
            bulanan=bulanan_dicts,
            status_ptkp=payload.status_ptkp,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# PPh BADAN
# ---------------------------------------------------------------------------

@app.post(
    "/api/pph-badan",
    response_model=PPhBadanResponse,
    tags=["PPh Badan"],
    summary="Hitung PPh Badan setahun & PPh 29 (kurang bayar) dengan Pasal 31E",
)
def pph_badan(payload: PPhBadanRequest):
    try:
        return services.hitung_pph_badan(
            omzet=payload.omzet,
            omzet_lain=payload.omzet_lain,
            biaya=payload.biaya,
            kredit_pph25=payload.kredit_pph25,
            kredit_pph22_23=payload.kredit_pph22_23,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# REGISTRASI & VERIFIKASI OTP
# ---------------------------------------------------------------------------

@app.post(
    "/api/register",
    response_model=PesanResponse,
    tags=["Auth"],
    summary="Daftar akun baru, kirim kode OTP ke email",
)
async def register(payload: RegisterRequest):
    """
    Tahap 1 registrasi.

    User BELUM dibuat di tabel users pada langkah ini — data ditahan dulu
    sampai OTP diverifikasi. Kode OTP dikirim ke email tujuan.

    Catatan: menahan pembuatan user sampai verifikasi mencegah username
    "dibajak" oleh orang yang mendaftar dengan email palsu.
    """
    username = payload.username.strip()
    email = str(payload.email).strip()

    galat_pw = email_otp.validasi_password_kuat(payload.password)
    if galat_pw:
        raise HTTPException(status_code=422, detail=galat_pw)

    if database.ambil_user_by_username(username) is not None:
        raise HTTPException(status_code=409, detail="Username sudah terdaftar")
    if database.ambil_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="Email sudah terdaftar")

    # Buang OTP kedaluwarsa agar tabel tidak menumpuk.
    database.bersihkan_otp_kedaluwarsa()

    kode = email_otp.buat_kode_otp()
    # Simpan dulu data pendaftaran (password sudah di-hash) supaya tahap
    # verifikasi punya bahan untuk membuat user.
    database.simpan_pending_registrasi(
        username=username,
        email=email,
        hashed_password=auth.hash_password(payload.password),
    )
    database.simpan_otp(email, kode, ttl_minutes=database.OTP_TTL_MINUTES)

    try:
        terkirim = await email_otp.kirim_otp_email(
            email=email,
            kode=kode,
            username=username,
            menit=database.OTP_TTL_MINUTES,
        )
    except Exception as exc:  # noqa: BLE001
        # SMTP gagal (kredensial salah, jaringan, dsb). Hapus OTP agar
        # tidak ada kode "yatim" yang tidak pernah sampai ke pengguna.
        database.hapus_otp(email)
        # Cetak error asli + traceback ke log server (Render: tab Logs) supaya
        # penyebabnya bisa dilihat, bukan hanya pesan 503 ke pengguna.
        print(f"[ERROR] Gagal mengirim OTP registrasi ke {email}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise HTTPException(
            status_code=503,
            detail="Gagal mengirim email verifikasi. Coba lagi beberapa saat.",
        ) from exc

    if terkirim:
        detail = "Kode verifikasi telah dikirim ke email Anda."
    else:
        detail = (
            "SMTP belum dikonfigurasi di server. Kode OTP dicetak di log "
            "server untuk keperluan pengujian."
        )

    return {"message": "Registrasi berhasil, silakan verifikasi email", "detail": detail}


@app.post(
    "/api/verify-otp",
    response_model=PesanResponse,
    tags=["Auth"],
    summary="Verifikasi kode OTP dan aktifkan akun",
)
def verify_otp(payload: VerifyOtpRequest):
    """
    Tahap 2 registrasi.

    Memeriksa kode OTP lalu membuat user dengan ``is_verified=True``.
    Kode yang salah/kedaluwarsa tidak menghapus data pendaftaran, hanya
    menolak permintaan sehingga user bisa mencoba lagi (atau daftar ulang
    untuk mendapat kode baru).
    """
    email = str(payload.email).strip()
    kode = payload.otp_code.strip()

    if database.ambil_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="Email sudah terverifikasi")

    otp = database.ambil_otp(email)
    if otp is None:
        raise HTTPException(
            status_code=404,
            detail="Kode OTP tidak ditemukan. Silakan registrasi ulang.",
        )
    if database.otp_kedaluwarsa(otp["expires_at"]):
        database.hapus_otp(email)
        raise HTTPException(
            status_code=410,
            detail="Kode OTP sudah kedaluwarsa. Silakan registrasi ulang.",
        )
    # Perbandingan constant-time agar tidak bisa ditebak lewat timing.
    if not secrets.compare_digest(str(otp["code"]), str(kode)):
        raise HTTPException(status_code=400, detail="Kode OTP salah")

    # OTP benar, tapi user belum tersimpan. Karena /api/register menahan
    # pembuatan user, data pendaftaran perlu diambil dari tempat lain.
    pending = database.ambil_pending_registrasi(email)
    if pending is None:
        raise HTTPException(
            status_code=410,
            detail="Data pendaftaran tidak ditemukan atau sudah kedaluwarsa. "
            "Silakan registrasi ulang.",
        )

    try:
        user = database.buat_user(
            username=pending["username"],
            hashed_password=pending["hashed_password"],
            role="user",
            email=email,
            is_verified=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    database.hapus_otp(email)
    database.hapus_pending_registrasi(email)

    return {
        "message": "Verifikasi berhasil, akun sudah aktif",
        "detail": f"Silakan login dengan username {user['username']}.",
    }


# ---------------------------------------------------------------------------
# LUPA / RESET PASSWORD
# ---------------------------------------------------------------------------

@app.post(
    "/api/forgot-password",
    response_model=PesanResponse,
    tags=["Auth"],
    summary="Kirim kode OTP untuk reset password",
)
async def forgot_password(payload: ForgotPasswordRequest):
    """
    Mengirim kode OTP ke email pemilik akun untuk keperluan reset password.

    Sengaja TIDAK membocorkan apakah email terdaftar atau tidak: untuk email
    yang tidak dikenal tetap mengembalikan pesan sukses yang sama, sehingga
    penyerang tidak bisa memakai endpoint ini untuk memeriksa email mana yang
    punya akun.
    """
    email = str(payload.email).strip()

    database.bersihkan_otp_kedaluwarsa()

    if database.ambil_user_by_email(email) is None:
        return {
            "message": "Jika email terdaftar, kode reset password telah dikirim",
            "detail": "Periksa kotak masuk dan folder spam Anda.",
        }

    user = database.ambil_user_by_email(email)

    kode = email_otp.buat_kode_otp()
    database.simpan_otp(email, kode, ttl_minutes=database.OTP_TTL_MINUTES)

    try:
        terkirim = await email_otp.kirim_otp_email(
            email=email,
            kode=kode,
            username=user["username"],
            menit=database.OTP_TTL_MINUTES,
        )
    except Exception as exc:  # noqa: BLE001
        database.hapus_otp(email)
        # Sama seperti alur registrasi: tampilkan error aslinya di log.
        print(
            "[ERROR] Gagal mengirim OTP reset password ke "
            f"{email}: {type(exc).__name__}: {exc}"
        )
        traceback.print_exc()
        raise HTTPException(
            status_code=503,
            detail="Gagal mengirim email reset password. Coba lagi beberapa saat.",
        ) from exc

    if terkirim:
        detail = "Kode reset password telah dikirim ke email Anda."
    else:
        detail = (
            "SMTP belum dikonfigurasi di server. Kode OTP dicetak di log "
            "server untuk keperluan pengujian."
        )

    return {
        "message": "Jika email terdaftar, kode reset password telah dikirim",
        "detail": detail,
    }


@app.post(
    "/api/reset-password",
    response_model=PesanResponse,
    tags=["Auth"],
    summary="Verifikasi OTP lalu ganti password",
)
def reset_password(payload: ResetPasswordRequest):
    """
    Memeriksa OTP lalu mengganti password user menjadi ``new_password``.

    Password baru disimpan sebagai hash bcrypt; password lama langsung tidak
    berlaku karena hash-nya ditimpa (token JWT lama tetap valid sampai
    kedaluwarsa — ini konsekuensi token stateless).
    """
    email = str(payload.email).strip()
    kode = payload.otp_code.strip()

    galat_pw = email_otp.validasi_password_kuat(payload.new_password)
    if galat_pw:
        raise HTTPException(status_code=422, detail=galat_pw)

    if database.ambil_user_by_email(email) is None:
        raise HTTPException(status_code=404, detail="Email tidak terdaftar")

    otp = database.ambil_otp(email)
    if otp is None:
        raise HTTPException(
            status_code=404,
            detail="Kode OTP tidak ditemukan. Silakan minta kode baru.",
        )
    if database.otp_kedaluwarsa(otp["expires_at"]):
        database.hapus_otp(email)
        raise HTTPException(
            status_code=410,
            detail="Kode OTP sudah kedaluwarsa. Silakan minta kode baru.",
        )
    if not secrets.compare_digest(str(otp["code"]), str(kode)):
        raise HTTPException(status_code=400, detail="Kode OTP salah")

    if not database.update_password(email, auth.hash_password(payload.new_password)):
        raise HTTPException(status_code=404, detail="Email tidak terdaftar")

    database.hapus_otp(email)

    return {
        "message": "Password berhasil diubah",
        "detail": "Silakan login dengan password baru Anda.",
    }


# ---------------------------------------------------------------------------
# AUTENTIKASI
# ---------------------------------------------------------------------------

@app.post(
    "/api/login",
    response_model=Token,
    tags=["Auth"],
    summary="Login dan dapatkan access token JWT (berlaku 8 jam)",
)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = auth.authenticate_user(form_data.username, form_data.password)
    if user is None:
        # Pesan sengaja umum: jangan bocorkan apakah username terdaftar.
        raise HTTPException(
            status_code=401,
            detail="Username atau password salah",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Akun dinonaktifkan")

    token = auth.create_access_token(
        {"sub": user["username"], "role": user["role"]}
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": auth.JWT_EXPIRE_MINUTES * 60,
        "username": user["username"],
        "role": user["role"],
    }


@app.get(
    "/api/me",
    response_model=UserInfo,
    tags=["Auth"],
    summary="Info pengguna yang sedang login",
)
def me(user: dict = Depends(auth.get_current_user)):
    return user


# ---------------------------------------------------------------------------
# USER MANAGEMENT (khusus admin)
# ---------------------------------------------------------------------------

@app.get(
    "/api/users",
    response_model=UserDaftarResponse,
    tags=["Auth"],
    summary="Daftar user (hanya admin)",
)
def daftar_user(_admin: dict = Depends(auth.get_current_admin_user)):
    items = database.daftar_user()
    return {"total": len(items), "items": items}


@app.post(
    "/api/users",
    response_model=UserInfo,
    status_code=201,
    tags=["Auth"],
    summary="Buat user baru (hanya admin)",
)
def tambah_user(
    payload: UserBuatRequest,
    _admin: dict = Depends(auth.get_current_admin_user),
):
    try:
        return database.buat_user(
            username=payload.username,
            hashed_password=auth.hash_password(payload.password),
            role=payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# RIWAYAT (Database)
# ---------------------------------------------------------------------------

@app.post(
    "/api/riwayat",
    response_model=RiwayatItem,
    status_code=201,
    tags=["Riwayat"],
    summary="Simpan satu perhitungan ke database",
)
def simpan_riwayat(payload: RiwayatSimpanRequest):
    try:
        return database.simpan_riwayat(
            modul=payload.modul,
            judul=payload.judul or payload.nama_wajib or "Tanpa Nama",
            nama_wajib=payload.nama_wajib,
            identitas=payload.identitas,
            tahun_pajak=payload.tahun_pajak,
            status_ptkp=payload.status_ptkp,
            ringkasan=payload.ringkasan,
            total_pph=payload.total_pph,
            detail=payload.detail,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get(
    "/api/riwayat",
    response_model=RiwayatDaftarResponse,
    tags=["Riwayat"],
    summary="Daftar riwayat perhitungan (khusus admin)",
)
def daftar_riwayat(
    modul: Optional[str] = Query(
        None,
        description="Filter modul: pph21_bulanan | pph21_tahunan | pph_badan",
    ),
    limit: int = Query(50, ge=1, le=200, description="Jumlah maksimum baris"),
    _admin: dict = Depends(auth.get_current_admin_user),
):
    if modul and modul not in database.MODUL_VALID:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Modul tidak dikenal: {modul!r}. "
                f"Pilihan: {', '.join(database.MODUL_VALID)}"
            ),
        )
    items = database.daftar_riwayat(modul=modul, limit=limit)
    return {"total": len(items), "items": items}


@app.get(
    "/api/riwayat/{riwayat_id}",
    response_model=RiwayatItem,
    tags=["Riwayat"],
    summary="Detail satu riwayat beserta payload & hasil lengkap",
)
def detail_riwayat(
    riwayat_id: int,
    _admin: dict = Depends(auth.get_current_admin_user),
):
    item = database.ambil_riwayat(riwayat_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Riwayat tidak ditemukan")
    return item


@app.delete(
    "/api/riwayat/{riwayat_id}",
    tags=["Riwayat"],
    summary="Hapus satu riwayat",
)
def hapus_riwayat(
    riwayat_id: int,
    _admin: dict = Depends(auth.get_current_admin_user),
):
    if not database.hapus_riwayat(riwayat_id):
        raise HTTPException(status_code=404, detail="Riwayat tidak ditemukan")
    return {"deleted": True, "id": riwayat_id}


# ---------------------------------------------------------------------------
# ENTRY POINT (opsional: python main.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)