from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.timezone import now
from django.forms.models import model_to_dict
from .models import ConsumerMU
from import_document_mu.models import ConsumerDocumentMU
from accesslogmu.models import ActivityLogMU
from decimal import Decimal
from django.db.models.fields.files import FieldFile
import datetime
from UpdateDataMU.models import TemporaryUpdateMU
from AdminNotaries.models import Notary
from warkahDokumentMU.models import LegalDocumentMU
from django.db.models import Q
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

@receiver(post_save, sender=ConsumerMU)
def log_consumer_create_update(sender, instance, created, **kwargs):
    if getattr(instance, "_skip_signal", False):
        return

    # ✅ Validasi nomor mesin unik (selain dirinya sendiri)
    if instance.engine_number:
        if ConsumerMU.objects.filter(engine_number=instance.engine_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor mesin: {instance.engine_number}")
            return

    # ✅ Validasi nomor rangka unik (selain dirinya sendiri)
    if instance.chassis_number:
        if ConsumerMU.objects.filter(chassis_number=instance.chassis_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor rangka: {instance.chassis_number}")
            return

    print(f"⚡ SIGNAL CALLED for: {instance.agreement_number} | created: {created}")

    # 🔁 Ambil data lama untuk log jika update
    old_data = {}
    if not created:
        try:
            old_instance = ConsumerMU.objects.get(pk=instance.pk)
            old_data = model_to_dict(old_instance)
        except ConsumerMU.DoesNotExist:
            pass

    new_data = model_to_dict(instance)

    properties = {
        "old": serialize_dates(old_data) if not created else {},
        "attributes": serialize_dates(new_data),
        "agreement_number": instance.agreement_number
    }

    try:
        ActivityLogMU.objects.create(
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

    # 🔁 Tetap lanjut meskipun agreement_number boleh sama
    _create_consumer_document(instance)
    #_create_or_update_legal_document(instance)

@receiver(post_delete, sender=ConsumerMU)
def log_consumer_delete(sender, instance, **kwargs):
    print(f"⚡ SIGNAL DELETE CALLED for: {instance.agreement_number}")

    try:
        raw_old_data = model_to_dict(instance)
    except Exception as e:
        print(f"❌ ERROR serializing deleted instance: {e}")
        raw_old_data = {}

    serialized_data = serialize_dates(raw_old_data)

    properties = {
        "old": serialized_data,
        "attributes": {},
        "agreement_number": instance.agreement_number
    }

    try:
        causer = getattr(instance, 'uploader', None)
        ActivityLogMU.objects.create(
            log_name='CONSUMER',
            description='deleted',
            causer=causer,
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

def _create_consumer_document(instance, file_url=''):
    # ✅ Skip jika agreement_number kosong
    if not instance.agreement_number:
        print("⚠️ Skipping ConsumerDocument creation: agreement_number is empty")
        return

    # ✅ Validasi nomor mesin duplikat
    if instance.engine_number:
        if ConsumerMU.objects.filter(engine_number=instance.engine_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor mesin: {instance.engine_number} — batal buat ConsumerDocument")
            return

    # ✅ Validasi nomor rangka duplikat
    if instance.chassis_number:
        if ConsumerMU.objects.filter(chassis_number=instance.chassis_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor rangka: {instance.chassis_number} — batal buat ConsumerDocument")
            return

    # ✅ Cek jika sudah ada ConsumerDocument untuk agreement_number → skip
    if ConsumerDocumentMU.objects.filter(agreement_number=instance.agreement_number).exists():
        print(f"🚫 ConsumerDocument already exists for agreement_number: {instance.agreement_number}")
        return

    try:
        # Ambil nama file
        file_url = str(file_url).strip()
        filename = os.path.basename(file_url) if file_url else ''
        file_path = file_url

        # Format tanggal
        agreement_date = getattr(instance, 'agreement_date', None)
        if isinstance(agreement_date, str):
            try:
                agreement_date = datetime.strptime(agreement_date.strip(), "%d/%m/%Y").date()
            except ValueError:
                print(f"⚠️ Format tanggal tidak valid: {agreement_date}, menggunakan hari ini")
                agreement_date = now().date()
        elif not agreement_date:
            agreement_date = now().date()

        # Simpan dokumen
        doc = ConsumerDocumentMU.objects.create(
            agreement_number=instance.agreement_number,
            consumer_name=getattr(instance, 'debtor_name', 'Unknown') or 'Unknown',
            agreement_date=agreement_date,
            document_type='EXCEL_UPLOAD',
            description='Auto-generated from signal after Excel import',
            notes='',
            file_url=file_url,
            modified_by=getattr(instance, 'uploader', None),
            #created_at=now(),
            #updated_at=now(),
        )
        print(f"✅ ConsumerDocument saved successfully with ID: {doc.id}")

        # Simpan Activity Log
        ActivityLogMU.objects.create(
            log_name='UPLOAD_FILE',
            description='Dokumen konsumen berhasil dibuat',
            causer=getattr(instance, 'uploader', None),
            causer_type='AdminUser.User',
            subject_id=doc.id,
            subject_type='ConsumerDocumentMU',
            properties={
                "agreement_number": instance.agreement_number,
                "filename": filename,
                "file_path": file_path
            }
        )
        print(f"📝 ActivityLog saved with file: {filename if filename else '[EMPTY]'}")

    except Exception as e:
        print("❌ ERROR creating ConsumerDocument:", str(e))
        print(traceback.format_exc())

def _create_legal_document(instance):
    clean_number = str(instance.agreement_number or "").strip().upper()
    if not clean_number:
        print("⚠️ Skipping LegalDocument creation: agreement_number is empty")
        return

    # ✅ Validasi nomor mesin tidak duplikat di ConsumerMU
    if instance.engine_number:
        if ConsumerMU.objects.filter(engine_number=instance.engine_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor mesin: {instance.engine_number} — batal buat LegalDocument")
            return

    # ✅ Validasi nomor rangka tidak duplikat di ConsumerMU
    if instance.chassis_number:
        if ConsumerMU.objects.filter(chassis_number=instance.chassis_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor rangka: {instance.chassis_number} — batal buat LegalDocument")
            return

    # ⚠️ Optional: Jika ingin izinkan lebih dari 1 document_number = agreement_number, maka hapus pengecekan ini.
    if LegalDocumentMU.objects.filter(document_number__iexact=clean_number).exists():
        print(f"🚫 LegalDocument already exists for document_number: {clean_number}")
        return

    try:
        legal_doc = LegalDocumentMU.objects.create(
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
    clean_number = str(instance.agreement_number).strip().upper() if instance.agreement_number else ""
    if not clean_number:
        print("⚠️ Skipping LegalDocument creation/update: agreement_number is empty")
        return

    # Validasi duplikat engine_number
    if instance.engine_number:
        if ConsumerMU.objects.filter(engine_number=instance.engine_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor mesin: {instance.engine_number} — batal")
            return

    # Validasi duplikat chassis_number
    if instance.chassis_number:
        if ConsumerMU.objects.filter(chassis_number=instance.chassis_number).exclude(id=instance.id).exists():
            print(f"❌ Duplikat nomor rangka: {instance.chassis_number} — batal")
            return

    try:
        print("🔁 Mulai _create_or_update_legal_document")
        print(f"📄 Agreement: {clean_number}")

        # Ambil kode cabang lengkap dan 4 digit terakhir
        branch_code_full = getattr(instance.branch, 'branch_code', None) if hasattr(instance, 'branch') and instance.branch else None
        print(f"🏢 Branch code: {branch_code_full}")
        branch_suffix = branch_code_full[-4:] if branch_code_full and len(branch_code_full) >= 4 else None
        print(f"🔍 Branch suffix (4 digit terakhir): {branch_suffix}")
        print(f"📦 Notaris awal (manual): '{instance.director_notary_name}'")

        # Mapping 4 digit akhir ke full_name notaris
        cabang_ke_notaris = {
            '0002': 'Kiki Kartikasari',
            '0003': 'Muchairani',
            '0004': 'Neltje Tewu',
            '0005': 'Ni Wayan Anik Parwati',
        }

        notary_instance = None

        if not instance.director_notary_name or instance.director_notary_name.strip().lower() == 'none':
            nama_notaris = cabang_ke_notaris.get(branch_suffix)
            print(f"🔎 Mapped notaris dari cabang: {nama_notaris}")
            if nama_notaris:
                notary_instance = Notary.objects.filter(
                    Q(firm_name__icontains=nama_notaris) | Q(full_name__icontains=nama_notaris)
                ).first()

                if notary_instance:
                    instance.director_notary_name = notary_instance.firm_name
                    instance.save(update_fields=['director_notary_name'])
                    print(f"✅ Notaris diisi otomatis dari cabang: {notary_instance.firm_name}")
                else:
                    print(f"⚠️ Nama notaris dari cabang '{branch_suffix}' tidak ditemukan di tabel Notary")
                    print("📋 Notaris tersedia di database:")
                    for n in Notary.objects.all():
                        print(f" - ID {n.id}: firm='{n.firm_name}' | full='{n.full_name}'")
            else:
                print(f"⚠️ Tidak ada mapping notaris untuk cabang: {branch_suffix}")
        else:
            nama = instance.director_notary_name.strip()
            notary_instance = Notary.objects.filter(
                Q(firm_name__icontains=nama) | Q(full_name__icontains=nama)
            ).first()

            if notary_instance:
                print(f"✅ Ditemukan Notary dari input manual: {notary_instance.firm_name}")
            else:
                print(f"⚠️ Nama notaris manual tidak ditemukan: '{nama}'")
                print("📋 Notaris tersedia di database:")
                for n in Notary.objects.all():
                    print(f" - ID {n.id}: firm='{n.firm_name}' | full='{n.full_name}'")

        # Update atau buat LegalDocument
        legal_doc = LegalDocumentMU.objects.filter(consumer_id=instance.id).first()
        document_date = None

        if legal_doc:
            print(f"✏️ Updating LegalDocument ID {legal_doc.id}")
            if legal_doc.notary != notary_instance:
                legal_doc.document_date = document_date
                legal_doc.notary = notary_instance
                legal_doc.updated_at = now()
                legal_doc.save()
                print("✅ LegalDocument berhasil diupdate")
            else:
                print("ℹ️ Notary tidak berubah")
        else:
            print("🆕 Membuat LegalDocument baru")
            LegalDocumentMU.objects.create(
                consumer=instance,
                document_number=None,
                document_date=document_date,
                notary=notary_instance,
                created_at=now(),
                updated_at=now(),
            )
            print("✅ LegalDocument berhasil dibuat")

    except Exception as e:
        print("❌ ERROR:", e)
        import traceback
        traceback.print_exc()
        
from UpdateDataMU.views import TemporaryUpdateBatchPatchAPIViewMU

@receiver(post_save, sender=TemporaryUpdateMU)
def apply_temporary_update_to_consumer(sender, instance, created, **kwargs):
    if instance.is_approved and instance.agreement_number:
        try:
            consumer = (ConsumerMU.objects.filter(agreement_number=instance.agreement_number, is_st_used=1).order_by('-id').first())

            # gunakan mapping
            api_view = TemporaryUpdateBatchPatchAPIViewMU()
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
                    ActivityLogMU.objects.create(
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
        except ConsumerMU.DoesNotExist:
            print(f"❌ Consumer not found with agreement_number: {instance.agreement_number}")
        except Exception as e:
            print(f"❌ Error applying temporary update: {e}")

@receiver(post_save, sender=LegalDocumentMU)
def update_consumer_from_legal_document(sender, instance, **kwargs):
    try:
        consumer = instance.consumer
        if not consumer:
            print(f"⚠️ LegalDocument {instance.id} has no consumer linked.")
            return

        updated_document = False
        today = now().date()

        # ✅ Jika document_number baru saja diisi dan document_date belum ada, isi document_date = today
        if instance.document_number and not instance.document_date:
            instance.document_date = today
            instance.save(update_fields=['document_date'])
            updated_document = True
            print(f"📄 Updated document_date for LegalDocument {instance.id} to {today}")

        # ❌ Tidak ada lagi sinkronisasi ke consumer.deed_number atau consumer.deed_date
        # Jadi cukup log info saja
        print(f"ℹ️ Consumer {consumer.agreement_number} TIDAK diupdate deed_number/deed_date dari LegalDocument {instance.id}")

    except Exception as e:
        print(f"❌ Error updating consumer from LegalDocument: {e}")
        import traceback
        traceback.print_exc()
        
@receiver(post_save, sender=ConsumerDocumentMU)
def update_activity_log_file_info(sender, instance, **kwargs):
    # ✅ Cek file_url valid
    file_url = str(instance.file_url or '').strip()
    if not file_url:
        print(f"⚠️ File URL kosong atau tidak valid untuk ConsumerDocumentMU ID {instance.id}")
        return

    try:
        # ✅ Ekstrak file name dan path
        filename = os.path.basename(file_url)
        file_path = file_url

        # ✅ Cari log activity
        log = ActivityLogMU.objects.filter(
            subject_id=instance.id,
            subject_type='ConsumerDocument',  # pastikan nilai ini juga konsisten saat membuat log
            log_name='UPLOAD_FILE'
        ).first()

        if log:
            # Pastikan field properties adalah dict
            if not isinstance(log.properties, dict):
                log.properties = {}

            log.properties['filename'] = filename
            log.properties['file_path'] = file_path

            log.save(update_fields=["properties"])

            print(f"✅ ActivityLog updated with file info: {filename}")
        else:
            print(f"⚠️ ActivityLog not found for ConsumerDocumentMU ID {instance.id}")

    except Exception as e:
        print(f"❌ ERROR updating ActivityLog for ConsumerDocumentMU ID {instance.id}: {e}")
        traceback.print_exc()