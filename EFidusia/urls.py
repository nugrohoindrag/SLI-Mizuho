from django.urls import path, include, register_converter, re_path, path
from accesslog import views as log
from accessloguser import views as loguser
from accesslogoc import views as logoc
from accesslogmu import views as logmu
from AdminUser import views as main
from AdminMenu import views as Admin
from AdminPermission import views as Permission
from AdminRole import views as Role
from AdminNotaries import views as Notaries 
from AdminProxies import views as Proxies
from AdminRecipients import views as Recipients
from AdminPnbp import views as PnbpConfig
from core.converters import AktaTypeConv, DocTypeConv
from import_cabang import views as cabang
from import_cabang_oc import views as cabangoc
from import_cabang_mu import views as cabangmu
from prekonsumen_oc import views as prekonsumenoc
from change_fiduciary import views as PF
from warkahDokument import views as warkah
from warkahDokumentMU import views as warkahmu
from warkahDokumentOC import views as warkahoc
from warkahDokumentPF import views as warkahpf
from import_document import views as dokument
from import_document_pf import views as dokumentpf
from import_document_mu import views as dokumentmu
from import_document_oc import views as dokumentoc
from UpdateData import views as updatedata
from UpdateDataMU import views as updatedatamu
from assignaktaPF import views as assignpf
from assignaktaOC import views as assignoc
from UpdateDataPF import views as updatedatapf
from UpdateDataOC import views as updatedataoc
from FinanceOC import views as financeoc
from FinanceMU import views as financemu
from prekonsumen import views as prekonsumen
from invoice import views as invoice
from invoiceOC import views as invoiceoc
from invoiceMU import views as invoicemu
from prekonsumen_mu import views as prekonsumen_mu
from prekonsumen_pf import views as prekonsumen_pf
from assignakta import views as assign
from assignaktaMU import views as assignmu
from AktaSertifikat import views as akser
from AktaSertifikat import mizuho_views as akser_mizuho
from billing import views as billing
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework.routers import DefaultRouter
from django.conf import settings
from django.conf.urls.static import static

register_converter(AktaTypeConv, "akta")
register_converter(DocTypeConv, "doc")

# from rest_framework import permissions
# from drf_yasg.views import get_schema_view
# from drf_yasg import openapi
# from django.urls import path
# from drf_yasg.utils import swagger_auto_schema
# from rest_framework.decorators import api_view
# from rest_framework.response import Response

# schema_view = get_schema_view(
#    openapi.Info(
#       title="Dok API TAF-SLI",
#       default_version='v1',
#       description="Dokumen API Django PostgreSQL",
#       contact=openapi.Contact(email="rifai.alpath@saranalegalitas.id"),
#       license=openapi.License(name="SLI License"),
#    ),
#    public=True,
#    permission_classes=(permissions.AllowAny,),
# )

# @swagger_auto_schema(
#     method='get',
#     operation_description="Test Swagger",
#     responses={200: openapi.Response('Success')}
# )
# @api_view(['GET'])
# def test_view(request):
#     return Response({"message": "ok"})

router = DefaultRouter()
router.register(r'users', main.UserViewSet, basename='user')
router.register(r'consumer-documents', dokument.ConsumerDocumentViewSet, basename='consumerdocument')
router.register(r'consumer-documents-pf', dokumentpf.ConsumerDocumentViewSetPF, basename='consumerdocumentpf')
router.register(r'consumer-documents-oc', dokumentoc.ConsumerDocumentViewSetOC, basename='consumerdocumentoc')
router.register(r'consumer-documents-mu', dokumentmu.ConsumerDocumentViewSetMU, basename='consumerdocumentmu')
router.register(r'legal-documents', warkah.LegalDocumentViewSet, basename='legal-document')
router.register(r'legal-documents-mu', warkahmu.LegalDocumentViewSetMU, basename='legal-document-mu')
router.register(r'legal-documents-oc', warkahoc.LegalDocumentViewSetOC, basename='legal-document-oc')
router.register(r'legal-documents-pf', warkahpf.LegalDocumentViewSetPF, basename='legal-document-pf')

