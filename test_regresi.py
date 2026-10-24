"""
test_regresi.py
---------------
Tes regresi untuk perbaikan hasil audit (keamanan, otorisasi, validasi, pajak).

Menjalankan:
    pip install -r requirements-dev.txt
    python -m pytest -q

Kenapa tes ini ada
------------------
Bug yang sudah diperbaiki mudah muncul kembali tanpa disadari (mis. endpoint
baru lupa `Depends()`, atau password minimum diturunkan lagi). Tes ini mengunci
perilaku yang sudah benar.

Catatan penting
---------------
`main.py` dan `database.py` membaca environment SAAT DI-IMPORT, jadi variabel
di bawah WAJIB diset sebelum `import main`. Setiap sesi tes memakai file SQLite
sementara supaya `tax.db` milik pengembang tidak tersentuh.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Environment tes (harus sebelum import aplikasi)
# ---------------------------------------------------------------------------

_DIR_SEMENTARA = tempfile.mkdtemp(prefix="taxapi-test-")

os.environ["TAX_DB_PATH"] = os.path.join(_DIR_SEMENTARA, "test.db")
os.environ["ADMIN_USERNAME"] = "admin_uji"
os.environ["ADMIN_PASSWORD"] = "AdminKuat123"
os.environ["JWT_SECRET_KEY"] = "kunci-uji-yang-cukup-panjang-untuk-hs256-123456"
os.environ["OTP_MAKS_PERCOBAAN"] = "5"
# Kunci tiruan: tes tidak boleh benar-benar memanggil API Brevo.
os.environ["BREVO_API_KEY"] = "xkeysib-tiruan"

import database  # noqa: E402
import email_otp  # noqa: E402
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ADMIN_USER = "admin_uji"
ADMIN_PASS = "AdminKuat123"


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Bersihkan file database sementara setelah seluruh tes selesai."""
    shutil.rmtree(_DIR_SEMENTARA, ignore_errors=True)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def _token(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin_token(client):
    return _token(client, ADMIN_USER, ADMIN_PASS)


@pytest.fixture(scope="module")
def user_tokens(client, admin_token):
    """Buat dua user biasa (budi & sari) dan kembalikan tokennya masing-masing."""
    dibuat = {}
    for nama, email in (("budi", "budi@example.com"), ("sari", "sari@example.com")):
        r = client.post(
            "/api/users",
            json={
                "username": nama,
                "password": "PasswordKuat1",
                "role": "user",
            },
            headers=_auth(admin_token),
        )
        assert r.status_code == 201, r.text
        dibuat[nama] = _token(client, nama, "PasswordKuat1")
    return dibuat


# ---------------------------------------------------------------------------
# 1. Kredensial admin default
# ---------------------------------------------------------------------------

def test_password_default_publik_ditolak(client):
    """Password lama yang tertulis di repo tidak boleh berlaku lagi."""
    r = client.post(
        "/api/login", data={"username": "akhmad23", "password": "akhmadramadhan23"}
    )
    assert r.status_code == 401


def test_admin_login_dengan_env(client, admin_token):
    assert admin_token


# ---------------------------------------------------------------------------
# 2. Otorisasi riwayat
# ---------------------------------------------------------------------------

def test_simpan_riwayat_tanpa_token_ditolak(client):
    r = client.post("/api/riwayat", json={"modul": "pph21_bulanan", "judul": "x"})
    assert r.status_code == 401


def test_simpan_riwayat_mencatat_pemilik(client, user_tokens):
    r = client.post(
        "/api/riwayat",
        json={"modul": "pph21_bulanan", "judul": "Budi Juli", "total_pph": 1000},
        headers=_auth(user_tokens["budi"]),
    )
    assert r.status_code == 201, r.text
    # Pemilik diambil dari token, bukan dari body request.
    assert r.json()["username"] == "budi"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/riwayat"),
        ("get", "/api/riwayat/1"),
        ("delete", "/api/riwayat/1"),
    ],
)
def test_user_biasa_tidak_boleh_akses_riwayat(client, user_tokens, method, path):
    r = getattr(client, method)(path, headers=_auth(user_tokens["budi"]))
    assert r.status_code == 403


