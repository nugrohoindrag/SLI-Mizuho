from django.apps import apps
from django.http import Http404

from common.enum.choices import AktaType
from AktaSertifikat.models import AktaMinutaLog, AktaMinutaLogOC, AktaMinutaLogMU, AktaMinutaLogPF

DOCUMENT_MODEL_LABELS = {
    AktaType.REGULER: "warkahDokument.LegalDocument",
    AktaType.OC:      "warkahDokumentOC.LegalDocumentOC",
    AktaType.MU:      "warkahDokumentMU.LegalDocumentMU",
    AktaType.PF:      "assignaktaPF.LegalDocumentPF",
}

CONSUMER_MODEL_LABELS = {
    AktaType.REGULER: "import_cabang.Consumer",
    AktaType.OC:      "import_cabang_oc.ConsumerOC",
    AktaType.MU:      "import_cabang_mu.ConsumerMU",
    AktaType.PF:      "change_fiduciary.change_of_fiduciary",
}

def get_document_model(akta_type: AktaType):
    label = DOCUMENT_MODEL_LABELS.get(_coerce_akta_type(akta_type))
    if not label: raise Http404("Tipe akta tidak dikenali.")
    return apps.get_model(label)

def get_consumer_model(akta_type: AktaType):
    label = CONSUMER_MODEL_LABELS.get(_coerce_akta_type(akta_type))
    if not label: raise Http404("Tipe akta tidak dikenali.")
    return apps.get_model(label)

# ----- LOG MODEL MAP: pakai enum sebagai key (bukan string) -----
AKTA_LOG_MODEL_MAP = {
    AktaType.REGULER: AktaMinutaLog,     # default / reguler
    AktaType.OC:      AktaMinutaLogOC,
    AktaType.MU:      AktaMinutaLogMU,
    AktaType.PF:      AktaMinutaLogPF,
}

def get_log_model(akta_type):
    """
    akta_type boleh instance AktaType atau string ('reguler'|'oc'|'mu'|'pf').
    Kembalikan class model log yang sesuai. Fallback ke AktaMinutaLog.
    """
    at = _coerce_akta_type(akta_type)
    return AKTA_LOG_MODEL_MAP.get(at, AktaMinutaLog)

# ----- util kecil agar aman terima enum/string -----
def _coerce_akta_type(akta_type):
    if isinstance(akta_type, AktaType):
        return akta_type
    try:
        # coba konversi dari value string ('reguler','oc','mu','pf')
        return AktaType(akta_type)
    except Exception:
        # terakhir coba dari name string ('REGULER','OC','MU','PF')
        try:
            return AktaType[ str(akta_type).upper() ]
        except Exception:
            return None