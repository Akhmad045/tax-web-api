# Tax Web API

Backend FastAPI untuk kalkulator pajak (PPh 21 bulanan/tahunan, PPh Badan) dengan
autentikasi JWT, verifikasi email OTP (Brevo), dan penyimpanan riwayat SQLite.

## Menjalankan lokal

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

uvicorn main:app --reload
```

Buka http://127.0.0.1:8000/docs untuk dokumentasi interaktif.

## Environment yang wajib diset

| Variabel | Wajib | Keterangan |
|---|---|---|
| `JWT_SECRET_KEY` | Ya (produksi) | Kunci penanda tangan token. Tanpa ini dipakai kunci pengembangan yang TIDAK aman. |
| `ADMIN_PASSWORD` | Ya (saat pertama deploy) | Dipakai HANYA bila tabel `users` masih kosong. Tidak ada password default di dalam kode — tanpa env ini akun admin tidak dibuat. |
| `ADMIN_USERNAME` | Tidak | Default `akhmad23`. |
| `BREVO_API_KEY` | Ya (fitur OTP) | API key Brevo, diawali `xkeysib-` (bukan kunci SMTP). |
| `BREVO_SENDER` | Ya (fitur OTP) | Alamat pengirim yang sudah diverifikasi di Brevo. |
| `CORS_ORIGINS` | Ya (produksi) | Origin frontend, dipisah koma. |
| `TAX_DB_PATH` | Tidak | Lokasi file SQLite (lihat catatan disk di `render.yaml`). |
| `OTP_TTL_MINUTES` | Tidak | Masa berlaku kode OTP, default `5`. |
| `OTP_MAKS_PERCOBAAN` | Tidak | Batas tebakan salah sebelum kode dihapus, default `5`. |
| `JWT_EXPIRE_MINUTES` | Tidak | Default `480` (8 jam). |

Variabel `MAIL_*` dari versi SMTP lama sudah tidak dipakai dan boleh dihapus.

## Menjalankan tes

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

`test_regresi.py` mengunci perbaikan hasil audit: otorisasi riwayat per pemilik,
batas percobaan OTP, kebijakan password, penolakan nilai `NaN`/`Infinity`, dan
perhitungan pajak (PTKP, biaya jabatan, Pasal 31E). Tes memakai database
sementara di folder temp, jadi `tax.db` milik Anda tidak tersentuh.

## Catatan perilaku

- `GET/DELETE /api/riwayat*` hanya untuk **admin**; user biasa mendapat 403.
- `POST /api/riwayat` wajib login. Pemilik dicatat dari token, bukan dari body.
- Password minimal 8 karakter dan tidak boleh hanya angka, variasi karakter
  terlalu sedikit, atau termasuk password umum.