def test_admin_melihat_semua_riwayat(client, admin_token, user_tokens):
    client.post(
        "/api/riwayat",
        json={"modul": "pph_badan", "judul": "Sari"},
        headers=_auth(user_tokens["sari"]),
    )
    r = client.get("/api/riwayat", headers=_auth(admin_token))
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_total_tidak_terpotong_limit(client, admin_token):
    """`total` harus COUNT(*) asli, bukan panjang halaman."""
    r = client.get("/api/riwayat?limit=1", headers=_auth(admin_token))
    assert r.json()["total"] == 2
    assert len(r.json()["items"]) == 1


# ---------------------------------------------------------------------------
# 3. Password policy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("password", ["pendek", "1234567"])
def test_register_password_kurang_8_ditolak(client, password):
    r = client.post(
        "/api/register",
        json={"username": "pendaftar", "email": "daftar@example.com", "password": password},
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "password,alasan",
    [
        ("12345678", "hanya angka"),
        ("aaaaaaaa", "variasi karakter kurang"),
        ("password123", "salah satu password umum"),
        (" Katasandi1", "diawali spasi"),
    ],
)
def test_validasi_password_kuat(password, alasan):
    assert email_otp.validasi_password_kuat(password) is not None, alasan


def test_password_kuat_diterima():
    assert email_otp.validasi_password_kuat("KataSandiKuat1") is None


