"""
database.py
-----------
Penyimpanan riwayat perhitungan menggunakan SQLite bawaan Python
(tanpa dependensi tambahan).

Tabel ``riwayat`` menyimpan satu baris per perhitungan yang disimpan
pengguna. Seluruh payload request & response disimpan sebagai JSON di
kolom ``detail`` agar halaman Riwayat bisa menampilkan kembali data
lengkap tanpa perlu join tabel tambahan.

Lokasi file database dapat dioverride lewat environment variable
``TAX_DB_PATH`` (berguna untuk pengujian).

CATATAN DEPLOY (Render free tier): filesystem bersifat *ephemeral* —
data hilang setiap redeploy/restart. Untuk data permanen, mount Render
Disk (mis. ke /var/data) lalu set ``TAX_DB_PATH=/var/data/tax.db``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# LOKASI DATABASE
# ---------------------------------------------------------------------------

_DEFAULT_DB = Path(__file__).resolve().parent / "tax.db"
DB_PATH = Path(os.environ.get("TAX_DB_PATH", _DEFAULT_DB))

# Jenis modul yang valid
MODUL_VALID = ("pph21_bulanan", "pph21_tahunan", "pph_badan")

# Batas jumlah baris yang dikembalikan GET /api/riwayat
MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# KONEKSI
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """Buka koneksi SQLite dengan row factory berbentuk dict."""
    # pastikan folder tujuan ada (mis. bila TAX_DB_PATH menunjuk subfolder)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Buat tabel riwayat bila belum ada. Aman dipanggil berulang."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS riwayat (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                modul         TEXT    NOT NULL,
                judul         TEXT    NOT NULL,
                nama_wajib    TEXT    NOT NULL DEFAULT '',
                identitas     TEXT    NOT NULL DEFAULT '',
                tahun_pajak   TEXT    NOT NULL DEFAULT '',
                status_ptkp   TEXT    NOT NULL DEFAULT '',
                ringkasan     TEXT    NOT NULL DEFAULT '',
                total_pph     REAL    NOT NULL DEFAULT 0,
                detail        TEXT    NOT NULL DEFAULT '{}',
                dibuat_pada   TEXT    NOT NULL,
                username      TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        # Migrasi: database lama belum punya kolom pemilik data.
        kolom_riwayat = {r["name"] for r in conn.execute("PRAGMA table_info(riwayat)")}
        if "username" not in kolom_riwayat:
            conn.execute(
                "ALTER TABLE riwayat ADD COLUMN username TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_riwayat_username "
            "ON riwayat(username, id DESC)"
        )
        # index bantu untuk pengurutan riwayat terbaru
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_riwayat_dibuat ON riwayat(dibuat_pada DESC)"
        )
        # tabel user untuk autentikasi & RBAC
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL UNIQUE,
                hashed_password TEXT    NOT NULL,
                role            TEXT    NOT NULL DEFAULT 'user',
                is_active       INTEGER NOT NULL DEFAULT 1,
                email           TEXT    NOT NULL DEFAULT '',
                is_verified     INTEGER NOT NULL DEFAULT 0,
                dibuat_pada     TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )

        _migrasi_users(conn)

        # kode OTP untuk verifikasi email saat registrasi
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS otp_codes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT    NOT NULL,
                code       TEXT    NOT NULL,
                expires_at TEXT    NOT NULL,
                dibuat_pada TEXT   NOT NULL,
                percobaan  INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Migrasi: tabel lama belum punya penghitung percobaan.
        kolom_otp = {r["name"] for r in conn.execute("PRAGMA table_info(otp_codes)")}
        if "percobaan" not in kolom_otp:
            conn.execute(
                "ALTER TABLE otp_codes ADD COLUMN percobaan INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email)"
        )

        # Data pendaftaran yang menunggu verifikasi OTP. User baru masuk ke
        # tabel users setelah kode terbukti benar.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_registrasi (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL,
                email           TEXT    NOT NULL,
                hashed_password TEXT    NOT NULL,
                dibuat_pada     TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_email "
            "ON pending_registrasi(lower(email))"
        )
        conn.commit()


def _migrasi_users(conn: sqlite3.Connection) -> None:
    """
    Tambahkan kolom baru ke tabel users pada database lama.

    ``CREATE TABLE IF NOT EXISTS`` tidak menambah kolom ke tabel yang sudah
    ada, jadi database dari sebelum fitur registrasi perlu dimigrasi manual.
    ALTER TABLE ADD COLUMN aman dipanggil berkali-kali asalkan dicek dulu.
    """
    kolom_ada = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "email" not in kolom_ada:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "is_verified" not in kolom_ada:
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 0"
        )
    # Akun lama (hasil seeding admin) dianggap terverifikasi.
    conn.execute("UPDATE users SET is_verified = 1 WHERE is_verified = 0 AND role = 'admin'")


# ---------------------------------------------------------------------------
# OPERASI CRUD
# ---------------------------------------------------------------------------

def simpan_riwayat(
    modul: str,
    judul: str,
    nama_wajib: str = "",
    identitas: str = "",
    tahun_pajak: str = "",
    status_ptkp: str = "",
    ringkasan: str = "",
    total_pph: float = 0,
    detail: Optional[Dict[str, Any]] = None,
    username: str = "",
) -> Dict[str, Any]:
    """Simpan satu perhitungan ke tabel riwayat, kembalikan baris tersimpan."""
    if modul not in MODUL_VALID:
        raise ValueError(
            f"Modul tidak dikenal: {modul!r}. Pilihan: {', '.join(MODUL_VALID)}"
        )

    dibuat_pada = datetime.now(timezone.utc).isoformat(timespec="seconds")
    detail_json = json.dumps(detail or {}, ensure_ascii=False, default=str)

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO riwayat
                (modul, judul, nama_wajib, identitas, tahun_pajak,
                 status_ptkp, ringkasan, total_pph, detail, dibuat_pada, username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                modul,
                judul,
                nama_wajib or "",
                identitas or "",
                tahun_pajak or "",
                status_ptkp or "",
                ringkasan or "",
                float(total_pph or 0),
                detail_json,
                dibuat_pada,
                username or "",
            ),
        )
        conn.commit()
        new_id = cur.lastrowid

    row = ambil_riwayat(new_id)
    if row is None:  # pragma: no cover - kondisi tidak mungkin
        raise RuntimeError("Gagal membaca kembali data yang baru disimpan")
    return row


def _row_ke_dict(row: sqlite3.Row, sertakan_detail: bool = True) -> Dict[str, Any]:
    """Konversi sqlite3.Row menjadi dict Python."""
    data: Dict[str, Any] = {
        "id": row["id"],
        "modul": row["modul"],
        "judul": row["judul"],
        "nama_wajib": row["nama_wajib"],
        "identitas": row["identitas"],
        "tahun_pajak": row["tahun_pajak"],
        "status_ptkp": row["status_ptkp"],
        "ringkasan": row["ringkasan"],
        "total_pph": row["total_pph"],
        "dibuat_pada": row["dibuat_pada"],
        # Pemilik data. Aman dipakai untuk baris hasil SELECT * maupun SELECT
        # eksplisit karena keduanya kini menyertakan kolom ini.
        "username": row["username"] if "username" in row.keys() else "",
    }
    if sertakan_detail:
        try:
            data["detail"] = json.loads(row["detail"] or "{}")
        except (json.JSONDecodeError, TypeError):
            data["detail"] = {}
    return data


def ambil_riwayat(riwayat_id: int) -> Optional[Dict[str, Any]]:
    """Ambil satu baris riwayat berdasarkan id."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM riwayat WHERE id = ?", (riwayat_id,)
        ).fetchone()
    return _row_ke_dict(row) if row else None


