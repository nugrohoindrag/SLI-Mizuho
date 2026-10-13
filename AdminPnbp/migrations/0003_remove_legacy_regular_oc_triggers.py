from django.db import migrations


DROP_LEGACY_PNBP = """
DO $$
BEGIN
    IF to_regclass('public.consumers') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_update_pnbp_fee ON consumers;
    END IF;
    IF to_regclass('public.consumers_oc') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_update_pnbp_fee_oc ON consumers_oc;
    END IF;
END
$$;
DROP FUNCTION IF EXISTS public.trg_update_pnbp_fee();
DROP FUNCTION IF EXISTS public.trg_update_pnbp_fee_oc();
"""


RESTORE_LEGACY_PNBP = """
CREATE OR REPLACE FUNCTION public.trg_update_pnbp_fee()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.is_st_used = 1 THEN
        IF NEW.guarantee_value <= 50000000 THEN
            NEW.pnbp_fee := 50000;
        ELSIF NEW.guarantee_value > 50000000 AND NEW.guarantee_value <= 100000000 THEN
            NEW.pnbp_fee := 100000;
        ELSIF NEW.guarantee_value > 100000000 AND NEW.guarantee_value <= 250000000 THEN
            NEW.pnbp_fee := 200000;
        ELSIF NEW.guarantee_value > 250000000 AND NEW.guarantee_value <= 500000000 THEN
            NEW.pnbp_fee := 450000;
        ELSIF NEW.guarantee_value > 500000000 AND NEW.guarantee_value <= 1000000000 THEN
            NEW.pnbp_fee := 850000;
        ELSIF NEW.guarantee_value > 1000000000 AND NEW.guarantee_value <= 100000000000 THEN
            NEW.pnbp_fee := 1800000;
        ELSE
            NEW.pnbp_fee := NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

DO $$
BEGIN
    IF to_regclass('public.consumers') IS NOT NULL THEN
        CREATE TRIGGER trg_update_pnbp_fee
        BEFORE INSERT OR UPDATE ON consumers
        FOR EACH ROW EXECUTE FUNCTION public.trg_update_pnbp_fee();
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION public.trg_update_pnbp_fee_oc()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.is_st_used = 1 THEN
        IF NEW.guarantee_value <= 50000000 THEN
            NEW.pnbp_fee := 50000;
        ELSIF NEW.guarantee_value > 50000000 AND NEW.guarantee_value <= 100000000 THEN
            NEW.pnbp_fee := 100000;
        ELSIF NEW.guarantee_value > 100000000 AND NEW.guarantee_value <= 250000000 THEN
            NEW.pnbp_fee := 200000;
        ELSIF NEW.guarantee_value > 250000000 AND NEW.guarantee_value <= 500000000 THEN
            NEW.pnbp_fee := 450000;
        ELSIF NEW.guarantee_value > 500000000 AND NEW.guarantee_value <= 1000000000 THEN
            NEW.pnbp_fee := 850000;
        ELSIF NEW.guarantee_value > 1000000000 AND NEW.guarantee_value <= 100000000000 THEN
            NEW.pnbp_fee := 1800000;
        ELSE
            NEW.pnbp_fee := NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

DO $$
BEGIN
    IF to_regclass('public.consumers_oc') IS NOT NULL THEN
        CREATE TRIGGER trg_update_pnbp_fee_oc
        BEFORE INSERT OR UPDATE ON consumers_oc
        FOR EACH ROW EXECUTE FUNCTION public.trg_update_pnbp_fee_oc();
    END IF;
END
$$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("AdminPnbp", "0002_register_admin_menu"),
    ]

    operations = [
        migrations.RunSQL(
            sql=DROP_LEGACY_PNBP,
            reverse_sql=RESTORE_LEGACY_PNBP,
        ),
    ]
