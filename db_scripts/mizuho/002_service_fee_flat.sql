-- Biaya jasa Mizuho: flat per akta (requirement §8), tanpa tier 60.000 setelah 2.000 transaksi/bulan.
-- Dipakai trigger trg_set_service_fee_consumers (consumers) & trg_set_service_fee_consumers_oc (consumers_oc).
-- Nilai dibaca dari common_codes.BIAYA_JASA (fallback 70000). Counter bulanan tetap dinaikkan
-- agar data monthly_service_counters tetap konsisten. Aman diulang.

CREATE OR REPLACE FUNCTION public.trg_set_service_fee_monthly_old()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_month date := date_trunc('month', now())::date;
    v_fee numeric;
    v_new_is1 boolean := (COALESCE(NEW.invoice_st::text, '') IN ('1','true','t','yes','y'));
    v_old_is1 boolean := (COALESCE(OLD.invoice_st::text, '') IN ('1','true','t','yes','y'));
BEGIN
    -- Hanya saat invoice_st menjadi 1 (insert baru dengan 1, atau update dari bukan-1 ke 1)
    IF v_new_is1 AND (TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NOT v_old_is1)) THEN
        INSERT INTO monthly_service_counters (month_start)
        VALUES (v_month)
        ON CONFLICT (month_start) DO NOTHING;

        UPDATE monthly_service_counters
           SET count_total = count_total + 1,
               updated_at  = now()
         WHERE month_start = v_month;

        SELECT CASE WHEN btrim(code_value) ~ '^[0-9]+(\.[0-9]+)?$' THEN btrim(code_value)::numeric END
          INTO v_fee
          FROM common_codes
         WHERE code_key = 'BIAYA_JASA';

        NEW.service_fee := COALESCE(v_fee, 70000);
    END IF;

    RETURN NEW;
END;
$function$;

-- Samakan Biaya Jasa2 (dipakai invoice OC/MU yang masih punya logika tier) agar efektif flat.
UPDATE common_codes
   SET code_value = '70000',
       updated_at = now()
 WHERE code_key = 'BIAYA_JASA_2';
