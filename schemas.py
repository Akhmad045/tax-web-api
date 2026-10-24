"""
schemas.py
----------
Definisi model Pydantic untuk validasi request & response
pada API kalkulator pajak.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field

# ---------------------------------------------------------------------------
# Enum status PTKP (dipakai sebagai validasi Literal sederhana)
# ---------------------------------------------------------------------------

StatusPTKP = str  # divalidasi di service (TK/0, K/0, K/1, K/2, K/3)


# ---------------------------------------------------------------------------
# REQUEST MODELS
# ---------------------------------------------------------------------------

class PPh21BulananRequest(BaseModel):
    gaji_pokok: float = Field(..., ge=0, description="Gaji pokok per bulan (Rupiah)")
    tunjangan: float = Field(0, ge=0, description="Tunjangan per bulan (Rupiah)")
    bpjs: float = Field(0, ge=0, description="Potongan BPJS per bulan (Rupiah)")
    jht: float = Field(0, ge=0, description="Potongan JHT per bulan (Rupiah)")
    status_ptkp: str = Field(
        "TK/0",
        description="Status PTKP: TK/0, K/0, K/1, K/2, K/3",
        examples=["TK/0"],
    )


class BulanItem(BaseModel):
    """Data penghasilan & potongan untuk satu bulan."""

    gaji_pokok: float = Field(0, ge=0, description="Gaji pokok bulan ini")
    tunjangan: float = Field(0, ge=0, description="Tunjangan bulan ini")
    thr: float = Field(0, ge=0, description="THR bulan ini (biasanya bulan tertentu)")
    bonus: float = Field(0, ge=0, description="Bonus bulan ini")
    bpjs: float = Field(0, ge=0, description="Potongan BPJS bulan ini")
    jht: float = Field(0, ge=0, description="Potongan JHT bulan ini")
    pph_dipotong: float = Field(
        0, ge=0, description="PPh 21 yang sudah dipotong pada bulan ini"
    )


class PPh21TahunanRequest(BaseModel):
    status_ptkp: str = Field(
        "TK/0",
        description="Status PTKP: TK/0, K/0, K/1, K/2, K/3",
        examples=["K/1"],
    )
    bulanan: List[BulanItem] = Field(
        ...,
        min_length=1,
        description="List data penghasilan per bulan (idealnya 12 bulan)",
    )


class PPhBadanRequest(BaseModel):
    omzet: float = Field(..., ge=0, description="Peredaran bruto dari kegiatan usaha utama")
    omzet_lain: float = Field(0, ge=0, description="Peredaran bruto dari kegiatan lain")
    biaya: float = Field(..., ge=0, description="Total biaya (dapat dikurangkan)")
    kredit_pph25: float = Field(0, ge=0, description="Kredit pajak PPh Pasal 25")
    kredit_pph22_23: float = Field(
        0, ge=0, description="Kredit pajak PPh Pasal 22 & 23"
    )


# ---------------------------------------------------------------------------
# RESPONSE MODELS (generik, karena struktur detail cukup bervariasi)
# ---------------------------------------------------------------------------

class PerhitunganBulanan(BaseModel):
    gaji_pokok: float
    tunjangan: float
    bruto: float
    biaya_jabatan: float = 0
    bpjs: float
    jht: float
    total_pengurang: float
    neto: float


class DetailLapisan(BaseModel):
    lapisan: str
    tarif: str
    dasar_pengenaan: float
    pajak: float


class PerhitunganSetahunBulanan(BaseModel):
    bruto_setahun: float
    neto_setahun: float
    ptkp: float
    pkp_sebelum_pembulatan: float
    pkp: float
    detail_tarif_progresif: List[DetailLapisan]
    pph_setahun: float


class HasilBulanan(BaseModel):
    pph21_per_bulan: float
    take_home_pay: float


class PPh21BulananResponse(BaseModel):
    status_ptkp: str
    perhitungan_bulanan: PerhitunganBulanan
    perhitungan_setahun: PerhitunganSetahunBulanan
    hasil: HasilBulanan


class RekapBulanan(BaseModel):
    bulan_ke: int
    bruto: float
    pph_dipotong: float


class HasilTahunan(BaseModel):
    pph21_terutang_setahun: float
    total_pph_dipotong: float
    selisih: float
    status: str
    keterangan: str


class PPh21TahunanResponse(BaseModel):
    status_ptkp: str
    rekap_bulanan: List[RekapBulanan]
    perhitungan_setahun: dict
    hasil: HasilTahunan


class FasilitasPasal31E(BaseModel):
    keterangan: str
    porsi_fasilitas: float
    pkp_dapat_fasilitas: float
    pkp_tidak_dapat_fasilitas: float
    tarif_fasilitas: str
    tarif_non_fasilitas: str
    pph_dari_fasilitas: float
    pph_dari_non_fasilitas: float


class HasilBadan(BaseModel):
    pph_terutang_setahun: float
    kredit_pph25: float
    kredit_pph22_23: float
    total_kredit_pajak: float
    pph_29_kurang_bayar: float
    status: str
    keterangan: str


class PPhBadanResponse(BaseModel):
    perhitungan_laba: dict
    fasilitas_pasal_31e: FasilitasPasal31E
    hasil: HasilBadan


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


# ---------------------------------------------------------------------------
# RIWAYAT (simpan data & riwayat perhitungan)
# ---------------------------------------------------------------------------

class RiwayatSimpanRequest(BaseModel):
    """
    Payload untuk menyimpan satu perhitungan ke database.

    ``detail`` bersifat bebas (JSON) agar bisa menampung payload request
    maupun response lengkap sesuai modul.
    """

    modul: str = Field(
        ...,
        description="Jenis modul: pph21_bulanan | pph21_tahunan | pph_badan",
        examples=["pph21_bulanan"],
    )
    judul: str = Field("", description="Judul tampilan, mis. nama pegawai/perusahaan")
    nama_wajib: str = Field("", description="Nama pegawai / wajib pajak badan")
    identitas: str = Field("", description="NIK atau NPWP (16 digit)")
    tahun_pajak: str = Field("", description="Tahun pajak, mis. 2025")
    status_ptkp: str = Field("", description="Status PTKP (khusus PPh 21)")
    ringkasan: str = Field("", description="Ringkasan singkat hasil perhitungan")
    total_pph: float = Field(0, ge=0, description="Nilai PPh utama (Rupiah)")
    detail: dict = Field(default_factory=dict, description="Payload & hasil lengkap")


class RiwayatItem(BaseModel):
    id: int
    modul: str
    judul: str
    nama_wajib: str
    identitas: str
    tahun_pajak: str
    status_ptkp: str
    ringkasan: str
    total_pph: float
    dibuat_pada: str
    detail: dict = Field(default_factory=dict)


class RiwayatDaftarResponse(BaseModel):
    total: int
    items: List[RiwayatItem]
# ---------------------------------------------------------------------------
# AUTENTIKASI & USER
# ---------------------------------------------------------------------------

class Token(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", description="Selalu 'bearer'")
    expires_in: int = Field(..., description="Masa berlaku token (detik)")
    username: str = Field(..., description="Username yang login")
    role: str = Field(..., description="Role pengguna: admin | user")


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool = True
    dibuat_pada: str = ""


class UserBuatRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=72)
    role: str = Field("user", description="admin | user")


class UserDaftarResponse(BaseModel):
    total: int
    items: List[UserInfo]


# ---------------------------------------------------------------------------
# REGISTRASI & VERIFIKASI OTP
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr = Field(..., description="Email tujuan pengiriman OTP")
    password: str = Field(..., min_length=6, max_length=72)


class VerifyOtpRequest(BaseModel):
    email: EmailStr = Field(..., description="Email yang didaftarkan")
    otp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class PesanResponse(BaseModel):
    message: str
    detail: str = ""
