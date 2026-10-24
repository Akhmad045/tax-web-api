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
from datetime import datetime, timezone
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
                dibuat_pada   TEXT    NOT NULL
            )
            """
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
                dibuat_pada     TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )
        conn.commit()


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
                 status_ptkp, ringkasan, total_pph, detail, dibuat_pada)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    modul: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Ambil daftar riwayat terbaru (tanpa kolom ``detail`` agar ringan).

    ``modul`` bila diisi akan memfilter berdasarkan jenis modul.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))

    sql = (
        "SELECT id, modul, judul, nama_wajib, identitas, tahun_pajak, "
        "status_ptkp, ringkasan, total_pph, dibuat_pada FROM riwayat"
    )
    params: List[Any] = []
    if modul:
        sql += " WHERE modul = ?"
        params.append(modul)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_ke_dict(r, sertakan_detail=False) for r in rows]


def hapus_riwayat(riwayat_id: int) -> bool:
    """Hapus satu baris riwayat. Return True bila ada baris terhapus."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM riwayat WHERE id = ?", (riwayat_id,))
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
        "role": row["role"],
        "is_active": bool(row["is_active"]),
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


def buat_user(
    username: str, hashed_password: str, role: str = "user"
) -> Dict[str, Any]:
    """
    Buat user baru. Raise ValueError bila username sudah dipakai
    atau role tidak valid.
    """
    if role not in ROLE_VALID:
        raise ValueError(
            f"Role tidak valid: {role!r}. Pilihan: {', '.join(ROLE_VALID)}"
        )
    if not username or not hashed_password:
        raise ValueError("Username dan password wajib diisi")

    dibuat_pada = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO users (username, hashed_password, role, is_active, dibuat_pada)
                VALUES (?, ?, ?, 1, ?)
                """,
                (username, hashed_password, role, dibuat_pada),
            )
            conn.commit()
            user_id = cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Username {username!r} sudah terdaftar") from exc

    user = ambil_user_by_username(username, sertakan_hash=False)
    if user is None:  # pragma: no cover
        raise RuntimeError(f"Gagal membaca user yang baru dibuat (id={user_id})")
    return user


def jumlah_user() -> int:
    """Hitung jumlah user (dipakai untuk seeding admin default)."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def daftar_user() -> List[Dict[str, Any]]:
    """Daftar seluruh user (tanpa hash password)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, role, is_active, dibuat_pada "
            "FROM users ORDER BY id ASC"
        ).fetchall()
    return [_user_ke_dict(r) for r in rows]