urlpatterns = [
    # path('test/', test_view),
    # path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    # path('swagger.json', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    # path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path("login/", main.view_login_page, name="login"),
    path("api/", include(router.urls)),
    
    path('check-sertif/', akser.check_sertif_view, name='check_sertif'),
    path('check-akta/', akser.check_akta_view, name='check_akta'),
    
    # 🔹 API Endpoints
    path('api/dashboard-stats/', main.dashboard_stats, name='dashboard_stats'),
    path('api/dashboard-graph/', main.dashboard_graph, name='dashboard_graph'),
    path('api/dashboard/excel/', main.dashboard_excel, name='dashboard_excel'),
    path('api/report-kelengkapan/', main.report_kelengkapan, name='report_kelengkapan'),
    path("dashboard/", main.index, name="dashboard"),
    path("api/login/", main.login_view, name="login_api"),
    path("verify-otp/", main.verify_otp, name="verify_otp"),
    path("resend-otp/", main.resend_otp, name="resend_otp"),
    path("api/logout/", main.logout_view, name="logout_api"),
    
    path('api/companies/', main.list_companies, name='list-companies'),
    path('api/provinces/', main.list_provinces, name='list_provinces'),
    path("profile/", main.view_user_profile_page, name="profile"),
    path("api/user/profile/", main.get_user_profile, name="get-user-profile"),
    path('api/user/profile/update/', main.update_user_profile, name='update-user-profile'),
    path("api/user/change-password/", main.change_password, name="change_password"),

    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/csrf/", main.get_csrf_token, name="csrf"),
    path("api/roles/", main.RoleListAPIView.as_view(), name="role_list_api"),
    path("api/branches/", main.BranchListAPIView.as_view(), name="branch_list_api"),
    
    path("admin/riwayat-login/", main.historyuser, name="history_user"),
    path("api/login-history/", main.login_history_api, name="login_history_api"),
    
    # 🔹Admin Users
    path("admin/users/", main.view_users_admin, name="admin_users"),
    path("admin/users/create/", main.view_create_users_admin, name="admin_users_create"),
    
    # 🔹Admin Permission
    path("admin/permission/", Permission.view_permission_users_admin, name="admin_permission"),
    path('api/permissions/', Permission.PermissionListView.as_view(), name='permission-list'),
    path("admin/permission/create/", Permission.create_permission, name="api_permission_create"),
    path("admin/permission/edit/<int:id>/", Permission.update_permission, name="api_permission_edit"),
    path("admin/permission/delete/<int:id>/", Permission.delete_permission, name="api_permission_delete"),
    path("admin/permission/data/", Permission.list_permission_json, name="api_permission_list"),
    
    # 🔹Menu Admin Page
    path("admin/menu/", Admin.view_menu_admin, name="admin_menu"),
    path("api/menu/all/", Admin.MenuListCreateView.as_view(), name="api_menu_all_admin"),
    path("api/menu/", Admin.get_sidebar_menu, name="api_menu_sidebar"),
    path("api/menu/<int:pk>/", Admin.MenuDetailView.as_view(), name="api_menu_detail"),
    path("api/permission/<int:pk>/", Admin.PermissionDetailView.as_view(), name="api_permission_detail"),
    
    #🔹Admin Role
    path("admin/role/", Role.view_role_admin, name="admin_role"),
    path('api/admin/roles/', Role.RoleListCreateView.as_view(), name='role_list_create'),
    path('api/roles/<int:pk>/', Role.RoleDetailView.as_view(), name='role_detail'),
    path('api/roles/<int:role_id>/menu/<int:menu_id>/', Role.RoleMenuPermissionView.as_view()),
    
    # 🔹Admin Access Log
    path("admin/access-log/", log.access_log, name="access_log"),
    path('api/access-log/', log.ActivityLogListAPIView.as_view(), name='activity_log_list'),

    # 🔹Admin Upload Log
    path("admin/upload-log/", log.upload_log, name="upload_log"),
    path('api/upload-log/', log.UploadLogListAPIView, name='upload_log_list'),
    
    # 🔹Admin Access Log User
    path("admin/access-log-user/", loguser.access_log_user, name="access_log_user"),
    path('api/access-log-user/', loguser.ActivityLogListAPIViewUser.as_view(), name='activity_log_list_user'),
    path('api/upload-log-user/', loguser.UploadLogListAPIViewUser.as_view(), name='upload_log_list_user'),
    
    #🔹Admin Notaries
    path("admin/notaris/", Notaries.view_notaris_users_admin, name="admin_notaris"),
    path("admin/notaris/create/", Notaries.create_notary, name="api_notaris_create"),
    path("admin/notaris/edit/<int:id>/", Notaries.edit_notary, name="api_notaris_edit"),
    path("admin/notaris/delete/<int:id>/", Notaries.delete_notary, name="api_notaris_delete"),
    path('admin/notaris/data/', Notaries.get_notary_data, name='api_notaris_list'),

    #🔹Admin Recipients
    path("admin/penerima-fidusia/", Recipients.view_pnrmfds_users_admin, name="admin_pnrmfds"),
    path('api/penerima-fidusia/create', Recipients.create_recipient, name='api_pnrmfds_create'),
    path('api/penerima-fidusia/edit/<int:id>/', Recipients.edit_recipient, name='api_pnrmfds_edit'),
    path('api/penerima-fidusia/delete/<int:id>/', Recipients.delete_recipient, name='api_pnrmfds_delete'),
    path('api/penerima-fidusia/data/', Recipients.get_recipient_data, name='api_pnrmfds_list'),
    path('api/penerima-fidusia/datatable/', Recipients.get_recipient_datatable, name='api_pnrmfds_datatable'),

    # Admin-only dynamic PNBP configuration for Regular and Over Credit.
    path("admin/pnbp-config/", PnbpConfig.pnbp_config_page, name="admin_pnbp_config"),
    path("api/admin/pnbp-rates/", PnbpConfig.pnbp_rate_list, name="api_admin_pnbp_rates"),
    path("api/admin/pnbp-rates/<int:pk>/", PnbpConfig.pnbp_rate_detail, name="api_admin_pnbp_rate_detail"),

    #🔹Admin Proxies
    path("admin/penerima-kuasa/", Proxies.view_pnrmks_users_admin, name="admin_pnrmks"),
    path('api/penerima-kuasa/create', Proxies.create_proxies, name='api_pnrmks_create'),
    path('api/penerima-kuasa/edit/<int:id>/', Proxies.edit_proxies, name='api_pnrmks_edit'),
    path('api/penerima-kuasa/delete/<int:id>/', Proxies.delete_proxies, name='api_pnrmks_delete'),
    path('api/penerima-kuasa/data/', Proxies.get_proxies_data, name='api_pnrmks_list'),
    
    path("admin/saksi/", Admin.view_saksi_users_admin, name="admin_saksi"),
    path("api/witnesses/", Admin.WitnessListCreateView.as_view(), name="witness_list_create"),
    path("api/witnesses/<int:pk>/", Admin.WitnessRetrieveUpdateDeleteView.as_view(), name="witness_rud"),
    path("api/witnesses/options/provinces/", Admin.ProvinceOptionsView.as_view(), name="witness_province_options"),
    path("api/witnesses/options/notaries/", Admin.NotaryOptionsView.as_view(), name="witness_notary_options"),
    path("api/witnesses/datatables/", Admin.WitnessDataTableView.as_view(), name="witnesses_datatables"),
    
    #🔹Upload File - Role Staff Cabang
    path('staff-cabang-import/', cabang.view_staff_cabang_import, name='staff-cabang-import'),
    path('api/consumers/preview_excel/', cabang.preview_excel, name='preview_excel'),
    path('api/consumers/save_excel_data/', cabang.save_excel_data, name='save_excel_data'),

    #🔹Upload Dokuments - Role Staff Cabang
    path('staff-cabang/document/', dokument.view_staff_cabang_document, name='view_staff_cabang_document'),
    path('download-template/', dokument.download_template, name='download_template'),
    path('api/upload-template/', dokument.upload_template_excel, name='upload_template_excel'),
    path('api/upload-template-patch/', dokument.upload_template_patch, name='upload_template_patch'),
    
    #🔹Dokuments Warkah - Role Warkah
    path('warkah/dokumen/', warkah.WarkahView.as_view(), name='warkahdownload_hybrid'),
    path('api/warkah/data/', warkah.WarkahDocumentDataAPIView.as_view(), name='warkah_document_data'),
    path('api/upload-excel-no-perjanjian', warkah.WarkahExcelUploadNoPerjanjianAPIView.as_view(), name='upload_excel_no_perjanjian'),
    
    #🔹Dokuments Warkah MU- Role Warkah
    path('warkah/dokumen-mu/', warkahmu.WarkahViewMU.as_view(), name='warkahdownload_hybrid_mu'),
    path('api/warkah/data-mu/', warkahmu.WarkahDocumentDataAPIViewMU.as_view(), name='warkah_document_data_mu'),
    path('api/upload-excel-warkah-mu', warkahmu.WarkahExcelUploadMUAPIView.as_view(), name='upload_excel_warkah_mu'),

    #🔹Dokuments Warkah OC- Role Warkah
    path('warkah/dokumen-oc/', warkahoc.WarkahViewOC.as_view(), name='warkahdownload_hybrid_oc'),
    path('api/warkah/data-oc/', warkahoc.WarkahDocumentDataAPIViewOC.as_view(), name='warkah_document_data_oc'),
    path('api/upload-excel-warkah-oc', warkahoc.WarkahExcelUploadOCAPIView.as_view(), name='upload_excel_warkah_oc'),

    #🔹Dokuments Warkah PF- Role Warkah
    path('warkah/dokumen-pf/', warkahpf.WarkahViewPF.as_view(), name='warkahdownload_hybrid_pf'),
    path('api/warkah/data-pf/', warkahpf.WarkahDocumentDataAPIViewPF.as_view(), name='warkah_document_data_pf'),
    path('api/upload-excel-warkah-pf', warkahpf.WarkahExcelUploadPFAPIView.as_view(), name='upload_excel_warkah_pf'),
    
    #🔹Update Data - Role Operator
    path('temporary-updates/', updatedata.TemporaryUpdatePageView.as_view(), name='temporary-update-list'),
    path('api/temporary-updates/', updatedata.TemporaryUpdateListCreateAPIView.as_view(), name='temporary-update-list-create'),
    path('api/temporary-updates/<int:pk>/', updatedata.TemporaryUpdateDetailAPIView.as_view(), name='temporary-update-detail'),
    path('api/temporary-updates/upload-excel/', updatedata.TemporaryUpdateExcelUploadAPIView.as_view(), name='temporary-update-upload-excel'),
    path('api/temporary-updates/batch/', updatedata.TemporaryUpdateBatchPatchAPIView.as_view(), name='temporary-update-batch'),
    
    #🔹Update Data MU- Role Operator
    path('temporary-updates-mu/', updatedatamu.TemporaryUpdatePageViewMU.as_view(), name='temporary-update-list-mu'),
    path('api/temporary-updates-mu/', updatedatamu.TemporaryUpdateListCreateAPIViewMU.as_view(), name='temporary-update-list-create-mu'),
    path('api/temporary-updates-mu/<int:pk>/', updatedatamu.TemporaryUpdateDetailAPIViewMU.as_view(), name='temporary-update-detail-mu'),
    path('api/temporary-updates-mu/upload-excel/', updatedatamu.TemporaryUpdateExcelUploadAPIViewMU.as_view(), name='temporary-update-upload-excel-mu'),
    path('api/temporary-updates-mu/batch/', updatedatamu.TemporaryUpdateBatchPatchAPIViewMU.as_view(), name='temporary-update-batch-mu'),
    
    #🔹Pre Konsumen - Role Operator
    path('prekonsumen/', prekonsumen.ConsumerListCreateAPIView.as_view(), name='consumer-list-create'),
    path('api/prekonsumen-json/', prekonsumen.ConsumerDataJSON.as_view(), name='consumer-data-json'),
    path('consumers/<int:pk>/', prekonsumen.ConsumerDetailAPIView.as_view(), name='consumer-detail'),
    path('api/consumer/export/', prekonsumen.ConsumerExportAPIView.as_view(), name='export_consumer_excel'),
    path('api/prekonsumen/otd-upload/', prekonsumen.OtdUploadAPIView.as_view(), name='prekonsumen-otd-upload'),
    path('api/prekonsumen/otd-template/', prekonsumen.OtdTemplateAPIView.as_view(), name='prekonsumen-otd-template'),
    
    #🔹Pre Konsumen MU - Role Operator
    path('prekonsumen-mu/', prekonsumen_mu.ConsumerListCreateAPIViewMU.as_view(), name='consumer-list-create-mu'),
    path('api/prekonsumen-json-mu/', prekonsumen_mu.ConsumerDataJSONMU.as_view(), name='consumer-data-json-mu'),
    path('consumers-mu/<int:pk>/', prekonsumen_mu.ConsumerDetailAPIViewMU.as_view(), name='consumer-detail-mu'),
    path('api/consumer/export-mu/', prekonsumen_mu.ConsumerExportAPIViewMU.as_view(), name='export_consumer_excel-mu'),
    path('api/consumer/exportall-mu/', prekonsumen_mu.ConsumerExportALLAPIViewMU.as_view(), name='export_all_consumer_excel-mu'),
    
    #🔹Assign Akta - Role Operator
    path('assignakta/', assign.AssignListCreateAPIView.as_view(), name='operator-assign-akta'),
    path('api/assignakta-json/', assign.AssignDataJSON.as_view(), name='assign-data-json'),
    path('assign/<int:pk>/', assign.AssignDetailAPIView.as_view(), name='assign-detail'),
    path('api/assignakta/<akta:akta_type>/<int:id>/documents/<doc:doc_type>/download/', assign.AssignDownloadAktaDoc.as_view(), name='assignakta-doc-download'),
    path('api/check-akta-download/<slug:scope>/', assign.check_akta_download_status, name='check_akta_download_status'),

    path('assignakta/notaris/', assign.get_notaris_list, name='get_notaris_list'),
    path('assignakta/generate/', assign.AssignGenerateAPIView.as_view(), name='generate_akta'),
    #path('assignakta/legal-detail/<int:notary_id>/', assign.LegalDocumentTodayAPIView.as_view()),
    path('assignakta/legal-upsert/', assign.LegalDocumentUpsertAPIView.as_view(), name='legal-upsert'),
    
    path('export_excel_by_date_range/', assign.export_excel_by_date_range, name='export_excel_by_date_range'),
    path('export_template_filtered/', assign.export_template_filtered, name='export_template_filtered'),
    
    #🔹Assign Akta MU - Role Operator
    path('assignakta-mu/', assignmu.AssignListCreateAPIViewMU.as_view(), name='operator-assign-akta-mu'),
    path('api/assignakta-json-mu/', assignmu.AssignDataJSONMU.as_view(), name='assign-data-json-mu'),
    path('assign-mu/<int:pk>/', assignmu.AssignDetailAPIViewMU.as_view(), name='assign-detail-mu'),
    path('assignakta/print-mu/<int:pk>/', assignmu.AssignPrintViewMU.as_view(), name='assignakta-print-mu'),
    path('assignakta/export-excel-mu/<int:pk>/', assignmu.AssignExportExcelSatuanViewMU.as_view(), name='assignakta-export-satuan-mu'),

    path('assignakta/notaris-mu/', assignmu.get_notaris_list_mu, name='get_notaris_list_mu'),
    path('assignakta/generate-mu/', assignmu.AssignGenerateAPIViewMU.as_view(), name='generate_akta_mu'),
    #path('assignakta/legal-detail-mu/<int:notary_id>/', assignmu.LegalDocumentTodayAPIViewMU.as_view()),
    path('assignakta/legal-upsert-mu/', assignmu.LegalDocumentUpsertAPIViewMU.as_view(), name='legal-upsert-mu'),
    
    path('export_excel_by_date_range-mu/', assignmu.export_excel_by_date_range_mu, name='export_excel_by_date_range_mu'),
    path('export_template_filtered-mu/', assignmu.export_template_filtered_mu, name='export_template_filtered_mu'),

    path('aktasertifikat/', akser.akta_sertifikat_list_view, name='akser-list'),
    path('aktasertifikat/export-excel/', akser.export_excel_akser, name='export_excel_akser'),
    path('aktasertifikat/export-laporan-mizuho/', akser_mizuho.export_laporan_mizuho, name='export_laporan_mizuho'),
    path('aktasertifikat/export-feedback-mizuho/', akser_mizuho.export_feedback_mizuho, name='export_feedback_mizuho'),
    path('download/bukti-bayar/<int:id>/', akser_mizuho.download_bukti_bayar, name='download_bukti_bayar'),
    path('update-sertifikat/bukti-bayar/<int:id>/', akser_mizuho.upload_bukti_bayar, name='upload_bukti_bayar'),
    path('download/sertifikat/<int:id>/', akser.download_sertifikat, name='download_sertifikat'),
    path('download/sertifikat-pf/<int:id>/', akser.download_sertifikat_pf, name='download_sertifikat_pf'),
    path('download/sertifikat-oc/<int:id>/', akser.download_sertifikat_oc, name='download_sertifikat_oc'),
    path('download/sertifikat-mu/<int:id>/', akser.download_sertifikat_mu, name='download_sertifikat_mu'),
    path('download/akta/<int:id>/', akser.download_akta, name='download_akta'),
    path('download/akta-pf/<int:id>/', akser.download_akta_pf, name='download_akta_pf'),
    path('download/akta-oc/<int:id>/', akser.download_akta_oc, name='download_akta_oc'),
    path('download/akta-mu/<int:id>/', akser.download_akta_mu, name='download_akta_mu'),
    
    #🔹Perubahan Fidusia - Role Staff Cabang
    path('perubahan-fidusia/', PF.PFListCreateAPIView.as_view(), name='pf-list-create'),
    path('api/PF-json/',  PF.PFDataJSON.as_view(), name='consumer-data-json'),
    path('PF/<int:pk>/', PF.PFDetailAPIView.as_view(), name='consumer-detail'),
    path('perubahan-fidusia/proses/', PF.ProcessChangeOfFiduciaryAPIView.as_view(), name='pf-proses'),
    path('api/consumer-by-agreement/<str:agreement_number>/', PF.get_consumer_by_agreement, name='pf-consumer-by-agreement'),
    
    path('pf/logs/', PF.PFLogListView.as_view(), name='pf_log_list'),
    path('pf/logs/data/', PF.PFLogDataJSON.as_view(), name='pf_log_data_json'),
    
    #🔹Pre Konsumen PF - Role Operator
    path('prekonsumen-pf/', prekonsumen_pf.ConsumerListCreateAPIViewPF.as_view(), name='consumer-list-create-pf'),
    path('api/prekonsumen-json-pf/', prekonsumen_pf.ConsumerDataJSONPF.as_view(), name='consumer-data-json-pf'),
    path('consumers-pf/<int:pk>/', prekonsumen_pf.ConsumerDetailAPIViewPF.as_view(), name='consumer-detail-pf'),
    path('api/consumer-pf/export/', prekonsumen_pf.ConsumerExportAPIViewPF.as_view(), name='export_consumer_excel-pf'),
    
    #🔹Assign Akta PF - Role Operator
    path('assignakta-pf/', assignpf.AssignListCreateAPIViewPF.as_view(), name='operator-assign-akta-pf'), 
    path('api/assignakta-json-pf/', assignpf.AssignDataJSONPF.as_view(), name='assign-data-json-pf'),
    path('assign-pf/<int:pk>/', assignpf.AssignDetailAPIViewPF.as_view(), name='assign-detail-pf'),
    path('assignakta-pf/print/<int:pk>/', assignpf.AssignPrintViewPF.as_view(), name='assignakta-print-pf'),
    path('assignakta-pf/export-excel/<int:pk>/', assignpf.AssignExportExcelSatuanViewPF.as_view(), name='assignakta-export-satuan-pf'),

    path('assignakta-pf/notaris/', assignpf.get_notaris_list_pf, name='get_notaris_list_pf'),
    path('assignakta-pf/generate/', assignpf.AssignGenerateAPIViewPF.as_view(), name='generate_akta_pf'),
    #path('assignakta-pf/legal-detail/<int:notary_id>/', assignpf.LegalDocumentTodayAPIViewPF.as_view()),
    path('assignakta-pf/legal-upsert/', assignpf.LegalDocumentUpsertAPIViewPF.as_view(), name='legal-upsert-pf'),
    
    path('export_excel_by_date_range_pf/', assignpf.export_excel_by_date_range_pf, name='export_excel_by_date_range_pf'),
    path('export_template_filtered_pf/', assignpf.export_template_filtered_pf, name='export_template_filtered_pf'),
    
    #🔹Update Data PF - Role Operator
    path('temporary-updates-pf/', updatedatapf.TemporaryUpdatePageViewPF.as_view(), name='temporary-update-list-pf'),
    path('api/temporary-updates-pf/', updatedatapf.TemporaryUpdateListCreateAPIViewPF.as_view(), name='temporary-update-list-create-pf'),
    path('api/temporary-updates-pf/<int:pk>/', updatedatapf.TemporaryUpdateDetailAPIViewPF.as_view(), name='temporary-update-detail-pf'),
    path('api/temporary-updates-pf/upload-excel/', updatedatapf.TemporaryUpdateExcelUploadAPIViewPF.as_view(), name='temporary-update-upload-excel-pf'),
    path('api/temporary-updates-pf/batch/', updatedatapf.TemporaryUpdateBatchPatchAPIViewPF.as_view(), name='temporary-update-batch-pf'),
    
    #🔹Upload Dokuments PF - Role Staff Cabang
    path('staff-cabang/document-pf/', dokumentpf.view_staff_cabang_document_pf, name='view_staff_cabang_document_pf'),
    path('download-template-pf/', dokumentpf.download_template_pf, name='download_template_pf'),
    path('api/upload-template-pf/', dokumentpf.upload_template_excel_pf, name='upload_template_excel_pf'),
    
    #🔹Upload Dokuments PF - Role Staff Cabang
    path('staff-cabang/document-mu/', dokumentmu.view_staff_cabang_document_mu, name='view_staff_cabang_document_mu'),
    path('download-template-mu/', dokumentmu.download_template_mu, name='download_template_mu'),
    path('api/upload-template-mu/', dokumentmu.upload_template_excel_mu, name='upload_template_excel_mu'),
    
    #🔹Upload File OC - Role Staff Cabang
    path('staff-cabang-import-oc/', cabangoc.view_staff_cabang_import_oc, name='staff-cabang-import-oc'),
    path('api/consumers/preview_excel-oc/', cabangoc.preview_excel_oc, name='preview_excel-oc'),
    path('api/consumers/save_excel_data-oc/', cabangoc.save_excel_data_oc, name='save_excel_data-oc'),
    
    # 🔹Admin Access Log OC
    path("admin/access-log-oc/", logoc.access_log_oc, name="access_log_oc"),
    path('api/access-log-oc/', logoc.ActivityLogListAPIViewOC.as_view(), name='activity_log_list_oc'),
    path("admin/upload-log-oc/", logoc.upload_log_oc, name="upload_log_oc"),
    path('api/upload-log-oc/', logoc.UploadLogListAPIViewOC, name='upload_log_list_oc'),
    path('api/upload-log-oc/range/', logoc.UploadLogRangeAPIViewOC.as_view(), name='upload-log-oc-range'),
    path('api/branches-oc/', logoc.branch_list_oc, name='branch_list_oc'),
    
    # 🔹Admin Access Log MU
    path("admin/access-log-mu/", logmu.access_log_MU, name="access_log_mu"),
    path('api/access-log-mu/', logmu.ActivityLogListAPIViewMU.as_view(), name='activity_log_list_mu'),
    path("admin/upload-log-mu/", logmu.upload_log_mu, name="upload_log_mu"),
    path('api/upload-log-mu/', logmu.UploadLogListAPIViewmu, name='upload_log_list_mu'),
    path('api/branches-mu/', logmu.branch_list_mu, name='branch_list_mu'),
    
    #🔹Pre Konsumen OC - Role Operator
    path('prekonsumen-oc/', prekonsumenoc.ConsumerListCreateAPIViewOC.as_view(), name='consumer-list-create-oc'),
    path('api/prekonsumen-json-oc/', prekonsumenoc.ConsumerDataJSONOC.as_view(), name='consumer-data-json-oc'),
    path('consumers-oc/<int:pk>/', prekonsumenoc.ConsumerDetailAPIViewOC.as_view(), name='consumer-detail-oc'),
    path('api/consumer/export-oc/', prekonsumenoc.ConsumerExportAPIViewOC.as_view(), name='export_consumer_excel-oc'),
    
    #🔹Assign Akta OC - Role Operator
    path('assignakta-oc/', assignoc.AssignListCreateAPIViewOC.as_view(), name='operator-assign-akta-oc'),
    path('api/assignakta-json-oc/', assignoc.AssignDataJSONOC.as_view(), name='assign-data-json-oc'),
    path('assign-oc/<int:pk>/', assignoc.AssignDetailAPIViewOC.as_view(), name='assign-detail-oc'),

    path('assignakta/notaris-oc/', assignoc.get_notaris_list_oc, name='get_notaris_list_oc'),
    path('assignakta/generate-oc/', assignoc.AssignGenerateAPIViewOC.as_view(), name='generate_akta_oc'),
    #path('assignakta/legal-detail-oc/<int:notary_id>/', assignoc.LegalDocumentTodayAPIViewOC.as_view()),
    path('assignakta/legal-upsert-oc/', assignoc.LegalDocumentUpsertAPIViewOC.as_view(), name='legal-upsert-oc'),
    
    path('export_excel_by_date_range-oc/', assignoc.export_excel_by_date_range_oc, name='export_excel_by_date_range_oc'),
    path('export_template_filtered-oc/', assignoc.export_template_filtered_oc, name='export_template_filtered_oc'),
    
    #🔹Update Data OC - Role Operator
    path('temporary-updates-oc/', updatedataoc.TemporaryUpdatePageViewOC.as_view(), name='temporary-update-list-oc'),
    path('api/temporary-updates-oc/', updatedataoc.TemporaryUpdateListCreateAPIViewOC.as_view(), name='temporary-update-list-create-oc'),
    path('api/temporary-updates-oc/<int:pk>/', updatedataoc.TemporaryUpdateDetailAPIViewOC.as_view(), name='temporary-update-detail-oc'),
    path('api/temporary-updates-oc/upload-excel/', updatedataoc.TemporaryUpdateExcelUploadAPIViewOC.as_view(), name='temporary-update-upload-excel-oc'),
    path('api/temporary-updates-oc/batch/', updatedataoc.TemporaryUpdateBatchPatchAPIViewOC.as_view(), name='temporary-update-batch-oc'),
    
    #🔹Upload File MU - Role Staff Cabang
    path('staff-cabang-import-mu/', cabangmu.view_staff_cabang_import_mu, name='staff-cabang-import-mu'),
    path('api/consumers/preview_excel-mu/', cabangmu.preview_excel_mu, name='preview_excel_mu'),
    path('api/consumers/save_excel_data-mu/', cabangmu.save_excel_data_mu, name='save_excel_data_mu'),
    
    #🔹Dokuments Warkah OC - Role Warkah
    path('warkahoc/dokumen/', warkahoc.WarkahViewOC.as_view(), name='warkahdownload_hybrid_oc'),
    path('api/warkah/data-oc/', warkahoc.WarkahDocumentDataAPIViewOC.as_view(), name='warkah_document_data_oc'),
    
    #🔹Upload Dokuments OC - Role Staff Cabang
    path('staff-cabang/document-oc/', dokumentoc.view_staff_cabang_document_oc, name='view_staff_cabang_document_oc'),
    path('download-template-oc/', dokumentoc.download_template_oc, name='download_template'),
    path('api/upload-template-oc/', dokumentoc.upload_template_excel_oc, name='upload_template_excel'),
    
    #🔹Dokuments Warkah PF - Role Warkah
    path('warkah/dokumen-pf/', warkahpf.WarkahViewPF.as_view(), name='warkahdownload_hybrid_pf'),

    path('update-sertifikat/', akser.update_sertifikat_list, name='update-sertif-list'),
    path('update-sertifikat-pf/', akser.update_sertifikat_list_pf, name='update-sertif-list-pf'),
    path('update-sertifikat-oc/', akser.update_sertifikat_list_oc, name='update-sertif-list-oc'),
    path('update-sertifikat-mu/', akser.update_sertifikat_list_mu, name='update-sertif-list-mu'),
    path('finance-reg/', akser.finance_reg_list, name='finance-reg-list'),
    path('update-sertifikat/upload-excel/', akser.import_excel_sertifikat, name='upload_excel_sertifikat'),
    path('update-sertifikat/upload-excel-pf/', akser.import_excel_sertifikat_pf, name='upload_excel_sertifikat_pf'),
    path('update-sertifikat-oc/upload-excel/', akser.import_excel_sertifikat_oc, name='upload_excel_sertifikat_oc'),
    path('update-sertifikat-mu/upload-excel/', akser.import_excel_sertifikat_mu, name='upload_excel_sertifikat_mu'),
    path('update-sertifikat/export-excel/', akser.export_excel_sertifikat, name='export_excel_sertifikat'),
    path('update-sertifikat-pf/export-excel/', akser.export_excel_sertifikat_pf, name='export_excel_sertifikat_pf'),
    path('update-sertifikat-oc/export-excel/', akser.export_excel_sertifikat_oc, name='export_excel_sertifikat_oc'),
    path('update-sertifikat-mu/export-excel/', akser.export_excel_sertifikat_mu, name='export_excel_sertifikat_mu'),
    path('finance-reg/export-excel/', akser.export_excel_finance, name='export_excel_finance'),
    path('finance-reg/upload-excel/', akser.import_excel_finance, name='upload_excel_finance'),
    path("update-sertifikat/update-sertifikat-bulk/", akser.update_sertifikat_bulk, name="update_sertifikat_bulk"),
    path("update-sertifikat-pf/update-sertifikat-bulk-pf/", akser.update_sertifikat_bulk_pf, name="update_sertifikat_bulk_pf"),
    path("update-sertifikat-oc/update-sertifikat-bulk-oc/", akser.update_sertifikat_bulk_oc, name="update_sertifikat_bulk_oc"),
    path("update-sertifikat-mu/update-sertifikat-bulk-mu/", akser.update_sertifikat_bulk_mu, name="update_sertifikat_bulk_mu"),

    path('akta-minuta-log/', akser.akta_minuta_log_list, name='akta-minuta-log-list'),
    path('api/akta-minuta-log/', akser.akta_minuta_log_api, name='akta-minuta-log-api'),
    path('akta-minuta-log-pf/', akser.akta_minuta_log_list_pf, name='akta-minuta-log-list-pf'),
    path('api/akta-minuta-log-pf/', akser.akta_minuta_log_api_pf, name='akta-minuta-log-api-pf'),
    path('akta-minuta-log-oc/', akser.akta_minuta_log_list_oc, name='akta-minuta-log-list-oc'),
    path('api/akta-minuta-log-oc/', akser.akta_minuta_log_api_oc, name='akta-minuta-log-api-oc'),
    path('akta-minuta-log-mu/', akser.akta_minuta_log_list_mu, name='akta-minuta-log-list-mu'),
    path('api/akta-minuta-log-mu/', akser.akta_minuta_log_api_mu, name='akta-minuta-log-api-mu'),
    
    path('akta-minuta-log/upload-excel/', akser.upload_akta_minuta_log, name='upload_excel_akta_minuta_log'),
    path('akta-minuta-log/upload-excel-oc/', akser.upload_akta_minuta_log_oc, name='upload_excel_akta_minuta_log_oc'),
    path('akta-minuta-log/upload-excel-mu/', akser.upload_akta_minuta_log_mu, name='upload_excel_akta_minuta_log_mu'),
    path('akta-minuta-log/upload-excel-pf/', akser.upload_akta_minuta_log_pf, name='upload_excel_akta_minuta_log_pf'),

    path('dokumen-warkah-pf/', warkah.dokumen_pf_list, name='data_doc_pf'),
    path('akser-pf/', akser.akta_sertifikat_pf_list_view, name='akser-pf-list'),
    path('akser-pf/export-excel/', akser.export_excel_akser_pf, name='export_excel_akser_pf'),
    path('akser-oc/', akser.akta_sertifikat_oc_list_view, name='akser-oc-list'),
    path('akser-oc/export-excel/', akser.export_excel_akser_oc, name='export_excel_akser_oc'),
    path('akser-mu/', akser.akta_sertifikat_mu_list_view, name='akser-mu-list'),
    path('akser-mu/export-excel/', akser.export_excel_akser_mu, name='export_excel_akser_mu'),
    
    path('invoice/reguler/', invoice.invoice_reguler_page, name='invoice-reguler'),
    path('api/invoice/generate/', invoice.GenerateInvoiceView.as_view(), name='generate-invoice'),
    path('api/invoice/download/', invoice.DownloadInvoiceView.as_view(), name='download-invoice'),
    path('api/invoice/data/', invoice.ConsumerInvoiceDataView.as_view(), name='invoice-data'),

    #🔹Billing (tagihan ke klien Mizuho)
    path('billing/', billing.billing_list, name='billing-list'),
    path('billing/export/', billing.billing_export, name='billing-export'),
    path('billing/upload/', billing.billing_upload, name='billing-upload'),
    path('billing/history/', billing.billing_history, name='billing-history'),
    path('billing/history/export/', billing.billing_history_export, name='billing-history-export'),
    
    path('invoice/overcredit/', invoiceoc.invoice_reguler_page_oc, name='invoice-overcredit'),
    path('api/invoice/generate-oc/', invoiceoc.GenerateInvoiceViewOC.as_view(), name='generate-invoice-oc'),
    path('api/invoice/download-oc/', invoiceoc.DownloadInvoiceViewOC.as_view(), name='download-invoice-oc'),
    path('api/invoice/data-oc/', invoiceoc.ConsumerInvoiceDataViewOC.as_view(), name='invoice-data-oc'),
    
    path('invoice/multiunit/', invoicemu.invoice_reguler_page_mu, name='invoice-multiunit'),
    path('api/invoice/generate-mu/', invoicemu.GenerateInvoiceViewMU.as_view(), name='generate-invoice-mu'),
    path('api/invoice/download-mu/', invoicemu.DownloadInvoiceViewMU.as_view(), name='download-invoice-mu'),
    path('api/invoice/data-mu/', invoicemu.ConsumerInvoiceDataViewMU.as_view(), name='invoice-data-mu'),
    
    path('finance-oc/', financeoc.finance_list_oc, name='finance-list-oc'),
    path('finance-reg/export-excel-oc/', financeoc.export_excel_finance_oc, name='export_excel_finance_oc'),
    path('finance-reg/upload-excel-oc/', financeoc.import_excel_finance_oc, name='upload_excel_finance_oc'),
    
    path('finance-mu/', financemu.finance_list_mu, name='finance-list-mu'),
    path('finance-reg/export-excel-mu/', financemu.export_excel_finance_mu, name='export_excel_finance_mu'),
    path('finance-reg/upload-excel-mu/', financemu.import_excel_finance_mu, name='upload_excel_finance_mu'),
    
    path('faq/', Admin.faq, name='faq'),
    path('contact/', Admin.contact, name='contact'),
    
    path('protected-media/<path:path>', dokument.protected_media_view, name='private_media'),
    path('media/<path:path>', dokument.protected_media_view, name='media'),
    path('media-mu/<path:path>', dokument.protected_media_view_mu, name='media-mu'),
    path('media-oc/<path:path>', dokument.protected_media_view_oc, name='media-oc'),
    path('media-pf/<path:path>', dokument.protected_media_view_pf, name='media-pf'),
]
