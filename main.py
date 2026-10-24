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
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import database
import services
from schemas import (
    HealthResponse,
    PPh21BulananRequest,
    PPh21BulananResponse,
    PPh21TahunanRequest,
    PPh21TahunanResponse,
    PPhBadanRequest,
    PPhBadanResponse,
    RiwayatDaftarResponse,
    RiwayatItem,
    RiwayatSimpanRequest,
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
    """Pastikan tabel riwayat tersedia saat aplikasi pertama kali berjalan."""
    database.init_db()


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
            "POST /api/pph21/bulanan",
            "POST /api/pph21/tahunan",
            "POST /api/pph-badan",
            "POST /api/riwayat",
            "GET /api/riwayat",
            "GET /api/riwayat/{id}",
            "DELETE /api/riwayat/{id}",
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
    summary="Daftar riwayat perhitungan (terbaru lebih dulu)",
)
def daftar_riwayat(
    modul: Optional[str] = Query(
        None,
        description="Filter modul: pph21_bulanan | pph21_tahunan | pph_badan",
    ),
    limit: int = Query(50, ge=1, le=200, description="Jumlah maksimum baris"),
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
def detail_riwayat(riwayat_id: int):
    item = database.ambil_riwayat(riwayat_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Riwayat tidak ditemukan")
    return item


@app.delete(
    "/api/riwayat/{riwayat_id}",
    tags=["Riwayat"],
    summary="Hapus satu riwayat",
)
def hapus_riwayat(riwayat_id: int):
    if not database.hapus_riwayat(riwayat_id):
        raise HTTPException(status_code=404, detail="Riwayat tidak ditemukan")
    return {"deleted": True, "id": riwayat_id}


# ---------------------------------------------------------------------------
# ENTRY POINT (opsional: python main.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)