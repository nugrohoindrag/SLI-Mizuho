-- Konfigurasi instance Mizuho pada tabel common_codes.
-- Jalankan sekali di database Mizuho saat deploy. Aman diulang.

-- Assign Akta: 5 nomor akta per menit jam akta (TAF: 10).
-- Dibaca assignakta.views._akta_per_minute(); fallback 5 bila kosong/tidak valid.
UPDATE common_codes
   SET code_value = '5',
       code_name  = 'Jumlah Akta per Menit',
       updated_at = now()
 WHERE code_key = 'interval_minute';
