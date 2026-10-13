from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.timezone import now
from django.forms.models import model_to_dict
from .models import Consumer
from import_document.models import ConsumerDocument
from accesslog.models import ActivityLog
from decimal import Decimal
from django.db.models.fields.files import FieldFile
import datetime
from UpdateData.models import TemporaryUpdate
from warkahDokument.models import LegalDocument
import os
import traceback

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

@receiver(post_save, sender=Consumer)
def log_consumer_create_update(sender, instance, created, **kwargs):
    if getattr(instance, "_skip_signal", False):
        return
    
    print(f"⚡ SIGNAL CALLED for: {instance.agreement_number} | created: {created}")

    # 🔁 Ambil data lama untuk log jika update
    old_data = {}
    if not created:
        try:
            old_instance = Consumer.objects.get(pk=instance.pk)
            old_data = model_to_dict(old_instance)
        except Consumer.DoesNotExist:
            pass

    new_data = model_to_dict(instance)

    properties = {
        "old": serialize_dates(old_data) if not created else {},
        "attributes": serialize_dates(new_data),
        "agreement_number": instance.agreement_number,
        "branch_name": instance.branch_name if hasattr(instance, 'branch_name') else '-'
    }

    try:
        ActivityLog.objects.create(
            log_name='CONSUMER',
            description='created' if created else 'updated',
            causer=instance.uploader if instance.uploader else None,
            causer_type='AdminUser.User',
            subject_id=instance.id,
            subject_type='Consumer',
            properties=properties
        )
        print("✅ ActivityLog created successfully.")
    except Exception as e:
        print("❌ ERROR creating ActivityLog from signal:", e)


    #if instance.agreement_number:
        #_create_consumer_document(instance)
        #_create_or_update_legal_document(instance)