def daftar_riwayat(
    modul: Optional[str] = None,
    limit: int = 50,
    username: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Ambil daftar riwayat terbaru (tanpa kolom ``detail`` agar ringan).

    ``modul``    : filter jenis modul.
    ``username`` : bila diisi, HANYA baris milik user tersebut. Admin memanggil
                   tanpa parameter ini sehingga melihat semua data.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))

    sql = (
        "SELECT id, modul, judul, nama_wajib, identitas, tahun_pajak, "
        "status_ptkp, ringkasan, total_pph, dibuat_pada, username FROM riwayat"
    )
    kondisi: List[str] = []
    params: List[Any] = []
    if modul:
        kondisi.append("modul = ?")
        params.append(modul)
    if username:
        kondisi.append("username = ?")
        params.append(username)
    if kondisi:
        sql += " WHERE " + " AND ".join(kondisi)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_ke_dict(r, sertakan_detail=False) for r in rows]


def hitung_riwayat(modul: Optional[str] = None, username: Optional[str] = None) -> int:
    """
    Hitung JUMLAH TOTAL baris riwayat (bukan panjang halaman).

    Dipakai agar ``total`` pada respons tidak selalu mentok di ``limit``.
    """
    sql = "SELECT COUNT(*) AS n FROM riwayat"
    kondisi: List[str] = []
    params: List[Any] = []
    if modul:
        kondisi.append("modul = ?")
        params.append(modul)
    if username:
        kondisi.append("username = ?")
        params.append(username)
    if kondisi:
        sql += " WHERE " + " AND ".join(kondisi)

    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["n"]) if row else 0


