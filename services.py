"""
services.py
-----------
Berisi seluruh logic perhitungan pajak:

- hitung_pph21_bulanan() : PPh 21 pegawai tetap per bulan (metode setahun-disetahunkan)
- hitung_pph21_tahunan() : PPh 21 setahun (rekap 12 bulan) + status Kurang/Lebih bayar
- hitung_pph_badan()     : PPh Badan (PPh 29) dengan fasilitas Pasal 31E

Semua angka menggunakan Rupiah dan bertipe float (atau int pada output akhir).
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# KONSTANTA
# ---------------------------------------------------------------------------

# PTKP setahun berdasarkan status (Rupiah)
# AUDIT 2026-10-25: TK/1, TK/2, TK/3 sebelumnya hilang dari tabel sehingga
# panggilan API dengan status sah tersebut ditolak 422 ("tidak dikenal"),
# padahal nilainya sama dengan K/0, K/1, K/2 (tanggungan yang sama).
PTKP_TABLE: Dict[str, float] = {
    "TK/0": 54_000_000,
    "TK/1": 58_500_000,
    "TK/2": 63_000_000,
    "TK/3": 67_500_000,
    "K/0": 58_500_000,
    "K/1": 63_000_000,
    "K/2": 67_500_000,
    "K/3": 72_000_000,
}

# Lapisan tarif progresif PPh 21/OP (Pasal 17 UU HPP) -> (batas_atas, tarif)
# batas_atas = None berarti lapisan terakhir (tak terbatas)
LAPISAN_TARIF_PPH21: List[tuple] = [
    (60_000_000, 0.05),      # s.d. 60 juta
    (250_000_000, 0.15),     # > 60 juta s.d. 250 juta
    (500_000_000, 0.25),     # > 250 juta s.d. 500 juta
    (5_000_000_000, 0.30),   # > 500 juta s.d. 5 miliar
    (None, 0.35),            # > 5 miliar
]

# Batas biaya jabatan
BIAYA_JABATAN_PERSEN = 0.05           # 5% dari bruto
BIAYA_JABATAN_MAX_TAHUN = 6_000_000   # maksimal Rp6.000.000 per tahun
BIAYA_JABATAN_MAX_BULAN = 500_000     # maksimal Rp500.000 per bulan

# Tarif PPh Badan
TARIF_PPH_BADAN = 0.22                # 22%
TARIF_PASAL_31E = 0.50 * 0.22         # 50% x 22% = 11%

# Batas bruto untuk fasilitas Pasal 31E
BATAS_31E_BAWAH = 4_800_000_000       # Rp4,8 miliar
BATAS_31E_ATAS = 50_000_000_000       # Rp50 miliar


# ---------------------------------------------------------------------------
# HELPER
# ---------------------------------------------------------------------------

def _round_down_ribuan(nilai: float) -> float:
    """Bulatkan ke bawah ke ribuan terdekat (mis. 1.234.567 -> 1.234.000)."""
    return float(
        Decimal(str(nilai)).quantize(Decimal("1000"), rounding=ROUND_DOWN)
    )


def _round2(nilai: float) -> float:
    """Bulatkan 2 desimal untuk keperluan tampilan."""
    return float(Decimal(str(nilai)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _hitung_pajak_progresif(pkp: float) -> Dict[str, Any]:
    """
    Hitung PPh dengan tarif progresif berlapis.
    Mengembalikan dict: total, detail lapisan yang terpakai.
    """
    sisa = pkp
    batas_bawah = 0.0
    total = 0.0
    detail: List[Dict[str, Any]] = []

    for batas_atas, tarif in LAPISAN_TARIF_PPH21:
        if sisa <= 0:
            break

        if batas_atas is None:
            kena = sisa
        else:
            lebar_lapisan = batas_atas - batas_bawah
            kena = min(sisa, lebar_lapisan)

        pajak = kena * tarif
        total += pajak

        detail.append(
            {
                "lapisan": f"{int(batas_bawah):,} - "
                           + ("tak terbatas" if batas_atas is None else f"{int(batas_atas):,}"),
                "tarif": f"{tarif * 100:.0f}%",
                "dasar_pengenaan": _round2(kena),
                "pajak": _round2(pajak),
            }
        )

        sisa -= kena
        if batas_atas is not None:
            batas_bawah = batas_atas

    return {"total": _round2(total), "detail_lapisan": detail}


def _biaya_jabatan_tahunan(bruto_tahun: float) -> float:
    """Biaya jabatan 5% dari bruto, maksimal Rp6.000.000 per tahun."""
    return _round2(min(bruto_tahun * BIAYA_JABATAN_PERSEN, BIAYA_JABATAN_MAX_TAHUN))


def _get_ptkp(status_ptkp: str) -> float:
    """Ambil nilai PTKP; lempar error jika status tidak dikenal."""
    status = status_ptkp.upper().strip()
    if status not in PTKP_TABLE:
        raise ValueError(
            f"Status PTKP '{status_ptkp}' tidak dikenal. "
            f"Pilihan: {', '.join(PTKP_TABLE.keys())}"
        )
    return float(PTKP_TABLE[status])


# ---------------------------------------------------------------------------
# PPh 21 - BULANAN
# ---------------------------------------------------------------------------

def hitung_pph21_bulanan(
    gaji_pokok: float,
    tunjangan: float,
    bpjs: float,
    jht: float,
    status_ptkp: str,
) -> Dict[str, Any]:
    """
    Hitung PPh 21 pegawai tetap untuk satu bulan.

    Pendekatan:
      1. Bruto bulan = gaji pokok + tunjangan
      2. Biaya jabatan bulan = 5% x bruto, maks Rp500.000/bulan
      3. Neto bulan  = bruto - biaya jabatan - bpjs - jht
      4. Neto disetahunkan = neto bulan x 12
      5. PKP = neto disetahunkan - PTKP  (dibulatkan ke bawah ribuan)
      6. PPh setahun = tarif progresif atas PKP
      7. PPh per bulan = PPh setahun / 12
      8. THP Kas = bruto bulan - bpjs - jht - PPh per bulan.
         Biaya jabatan TIDAK ikut memotong THP: ia hanya pengurang
         pajak (langkah 3, untuk menghitung neto/PKP), bukan potongan
         kas dari gaji yang diterima pegawai.
    """
    status = status_ptkp.upper().strip()
    ptkp = _get_ptkp(status)

    # 1. Bruto bulanan
    bruto_bulan = gaji_pokok + tunjangan

    # 2. Biaya jabatan 5% dari bruto, maks Rp500.000/bulan
    #    (setara maks Rp6.000.000/tahun).
    biaya_jabatan_bulan = min(bruto_bulan * BIAYA_JABATAN_PERSEN, BIAYA_JABATAN_MAX_BULAN)

    # 3. Pengurang & neto bulanan (biaya jabatan WAJIB ikut mengurangi)
    pengurang_bulan = biaya_jabatan_bulan + bpjs + jht
    neto_bulan = bruto_bulan - pengurang_bulan

    # 4. Disetahunkan
    bruto_tahun = bruto_bulan * 12
    neto_tahun = neto_bulan * 12

    # 5. PKP
    pkp_sebelum_pembulatan = neto_tahun - ptkp
    pkp = _round_down_ribuan(max(pkp_sebelum_pembulatan, 0))

    # 6. PPh setahun & per bulan
    hasil_progresif = _hitung_pajak_progresif(pkp)
    pph_setahun = hasil_progresif["total"]
    pph_bulan = _round2(pph_setahun / 12)

    # 7. Take Home Pay KAS: yang benar-benar diterima pegawai =
    #    bruto - BPJS - JHT - PPh. Biaya jabatan sengaja TIDAK dikurangkan
    #    di sini karena ia bukan potongan kas, melainkan hanya pengurang
    #    dalam perhitungan neto/PKP (langkah 3).
    thp = _round2(bruto_bulan - bpjs - jht - pph_bulan)

    return {
        "status_ptkp": status,
        "perhitungan_bulanan": {
            "gaji_pokok": _round2(gaji_pokok),
            "tunjangan": _round2(tunjangan),
            "bruto": _round2(bruto_bulan),
            "biaya_jabatan": _round2(biaya_jabatan_bulan),
            "bpjs": _round2(bpjs),
            "jht": _round2(jht),
            "total_pengurang": _round2(pengurang_bulan),
            "neto": _round2(neto_bulan),
        },
        "perhitungan_setahun": {
            "bruto_setahun": _round2(bruto_tahun),
            "neto_setahun": _round2(neto_tahun),
            "ptkp": ptkp,
            "pkp_sebelum_pembulatan": _round2(pkp_sebelum_pembulatan),
            "pkp": pkp,
            "detail_tarif_progresif": hasil_progresif["detail_lapisan"],
            "pph_setahun": pph_setahun,
        },
        "hasil": {
            "pph21_per_bulan": pph_bulan,
            "take_home_pay": thp,
        },
    }


# ---------------------------------------------------------------------------
# PPh 21 - TAHUNAN (rekap 12 bulan)
# ---------------------------------------------------------------------------

def hitung_pph21_tahunan(
    bulanan: List[Dict[str, Any]],
    status_ptkp: str,
) -> Dict[str, Any]:
    """
    Hitung PPh 21 setahun dari rekap 12 bulan.

    Setiap elemen `bulanan` adalah dict dengan key:
        - gaji_pokok        (float)
        - tunjangan         (float, opsional)
        - thr               (float, opsional)
        - bonus             (float, opsional)
        - bpjs              (float, opsional)
        - jht               (float, opsional)
        - pph_dipotong      (float, opsional) -> PPh yang sudah dipotong bulan tsb

    Return:
        - total bruto, biaya jabatan 5% (max 6jt), PTKP, PKP, PPh terutang setahun
        - total PPh dipotong & status Kurang/Lebih bayar
    """
    status = status_ptkp.upper().strip()
    ptkp = _get_ptkp(status)

    total_gaji = 0.0
    total_tunjangan = 0.0
    total_thr = 0.0
    total_bonus = 0.0
    total_bpjs = 0.0
    total_jht = 0.0
    total_pph_dipotong = 0.0
    rekap: List[Dict[str, Any]] = []

    for i, item in enumerate(bulanan, start=1):
        gaji = float(item.get("gaji_pokok", 0) or 0)
        tunjangan = float(item.get("tunjangan", 0) or 0)
        thr = float(item.get("thr", 0) or 0)
        bonus = float(item.get("bonus", 0) or 0)
        bpjs = float(item.get("bpjs", 0) or 0)
        jht = float(item.get("jht", 0) or 0)
        pph_dipotong = float(item.get("pph_dipotong", 0) or 0)

        bruto_bulan = gaji + tunjangan + thr + bonus

        total_gaji += gaji
        total_tunjangan += tunjangan
        total_thr += thr
        total_bonus += bonus
        total_bpjs += bpjs
        total_jht += jht
        total_pph_dipotong += pph_dipotong

        rekap.append(
            {
                "bulan_ke": i,
                "bruto": _round2(bruto_bulan),
                "pph_dipotong": _round2(pph_dipotong),
            }
        )

    bruto_setahun = total_gaji + total_tunjangan + total_thr + total_bonus
    biaya_jabatan = _biaya_jabatan_tahunan(bruto_setahun)

    # Pengurang pegawai (BPJS + JHT) boleh dikurangkan dari penghasilan bruto
    total_pengurang_pegawai = total_bpjs + total_jht
    neto_setahun = bruto_setahun - biaya_jabatan - total_pengurang_pegawai

    pkp_sebelum = neto_setahun - ptkp
    pkp = _round_down_ribuan(max(pkp_sebelum, 0))

    hasil_progresif = _hitung_pajak_progresif(pkp)
    pph_terutang = hasil_progresif["total"]

    selisih = _round2(pph_terutang - total_pph_dipotong)
    if selisih > 0:
        status_bayar = "KURANG_BAYAR"
        keterangan = "PPh 21 terutang lebih besar dari yang sudah dipotong (kurang bayar)."
    elif selisih < 0:
        status_bayar = "LEBIH_BAYAR"
        keterangan = "PPh 21 yang sudah dipotong lebih besar dari terutang (lebih bayar)."
    else:
        status_bayar = "NIHIL"
        keterangan = "PPh 21 terutang sama dengan yang sudah dipotong (nihil)."

    return {
        "status_ptkp": status,
        "rekap_bulanan": rekap,
        "perhitungan_setahun": {
            "total_gaji_pokok": _round2(total_gaji),
            "total_tunjangan": _round2(total_tunjangan),
            "total_thr": _round2(total_thr),
            "total_bonus": _round2(total_bonus),
            "bruto_setahun": _round2(bruto_setahun),
            "biaya_jabatan": biaya_jabatan,
            "total_bpjs": _round2(total_bpjs),
            "total_jht": _round2(total_jht),
            "neto_setahun": _round2(neto_setahun),
            "ptkp": ptkp,
            "pkp": pkp,
            "detail_tarif_progresif": hasil_progresif["detail_lapisan"],
        },
        "hasil": {
            "pph21_terutang_setahun": pph_terutang,
            "total_pph_dipotong": _round2(total_pph_dipotong),
            "selisih": selisih,
            "status": status_bayar,
            "keterangan": keterangan,
        },
    }


# ---------------------------------------------------------------------------
# PPh BADAN (PPh 29) dengan fasilitas Pasal 31E
# ---------------------------------------------------------------------------

def hitung_pph_badan(
    omzet: float,
    omzet_lain: float,
    biaya: float,
    kredit_pph25: float,
    kredit_pph22_23: float,
) -> Dict[str, Any]:
    """
    Hitung PPh Badan setahun & PPh 29 (kurang bayar).

    Langkah:
      1. Total bruto (peredaran bruto) = omzet + omzet_lain
      2. Laba bersih (PKP)            = total bruto - biaya
      3. Fasilitas Pasal 31E:
         - bruto <= 4,8 M            -> seluruh PKP tarif 11%
         - 4,8 M < bruto <= 50 M     -> PKP dibagi: (4,8M/bruto) tarif 11%, sisanya 22%
         - bruto > 50 M              -> seluruh PKP tarif 22%
      4. PPh 29 = PPh terutang - kredit pajak (PPh 25 + PPh 22/23)
    """
    total_bruto = omzet + omzet_lain
    laba_bersih = total_bruto - biaya
    pkp = max(laba_bersih, 0.0)

    # --- Tentukan fasilitas Pasal 31E ---
    if total_bruto <= 0:
        porsi_fasilitas = 0.0
        pkp_fasilitas = 0.0
        pkp_non_fasilitas = pkp
        pph_fasilitas = 0.0
        pph_non_fasilitas = _round2(pkp_non_fasilitas * TARIF_PPH_BADAN)
        ket_31e = "Tidak ada peredaran bruto; seluruh PKP dikenai tarif 22%."

    elif total_bruto <= BATAS_31E_BAWAH:
        # Seluruh PKP dapat fasilitas (11%)
        porsi_fasilitas = 1.0
        pkp_fasilitas = pkp
        pkp_non_fasilitas = 0.0
        pph_fasilitas = _round2(pkp_fasilitas * TARIF_PASAL_31E)
        pph_non_fasilitas = 0.0
        ket_31e = (
            "Peredaran bruto <= Rp4,8 miliar; seluruh PKP mendapat fasilitas "
            "Pasal 31E (tarif 50% x 22% = 11%)."
        )

    elif total_bruto <= BATAS_31E_ATAS:
        # Pembagian PKP: sebagian dapat fasilitas, sebagian tidak
        porsi_fasilitas = BATAS_31E_BAWAH / total_bruto
        pkp_fasilitas = pkp * porsi_fasilitas
        pkp_non_fasilitas = pkp - pkp_fasilitas
        pph_fasilitas = _round2(pkp_fasilitas * TARIF_PASAL_31E)
        pph_non_fasilitas = _round2(pkp_non_fasilitas * TARIF_PPH_BADAN)
        ket_31e = (
            "Peredaran bruto > Rp4,8 miliar s.d. Rp50 miliar; PKP dibagi sesuai "
            "fasilitas Pasal 31E (sebagian 11%, sebagian 22%)."
        )

    else:
        # Di atas 50 M -> tidak dapat fasilitas
        porsi_fasilitas = 0.0
        pkp_fasilitas = 0.0
        pkp_non_fasilitas = pkp
        pph_fasilitas = 0.0
        pph_non_fasilitas = _round2(pkp_non_fasilitas * TARIF_PPH_BADAN)
        ket_31e = (
            "Peredaran bruto > Rp50 miliar; tidak mendapat fasilitas Pasal 31E, "
            "seluruh PKP dikenai tarif 22%."
        )

    pph_terutang = _round2(pph_fasilitas + pph_non_fasilitas)

    # --- Kredit pajak & PPh 29 ---
    total_kredit = kredit_pph25 + kredit_pph22_23
    pph_29 = _round2(pph_terutang - total_kredit)

    if pph_29 > 0:
        status = "KURANG_BAYAR"
        keterangan = "PPh 29 kurang bayar, wajib disetor sebelum SPT Tahunan dilaporkan."
    elif pph_29 < 0:
        status = "LEBIH_BAYAR"
        keterangan = "Kredit pajak melebihi PPh terutang (lebih bayar), dapat direstitusi/dikompensasi."
    else:
        status = "NIHIL"
        keterangan = "PPh terutang sama dengan total kredit pajak (nihil)."

    return {
        "perhitungan_laba": {
            "omzet": _round2(omzet),
            "omzet_lain": _round2(omzet_lain),
            "total_peredaran_bruto": _round2(total_bruto),
            "biaya": _round2(biaya),
            "laba_bersih": _round2(laba_bersih),
            "pkp": _round2(pkp),
        },
        "fasilitas_pasal_31e": {
            "keterangan": ket_31e,
            "porsi_fasilitas": _round2(porsi_fasilitas) if porsi_fasilitas else 0.0,
            "pkp_dapat_fasilitas": _round2(pkp_fasilitas),
            "pkp_tidak_dapat_fasilitas": _round2(pkp_non_fasilitas),
            "tarif_fasilitas": "11%",
            "tarif_non_fasilitas": "22%",
            "pph_dari_fasilitas": pph_fasilitas,
            "pph_dari_non_fasilitas": pph_non_fasilitas,
        },
        "hasil": {
            "pph_terutang_setahun": pph_terutang,
            "kredit_pph25": _round2(kredit_pph25),
            "kredit_pph22_23": _round2(kredit_pph22_23),
            "total_kredit_pajak": _round2(total_kredit),
            "pph_29_kurang_bayar": pph_29,
            "status": status,
            "keterangan": keterangan,
        },
    }