@receiver(post_delete, sender=Consumer)
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
        ActivityLog.objects.create(
            log_name='CONSUMER',
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

# def _create_consumer_document(instance):
#     if not instance.agreement_number:
#         print("⚠️ Skipping ConsumerDocument creation: agreement_number is empty")
#         return

#     if ConsumerDocument.objects.filter(agreement_number=instance.agreement_number).exists():
#         print(f"🚫 ConsumerDocument already exists for agreement_number: {instance.agreement_number}")
#         return

#     try:
#         # Pastikan agreement_date valid
#         agreement_date = instance.agreement_date
#         if isinstance(agreement_date, str):
#             try:
#                 # Asumsikan format DD/MM/YYYY jika string
#                 agreement_date = datetime.strptime(agreement_date.strip(), "%d/%m/%Y").date()
#             except ValueError:
#                 print(f"⚠️ Format tanggal tidak valid: {agreement_date}, menggunakan tanggal hari ini")
#                 agreement_date = now().date()
#         elif agreement_date is None:
#             agreement_date = now().date()

#         doc = ConsumerDocument.objects.create(
#             agreement_number=instance.agreement_number,
#             consumer_name=instance.debtor_name or 'Unknown',
#             agreement_date=agreement_date,
#             document_type='Default Type',
#             description='Auto-generated from signal',
#             notes='',
#             file_url='',  # masih default kosong
#             modified_by=instance.uploader if instance.uploader else None,
#             created_at=now(),
#             updated_at=now(),
#         )
#         print(f"✅ ConsumerDocument created successfully with ID: {doc.id}")

#         # Extract file name and path (if someday populated)
#         file_url = str(doc.file_url or '').strip()
#         filename = os.path.basename(file_url) if file_url else ''
#         file_path = file_url

#         ActivityLog.objects.create(
#             log_name='UPLOAD_FILE',
#             description='Dokumen konsumen berhasil dibuat',
#             causer=instance.uploader if instance.uploader else None,
#             causer_type='AdminUser.User',
#             subject_id=doc.id,
#             subject_type='ConsumerDocument',
#             properties={
#                 "agreement_number": instance.agreement_number,
#                 "filename": filename,
#                 "file_path": file_path
#             }
#         )
#         print(f"📥 ActivityLog disimpan dengan file: {filename if filename else '[EMPTY]'}")

#     except Exception as e:
#         print("❌ ERROR creating ConsumerDocument from signal:", str(e))
#         print(traceback.format_exc())

def _create_legal_document(instance):
    clean_number = str(instance.agreement_number or "").strip().upper()
    if not clean_number:
        print("⚠️ Skipping LegalDocument creation: agreement_number is empty")
        return

    try:
        if LegalDocument.objects.filter(document_number__iexact=clean_number).exists():
            print(f"🚫 LegalDocument already exists for document_number: {clean_number}")
            return

        legal_doc = LegalDocument.objects.create(
            consumer=instance,
            document_date=None,
            document_number=clean_number,
            created_at=now(),
            updated_at=now(),
        )
        print(f"✅ LegalDocument created with ID: {legal_doc.id}")
    except Exception as e:
        print("❌ ERROR creating LegalDocument:", e)


def _create_or_update_legal_document(instance):
    clean_number = str(instance.agreement_number).strip().upper() if instance.agreement_number is not None else ""
    if not clean_number:
        print("⚠️ Skipping LegalDocument creation/update: agreement_number is empty")
        return

    try:
        from AdminNotaries.models import Notary
        notary_instance = None
        if instance.director_notary_name:
            notary_instance = Notary.objects.filter(firm_name__iexact=instance.director_notary_name.strip()).first()
            if notary_instance:
                print(f"✅ Found Notary: {notary_instance.firm_name}")
            else:
                print(f"⚠️ Notary not found for name: {instance.director_notary_name}")

        legal_doc = LegalDocument.objects.filter(consumer_id=instance.id).first()

        # ⛔️ document_date diset ke None agar kosong
        document_date = None

        if legal_doc:
            print(f"✏️ Updating LegalDocument for consumer ID {instance.id}")
            # ⚠️ Jangan update document_number di sini!
            legal_doc.document_date = document_date
            legal_doc.notary = notary_instance
            legal_doc.updated_at = now()
            legal_doc.save()
            print(f"✅ LegalDocument updated with ID: {legal_doc.id}")
        else:
            new_doc = LegalDocument.objects.create(
                consumer=instance,
                document_number=None,  # default awal kosong
                document_date=document_date,  # ⛔️ kosong/null
                notary=notary_instance,
                created_at=now(),
                updated_at=now(),
            )
            print(f"✅ LegalDocument created with ID: {new_doc.id}")
    except Exception as e:
        print("❌ ERROR creating/updating LegalDocument:", e)
        import traceback
        traceback.print_exc()

from UpdateData.views import TemporaryUpdateBatchPatchAPIView

@receiver(post_save, sender=TemporaryUpdate)
def apply_temporary_update_to_consumer(sender, instance, created, **kwargs):
    if instance.is_approved and instance.agreement_number:
        try:
            consumer = Consumer.objects.get(agreement_number=instance.agreement_number)

            # gunakan mapping
            api_view = TemporaryUpdateBatchPatchAPIView()
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
                    ActivityLog.objects.create(
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
        except Consumer.DoesNotExist:
            print(f"❌ Consumer not found with agreement_number: {instance.agreement_number}")
        except Exception as e:
            print(f"❌ Error applying temporary update: {e}")

@receiver(post_save, sender=LegalDocument)
def update_consumer_from_legal_document(sender, instance, **kwargs):
    try:
        consumer = instance.consumer
        if not consumer:
            print(f"⚠️ LegalDocument {instance.id} has no consumer linked.")
            return

        updated_document = False
        today = now().date()

        # Tetap pastikan document_date terisi di LegalDocument
        if instance.document_number and not instance.document_date:
            instance.document_date = today
            instance.save(update_fields=['document_date'])
            updated_document = True
            print(f"📄 Updated document_date for LegalDocument {instance.id} to {today}")

        # ❌ Jangan update consumer.deed_number / consumer.deed_date lagi
        # biarkan kosong atau legacy saja
        print(f"ℹ️ Consumer {consumer.agreement_number} not modified from LegalDocument {instance.id}")

    except Exception as e:
        print(f"❌ Error updating consumer from LegalDocument: {e}")
        import traceback
        traceback.print_exc()
        
# @receiver(post_save, sender=ConsumerDocument)
# def update_activity_log_file_info(sender, instance, **kwargs):
#     if not instance.file_url or not str(instance.file_url).strip():
#         print(f"⚠️ File URL kosong atau tidak valid untuk ConsumerDocument ID {instance.id}")
#         return

#     try:
#         file_path = str(instance.file_url).strip()
#         filename = os.path.basename(file_path)

#         log = ActivityLog.objects.filter(
#             subject_id=instance.id,
#             subject_type='ConsumerDocument',
#             log_name='UPLOAD_FILE'
#         ).first()

#         if log:
#             # Pastikan log.properties adalah dict
#             if not isinstance(log.properties, dict):
#                 log.properties = {}

#             log.properties['filename'] = filename
#             log.properties['file_path'] = file_path
#             log.save(update_fields=["properties"])

#             print(f"✅ ActivityLog updated with file info: {filename}")
#         else:
#             print(f"⚠️ ActivityLog not found for ConsumerDocument ID {instance.id}")

#     except Exception as e:
#         print(f"❌ ERROR updating ActivityLog with file info: {e}")
#         import traceback
#         traceback.print_exc()