def ambil_riwayat_milik(riwayat_id: int, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Ambil satu baris riwayat dengan pembatasan pemilik.

    ``username=None`` berarti admin (tanpa filter). Bila diisi, baris milik
    user lain dianggap tidak ada sehingga tidak bisa dibaca/dihapus.
    """
    sql = "SELECT * FROM riwayat WHERE id = ?"
    params: List[Any] = [riwayat_id]
    if username:
        sql += " AND username = ?"
        params.append(username)
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return _row_ke_dict(row) if row else None


def hapus_riwayat(riwayat_id: int, username: Optional[str] = None) -> bool:
    """
    Hapus satu baris riwayat. Return True bila ada baris terhapus.

    ``username`` membatasi penghapusan pada baris milik user tersebut.
    """
    sql = "DELETE FROM riwayat WHERE id = ?"
    params: List[Any] = [riwayat_id]
    if username:
        sql += " AND username = ?"
        params.append(username)
    with get_connection() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# USER & AUTENTIKASI
# ---------------------------------------------------------------------------

ROLE_VALID = ("admin", "user")


def _user_ke_dict(row: sqlite3.Row, sertakan_hash: bool = False) -> Dict[str, Any]:
    """Konversi baris users menjadi dict. Hash hanya disertakan bila diminta."""
    data: Dict[str, Any] = {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"] if "email" in row.keys() else "",
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "is_verified": bool(row["is_verified"]) if "is_verified" in row.keys() else False,
        "dibuat_pada": row["dibuat_pada"],
    }
    if sertakan_hash:
        data["hashed_password"] = row["hashed_password"]
    return data


def ambil_user_by_username(
    username: str, sertakan_hash: bool = True
) -> Optional[Dict[str, Any]]:
    """Ambil user berdasarkan username (case-sensitive)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return _user_ke_dict(row, sertakan_hash=sertakan_hash) if row else None


def ambil_user_by_email(
    email: str, sertakan_hash: bool = False
) -> Optional[Dict[str, Any]]:
    """Ambil user berdasarkan email (dibandingkan case-insensitive)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(email) = lower(?) LIMIT 1", (email,)
        ).fetchone()
    return _user_ke_dict(row, sertakan_hash=sertakan_hash) if row else None


def buat_user(
    username: str,
    hashed_password: str,
    role: str = "user",
    email: str = "",
    is_verified: bool = True,
) -> Dict[str, Any]:
    """
    Buat user baru. Raise ValueError bila username/email sudah dipakai
    atau role tidak valid.

    ``is_verified`` default True agar pembuatan akun lewat panel admin
    langsung bisa dipakai login. Alur registrasi publik mengirim False
    sampai OTP diverifikasi.
    """
    if role not in ROLE_VALID:
        raise ValueError(
            f"Role tidak valid: {role!r}. Pilihan: {', '.join(ROLE_VALID)}"
        )
    if not username or not hashed_password:
        raise ValueError("Username dan password wajib diisi")
    if email and ambil_user_by_email(email) is not None:
        raise ValueError(f"Email {email!r} sudah terdaftar")

    dibuat_pada = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO users
                    (username, hashed_password, role, is_active,
                     email, is_verified, dibuat_pada)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    username,
                    hashed_password,
                    role,
                    email or "",
                    1 if is_verified else 0,
                    dibuat_pada,
                ),
            )
            conn.commit()
            user_id = cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Username {username!r} sudah terdaftar") from exc

    user = ambil_user_by_username(username, sertakan_hash=False)
    if user is None:  # pragma: no cover
        raise RuntimeError(f"Gagal membaca user yang baru dibuat (id={user_id})")
    return user


def verifikasi_user(username: str) -> bool:
    """Tandai user sudah terverifikasi (dipakai setelah OTP benar)."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE users SET is_verified = 1 WHERE username = ?", (username,)
        )
        conn.commit()
        return cur.rowcount > 0


