from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.timezone import now
from django.forms.models import model_to_dict
from change_fiduciary.models import change_of_fiduciary, ActivityLogPf
from import_document_pf.models import ConsumerDocumentPF
from decimal import Decimal
from django.db.models.fields.files import FieldFile
import datetime
from UpdateDataPF.models import TemporaryUpdatePF
#from .models import LegalDocumentPF
from import_cabang.models import Consumer
from django.db import transaction

def serialize_dates(data):
    if isinstance(data, dict):
        return {k: serialize_dates(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_dates(item) for item in data]
    elif isinstance(data, (datetime.date, datetime.datetime)):
        return data.isoformat()
    elif isinstance(data, Decimal):
        return float(data)
    elif isinstance(data, FieldFile):
        return data.url if data else ''
    else:
        return data

@receiver(post_save, sender=change_of_fiduciary)
def log_consumer_create_update(sender, instance, created, **kwargs):
    print(f"⚡ SIGNAL CALLED for: {instance.agreement_number} | created: {created}")

    # 🔁 Ambil data lama untuk log jika update
    old_data = {}
    if not created:
        try:
            old_instance = change_of_fiduciary.objects.get(pk=instance.pk)
            old_data = model_to_dict(old_instance)
        except change_of_fiduciary.DoesNotExist:
            pass

    new_data = model_to_dict(instance)

    properties = {
        "old": serialize_dates(old_data) if not created else {},
        "attributes": serialize_dates(new_data),
        "agreement_number": instance.agreement_number
    }

    try:
        ActivityLogPf.objects.create(
            log_name='change_of_fiduciary',
            description='created' if created else 'updated',
            causer=instance.uploader if instance.uploader else None,
            causer_type='AdminUser.User',
            subject_id=instance.id,
            subject_type='change_of_fiduciary',
            properties=properties
        )
        print("✅ ActivityLog created successfully.")
    except Exception as e:
        print("❌ ERROR creating ActivityLog from signal:", e)


    if instance.agreement_number:
        transaction.on_commit(lambda: _create_consumer_document(instance))
        #transaction.on_commit(lambda: _create_or_update_legal_document(instance))

@receiver(post_delete, sender=change_of_fiduciary)
def log_consumer_delete(sender, instance, **kwargs):
    print(f"⚡ SIGNAL DELETE CALLED for: {instance.agreement_number}")

    raw_old_data = model_to_dict(instance)
    serialized_data = serialize_dates(raw_old_data)

    properties = {
        "old": serialized_data,
        "attributes": {},
        "agreement_number": instance.agreement_number
    }

    try:
        ActivityLogPf.objects.create(
            log_name='change_of_fiduciary',
            description='deleted',
            causer=instance.uploader if instance.uploader else None,
            causer_type='AdminUser.User',
            subject_id=instance.id,
            subject_type='Consumer',
            properties=properties
        )
        print("✅ ActivityLog for delete created successfully.")
    except Exception as e:
        print("❌ ERROR creating ActivityLog for delete from signal:", e)

# ==============================
# ✅ HELPER: CEK & BUAT DOKUMEN
# ==============================

def _create_consumer_document(instance):
    if not instance.agreement_number:
        print("⚠️ Skipping ConsumerDocument creation: agreement_number is empty")
        return

    if ConsumerDocumentPF.objects.filter(agreement_number=instance.agreement_number).exists():
        print(f"🚫 ConsumerDocument already exists for agreement_number: {instance.agreement_number}")
        return

    try:
        doc = ConsumerDocumentPF.objects.create(
            agreement_number=instance.agreement_number,
            consumer_name=instance.debtor_name or 'Unknown',
            agreement_date=instance.agreement_date or now().date(),
            document_type='Default Type',
            description='Auto-generated from signal',
            notes='',
            file_url='',
            modified_by=instance.uploader if instance.uploader else None,
            #created_at=now(),
            #updated_at=now(),
        )
        print(f"✅ ConsumerDocument created successfully with ID: {doc.id}")
    except Exception as e:
        print("❌ ERROR creating ConsumerDocument from signal:", e)

# def _create_legal_document(instance):
#     clean_number = str(instance.agreement_number or "").strip().upper()
#     if not clean_number:
#         print("⚠️ Skipping LegalDocument creation: agreement_number is empty")
#         return

#     try:

#         # Cegah duplikat berdasarkan FK change_of_fiduciary
#         if LegalDocumentPF.objects.filter(
#             change_of_fiduciary_id=instance.id
#         ).exists():
#             print(f"🚫 LegalDocument already exists for change_of_fiduciary_id={instance.id}")
#             return

#         legal_doc = LegalDocumentPF.objects.create(
#             change_of_fiduciary=instance,   # ⬅️ sesuaikan dengan model
#             document_number=clean_number,   # atau None jika ingin isi belakangan
#             document_date=None,
#             # created_at/updated_at dibiarkan, auto_now_add/auto_now yang set
#         )
#         print(f"✅ LegalDocument created with ID: {legal_doc.id}")
#     except Exception as e:
#         print("❌ ERROR creating LegalDocument:", e)

# def _create_or_update_legal_document(instance):
#     clean_number = str(instance.agreement_number or "").strip().upper()
#     if not clean_number:
#         print("⚠️ Skipping LegalDocument creation/update: agreement_number is empty")
#         return

#     try:

#         # Notaris (opsional)
#         notary_instance = None
#         if getattr(instance, "director_notary_name", None):
#             notary_instance = Notary.objects.filter(
#                 firm_name__iexact=instance.director_notary_name.strip()
#             ).first()
#             if notary_instance:
#                 print(f"✅ Found Notary: {notary_instance.firm_name}")
#             else:
#                 print(f"⚠️ Notary not found for name: {instance.director_notary_name}")

#         # Cari dokumen berdasarkan FK change_of_fiduciary
#         legal_doc = LegalDocumentPF.objects.filter(
#             change_of_fiduciary_id=instance.id
#         ).first()

#         document_date = None  # biarkan kosong

#         if legal_doc:
#             print(f"✏️ Updating LegalDocument for change_of_fiduciary ID {instance.id}")
#             legal_doc.notary = notary_instance
#             legal_doc.document_date = document_date
#             legal_doc.save(update_fields=["notary", "document_date"])
#             print(f"✅ LegalDocument updated with ID: {legal_doc.id}")
#         else:
#             new_doc = LegalDocumentPF.objects.create(
#                 change_of_fiduciary=instance,   # ⬅️ sesuai model
#                 document_number=None,           # atau clean_number jika ingin isi sekarang
#                 document_date=document_date,
#                 notary=notary_instance,
#             )
#             print(f"✅ LegalDocument created with ID: {new_doc.id}")
#     except Exception as e:
#         print("❌ ERROR creating/updating LegalDocument:", e)
#         import traceback
#         traceback.print_exc()

from UpdateDataPF.views import TemporaryUpdateBatchPatchAPIViewPF
@receiver(post_save, sender=TemporaryUpdatePF)
def apply_temporary_update_to_consumer(sender, instance, created, **kwargs):
    if instance.is_approved and instance.agreement_number:
        try:
            consumer = change_of_fiduciary.objects.get(agreement_number=instance.agreement_number)

            # gunakan mapping
            api_view = TemporaryUpdateBatchPatchAPIViewPF()
            result = api_view.find_category_info(instance.category)

            if not result:
                print(f"❌ Kategori {instance.category} tidak valid.")
                return

            parent, key, field = result
            new_value = instance.replacement_value

            if hasattr(consumer, field):
                old_value = getattr(consumer, field)
                setattr(consumer, field, new_value)
                consumer.save(update_fields=[field])

                print(f"✅ Consumer updated: field '{field}' set to '{new_value}' for agreement {instance.agreement_number}")

                causer = getattr(instance, 'approved_by', None) or getattr(instance, 'updated_by', None)
                if causer:
                    ActivityLogPf.objects.create(
                        log_name='TEMPORARY_UPDATE',
                        description=f"approved and applied update to field '{field}'",
                        causer=causer,
                        causer_type='AdminUser.User',
                        subject_id=consumer.id,
                        subject_type='Consumer',
                        properties={
                            "agreement_number": instance.agreement_number,
                            "field": field,
                            "old_value": serialize_dates(old_value),
                            "new_value": serialize_dates(new_value),
                            "temporary_update_id": instance.id
                        }
                    )
                    print("📝 ActivityLog created for TemporaryUpdate apply.")
                else:
                    print(f"⚠️ Skipped ActivityLog for TemporaryUpdate: causer not found (approved_by/updated_by is None)")
            else:
                print(f"❌ Field '{field}' tidak ditemukan di Consumer model.")
        except change_of_fiduciary.DoesNotExist:
            print(f"❌ Consumer not found with agreement_number: {instance.agreement_number}")
        except Exception as e:
            print(f"❌ Error applying temporary update: {e}")

# @receiver(post_save, sender=LegalDocumentPF)
# def update_consumer_from_legal_document(sender, instance, **kwargs):
#     try:
#         consumer = getattr(instance, 'change_of_fiduciary', None)  # ⬅️ ganti dari instance.consumer
#         if not consumer:
#             print(f"⚠️ LegalDocument {instance.id} has no change_of_fiduciary linked.")
#             return

#         updated_consumer = False
#         updated_document = False
#         today = now().date()

#         # Jika document_number terisi dan document_date kosong → set today
#         if instance.document_number and not instance.document_date:
#             instance.document_date = today
#             instance.save(update_fields=['document_date'])
#             updated_document = True
#             print(f"📄 Updated document_date for LegalDocument {instance.id} to {today}")

#         # Sinkron ke change_of_fiduciary (alias 'consumer' lama)
#         if hasattr(consumer, 'deed_number') and instance.document_number and consumer.deed_number != instance.document_number:
#             consumer.deed_number = instance.document_number
#             updated_consumer = True

#         if hasattr(consumer, 'deed_date') and instance.document_date and not consumer.deed_date:
#             consumer.deed_date = instance.document_date
#             updated_consumer = True

#         if updated_consumer:
#             consumer.save()
#             print(f"✅ change_of_fiduciary {consumer.agreement_number} updated from LegalDocument {instance.id}")
#         else:
#             print(f"ℹ️ No changes needed for change_of_fiduciary {getattr(consumer, 'agreement_number', '-')}")
#     except Exception as e:
#         print(f"❌ Error updating change_of_fiduciary from LegalDocument: {e}")
#         import traceback
#         traceback.print_exc()