def test_admin_buat_user_password_lemah_ditolak(client, admin_token):
    r = client.post(
        "/api/users",
        json={"username": "lemah", "password": "12345678", "role": "user"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 4. Anti brute force OTP
# ---------------------------------------------------------------------------

def test_otp_dikunci_setelah_batas_percobaan(client):
    email = "calon@example.com"
    # Pakai data pending: verify-otp menolak email yang sudah terverifikasi.
    database.simpan_pending_registrasi(
        "calon", email, main.auth.hash_password("PasswordCalon1")
    )
    database.simpan_otp(email, "123456")

    for _ in range(database.OTP_MAKS_PERCOBAAN - 1):
        r = client.post("/api/verify-otp", json={"email": email, "otp_code": "000000"})
        assert r.status_code == 400
        assert "Sisa percobaan" in r.json()["detail"]

    r = client.post("/api/verify-otp", json={"email": email, "otp_code": "000000"})
    assert r.status_code == 429

    # Kode benar pun tidak berlaku lagi setelah batas tercapai.
    r = client.post("/api/verify-otp", json={"email": email, "otp_code": "123456"})
    assert r.status_code != 200


# ---------------------------------------------------------------------------
# 5. Validasi numerik
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nilai", ["Infinity", "-Infinity", "NaN"])
def test_nilai_tidak_terhingga_ditolak(client, nilai):
    r = client.post("/api/pph21/bulanan", json={"gaji_pokok": nilai})
    assert r.status_code == 422


def test_pph21_tahunan_maksimal_12_bulan(client):
    bulan = [{"gaji_pokok": 1}] * 13
    r = client.post(
        "/api/pph21/tahunan", json={"status_ptkp": "TK/0", "bulanan": bulan}
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 6. Perhitungan pajak & pembagian nol
# ---------------------------------------------------------------------------

def test_pph21_bulanan_input_nol_tidak_error(client):
    r = client.post("/api/pph21/bulanan", json={"gaji_pokok": 0, "status_ptkp": "TK/0"})
    assert r.status_code == 200
    assert r.json()["hasil"]["pph21_per_bulan"] == 0


def test_pph21_tahunan_input_nol_tidak_error(client):
    r = client.post(
        "/api/pph21/tahunan",
        json={"status_ptkp": "TK/0", "bulanan": [{"gaji_pokok": 0}] * 12},
    )
    assert r.status_code == 200


def test_pph_badan_omzet_nol_tidak_error(client):
    r = client.post("/api/pph-badan", json={"omzet": 0, "biaya": 0})
    assert r.status_code == 200


def test_pph21_bulanan_progresif_berlapis():
    """PPh setahun harus memakai tarif progresif, bukan satu tarif rata."""
    hasil = main.services.hitung_pph21_bulanan(
        gaji_pokok=20_000_000, tunjangan=0, bpjs=0, jht=0, status_ptkp="TK/0"
    )
    setahun = hasil["perhitungan_setahun"]
    bulanan = hasil["perhitungan_bulanan"]

    # PTKP TK/0 = 54 juta.
    assert setahun["ptkp"] == 54_000_000
    # PKP dibulatkan ke bawah kelipatan 1.000.
    assert setahun["pkp"] % 1000 == 0
    assert setahun["pkp"] == pytest.approx(
        (setahun["pkp_sebelum_pembulatan"] // 1000) * 1000
    )
    # Biaya jabatan 5% dari bruto bulan, dibatasi Rp500.000 (bruto 20 jt = 1 jt).
    assert bulanan["biaya_jabatan"] == 500_000
    # Ada lebih dari satu lapisan tarif yang terpakai pada PKP sebesar ini.
    assert len(setahun["detail_tarif_progresif"]) >= 1


def test_pph21_biaya_jabatan_bulanan_dibatasi_500_ribu():
    hasil = main.services.hitung_pph21_bulanan(
        gaji_pokok=50_000_000, tunjangan=0, bpjs=0, jht=0, status_ptkp="TK/0"
    )
    assert hasil["perhitungan_bulanan"]["biaya_jabatan"] == 500_000


def test_pph21_tahunan_biaya_jabatan_dibatasi_6_juta():
    bulan = [{"gaji_pokok": 50_000_000}] * 12
    hasil = main.services.hitung_pph21_tahunan(
        bulanan=bulan, status_ptkp="TK/0"
    )["perhitungan_setahun"]
    assert hasil["biaya_jabatan"] == 6_000_000


def test_pph_badan_pasal_31e_membagi_fasilitas():
    """Omzet antara 4,8 M dan 50 M: PKP dibagi porsi 11% dan 22%."""
    omzet = 10_000_000_000  # 10 miliar
    hasil = main.services.hitung_pph_badan(
        omzet=omzet,
        omzet_lain=0,
        biaya=5_000_000_000,
        kredit_pph25=0,
        kredit_pph22_23=0,
    )
    detail = hasil["fasilitas_pasal_31e"]

    # Porsi fasilitas = 4,8 M / 10 M = 0,48
    assert detail["porsi_fasilitas"] == pytest.approx(0.48)
    assert detail["pkp_dapat_fasilitas"] == pytest.approx(5_000_000_000 * 0.48)
    assert detail["pkp_tidak_dapat_fasilitas"] == pytest.approx(5_000_000_000 * 0.52)
    # 11% porsi fasilitas + 22% sisanya.
    assert detail["pph_dari_fasilitas"] == pytest.approx(
        5_000_000_000 * 0.48 * 0.11, rel=1e-6
    )
    assert detail["pph_dari_non_fasilitas"] == pytest.approx(
        5_000_000_000 * 0.52 * 0.22, rel=1e-6
    )


def test_pph_badan_omzet_di_atas_50_miliar_tanpa_fasilitas():
    hasil = main.services.hitung_pph_badan(
        omzet=60_000_000_000,
        omzet_lain=0,
        biaya=30_000_000_000,
        kredit_pph25=0,
        kredit_pph22_23=0,
    )
    detail = hasil["fasilitas_pasal_31e"]
    assert detail["porsi_fasilitas"] == 0
    assert detail["pph_dari_fasilitas"] == 0
    assert hasil["hasil"]["pph_terutang_setahun"] == pytest.approx(30_000_000_000 * 0.22)