def hapus_user(username: str) -> bool:
    """Hapus user berdasarkan username. Return True bila ada yang terhapus."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        return cur.rowcount > 0


def update_password(email: str, hashed_password: str) -> bool:
    """Perbarui hash password user berdasarkan email. Return True bila terupdate."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE users SET hashed_password = ? WHERE lower(email) = lower(?)",
            (hashed_password, email),
        )
        conn.commit()
        return cur.rowcount > 0


def jumlah_user() -> int:
    """Hitung jumlah user (dipakai untuk seeding admin default)."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def daftar_user() -> List[Dict[str, Any]]:
    """Daftar seluruh user (tanpa hash password)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, email, role, is_active, is_verified, dibuat_pada "
            "FROM users ORDER BY id ASC"
        ).fetchall()
    return [_user_ke_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# OTP (verifikasi email)
# ---------------------------------------------------------------------------

# Masa berlaku kode OTP dalam menit.
OTP_TTL_MINUTES = int(os.environ.get("OTP_TTL_MINUTES", "5"))


def simpan_otp(email: str, code: str, ttl_minutes: int = OTP_TTL_MINUTES) -> str:
    """
    Simpan kode OTP untuk sebuah email dan kembalikan waktu kedaluwarsanya.

    Kode lama untuk email yang sama dihapus lebih dulu agar hanya kode
    terbaru yang berlaku (mencegah kebingungan bila user minta kirim ulang).
    """
    sekarang = datetime.now(timezone.utc)
    kedaluwarsa = sekarang + timedelta(minutes=ttl_minutes)

    with get_connection() as conn:
        conn.execute("DELETE FROM otp_codes WHERE lower(email) = lower(?)", (email,))
        conn.execute(
            """
            INSERT INTO otp_codes (email, code, expires_at, dibuat_pada)
            VALUES (?, ?, ?, ?)
            """,
            (
                email,
                code,
                kedaluwarsa.isoformat(timespec="seconds"),
                sekarang.isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    return kedaluwarsa.isoformat(timespec="seconds")


def ambil_otp(email: str) -> Optional[Dict[str, Any]]:
    """Ambil OTP aktif untuk sebuah email (case-insensitive)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM otp_codes WHERE lower(email) = lower(?) "
            "ORDER BY id DESC LIMIT 1",
            (email,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "code": row["code"],
        "expires_at": row["expires_at"],
        "dibuat_pada": row["dibuat_pada"],
        "percobaan": row["percobaan"] if "percobaan" in row.keys() else 0,
    }


# Batas percobaan salah sebelum kode dihapus. Kode hanya 6 digit (1 juta
# kemungkinan); tanpa batas, penyerang bisa menebak sampai berhasil.
OTP_MAKS_PERCOBAAN = int(os.environ.get("OTP_MAKS_PERCOBAAN", "5"))


def tambah_percobaan_otp(email: str) -> int:
    """
    Naikkan penghitung percobaan salah untuk OTP email ini.

    Return jumlah percobaan setelah ditambah. Bila melewati
    ``OTP_MAKS_PERCOBAAN``, kode langsung dihapus sehingga kode benar pun
    tidak bisa dipakai lagi (user harus minta kirim ulang).
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE otp_codes SET percobaan = percobaan + 1 "
            "WHERE lower(email) = lower(?)",
            (email,),
        )
        row = conn.execute(
            "SELECT percobaan FROM otp_codes WHERE lower(email) = lower(?) "
            "ORDER BY id DESC LIMIT 1",
            (email,),
        ).fetchone()
        jumlah = int(row["percobaan"]) if row else OTP_MAKS_PERCOBAAN
        if jumlah >= OTP_MAKS_PERCOBAAN:
            conn.execute(
                "DELETE FROM otp_codes WHERE lower(email) = lower(?)", (email,)
            )
        conn.commit()
    return jumlah


def otp_kedaluwarsa(expires_at: str) -> bool:
    """True bila waktu kedaluwarsa sudah lewat."""
    try:
        batas = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return True
    if batas.tzinfo is None:
        batas = batas.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > batas


def hapus_otp(email: str) -> None:
    """Hapus OTP milik sebuah email."""
    with get_connection() as conn:
        conn.execute("DELETE FROM otp_codes WHERE lower(email) = lower(?)", (email,))
        conn.commit()


def bersihkan_otp_kedaluwarsa() -> int:
    """Hapus OTP yang sudah lewat masa berlaku. Return jumlah baris terhapus."""
    batas = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM otp_codes WHERE expires_at < ?", (batas,))
        conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# PENDING REGISTRASI
# ---------------------------------------------------------------------------

def simpan_pending_registrasi(
    username: str, email: str, hashed_password: str
) -> None:
    """
    Simpan data pendaftaran sementara (belum jadi user aktif).

    Password sudah di-hash sebelum disimpan, jadi tidak pernah ada
    password mentah di database.
    """
    sekarang = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM pending_registrasi WHERE lower(email) = lower(?)",
            (email,),
        )
        conn.execute(
            """
            INSERT INTO pending_registrasi
                (username, email, hashed_password, dibuat_pada)
            VALUES (?, ?, ?, ?)
            """,
            (username, email, hashed_password, sekarang),
        )
        conn.commit()


def ambil_pending_registrasi(email: str) -> Optional[Dict[str, Any]]:
    """Ambil data pendaftaran yang menunggu verifikasi."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pending_registrasi WHERE lower(email) = lower(?) "
            "ORDER BY id DESC LIMIT 1",
            (email,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "hashed_password": row["hashed_password"],
        "dibuat_pada": row["dibuat_pada"],
    }


def ambil_pending_registrasi_by_username(
    username: str,
) -> Optional[Dict[str, Any]]:
    """Ambil data pendaftaran yang menunggu verifikasi berdasarkan username.

    Tabel pending_registrasi hanya punya unique index pada email, bukan
    username, jadi username yang sama bisa muncul di dua baris pending
    berbeda (email berbeda) bila tidak dicek di sini. Dipakai /api/register
    untuk menolak duplikat username sebelum OTP baru diterbitkan.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pending_registrasi WHERE lower(username) = lower(?) "
            "ORDER BY id DESC LIMIT 1",
            (username,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "hashed_password": row["hashed_password"],
        "dibuat_pada": row["dibuat_pada"],
    }


def hapus_pending_registrasi(email: str) -> None:
    """Hapus data pendaftaran setelah berhasil diverifikasi."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM pending_registrasi WHERE lower(email) = lower(?)", (email,)
        )
        conn.commit()

