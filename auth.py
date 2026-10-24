"""
auth.py
-------
Autentikasi & otorisasi berbasis JWT untuk Tax Web API.

Alur:
    1. POST /api/login  -> verifikasi username+password, kembalikan access_token.
    2. Frontend menyimpan token dan mengirim header ``Authorization: Bearer <token>``.
    3. Dependency ``get_current_user`` / ``get_current_admin_user`` memvalidasi
       token lalu mengambil baris user dari tabel ``users``.

Password di-hash dengan bcrypt (library ``bcrypt`` dipakai langsung, bukan
lewat passlib — passlib 1.7.4 tidak kompatibel dengan bcrypt 5.x).

Secret key JWT diambil dari env ``JWT_SECRET_KEY``. Bila tidak diisi, dipakai
nilai default khusus pengembangan; WAJIB diganti di produksi karena token
yang ditandatangani dengan kunci default bisa dipalsukan siapa saja.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

import database

# ---------------------------------------------------------------------------
# KONFIGURASI
# ---------------------------------------------------------------------------

ALGORITHM = "HS256"

# Masa berlaku token: 8 jam (bisa dioverride lewat env JWT_EXPIRE_MINUTES).
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "480"))

# Default hanya untuk pengembangan lokal. Di produksi set JWT_SECRET_KEY.
JWT_SECRET_KEY = os.environ.get(
    "JWT_SECRET_KEY",
    "dev-secret-key-jangan-dipakai-di-produksi-ganti-lewat-env",
)

# Token yang dipakai kalau env belum diisi — dipakai untuk peringatan di log.
_USING_DEFAULT_SECRET = not os.environ.get("JWT_SECRET_KEY")


# tokenUrl dipakai agar tombol "Authorize" di /docs berfungsi.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


# ---------------------------------------------------------------------------
# PASSWORD
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash password dengan bcrypt. Batas bcrypt adalah 72 byte."""
    # bcrypt hanya memakai 72 byte pertama; potong agar tidak error di bcrypt 5.x.
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Bandingkan password mentah dengan hash tersimpan."""
    if not hashed:
        return False
    try:
        pw = password.encode("utf-8")[:72]
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # hash rusak / format tak dikenal -> anggap gagal, jangan 500.
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(
    data: Dict[str, Any], expires_minutes: Optional[int] = None
) -> str:
    """Buat JWT dengan klaim ``sub``, ``role``, dan ``exp``."""
    menit = JWT_EXPIRE_MINUTES if expires_minutes is None else expires_minutes
    payload = dict(data)
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=menit)
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode & verifikasi token. Raise HTTPException 401 bila tidak valid."""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak valid atau sudah kedaluwarsa",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# DEPENDENCY
# ---------------------------------------------------------------------------

def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
) -> Dict[str, Any]:
    """
    Validasi token lalu kembalikan baris user dari database.

    Selalu membaca ulang dari database supaya perubahan role / penghapusan
    akun langsung berlaku tanpa menunggu token kedaluwarsa.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak ditemukan. Silakan login.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak memuat identitas pengguna",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = database.ambil_user_by_username(str(username))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Pengguna tidak ditemukan atau sudah dihapus",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun dinonaktifkan",
        )
    return user


def get_current_admin_user(
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Lanjutan dari get_current_user: hanya role 'admin' yang lolos."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak: endpoint ini hanya untuk admin",
        )
    return user


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Cek username+password. Return baris user bila cocok, else None."""
    user = database.ambil_user_by_username(username)
    if user is None:
        # Tetap jalankan hash untuk menyamakan waktu respons (anti user-enumeration).
        verify_password(password, "$2b$12$" + "x" * 53)
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user
