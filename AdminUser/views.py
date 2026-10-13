import logging, openpyxl, textwrap, pytz, traceback
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout
from django.db.models import Q, DateField, Count
from django.db.models.functions import Cast
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt

from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache, cache_page
from django.middleware.csrf import get_token, rotate_token
from django.views.decorators.vary import vary_on_headers

from rest_framework import status, filters
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import date, timedelta, datetime
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework.viewsets import ModelViewSet

from django.contrib.auth.signals import user_logged_in
from urllib3 import request

from assignaktaPF.models import LegalDocumentPF
from warkahDokumentMU.models import LegalDocumentMU
from warkahDokumentOC.models import LegalDocumentOC

user_logged_in.disconnect(dispatch_uid='update_last_login')

from AdminMenu.auth_helpers import user_has_permission, permission_required, class_permission_required, log_action_fbv, log_activity, log_action_cbv, get_user_role_names, get_user_permissions
from common.const.role_map import has_role, ROLE_KEYS
from common.util.id_formatters import generate_otp
from django.core.mail import send_mail
from django.conf import settings
from .models import User, Branch, Role, Company, Region, LoginHistory
from .branch_access import (
    branch_scope_q,
    filter_queryset_by_user_branches,
    get_primary_user_branch_code,
    get_user_branch_codes,
    user_is_branch_restricted,
)
from .authentication import UserSerializer, LoginSerializer, SelfUserUpdateSerializer, ChangePasswordSerializer, validate_password_strength
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password

from django.utils.timezone import now, is_naive

from import_cabang.models import Consumer
from import_cabang_mu.models import ConsumerMU
from import_cabang_oc.models import ConsumerOC
from change_fiduciary.models import change_of_fiduciary
from warkahDokument.models import LegalDocument
from rest_framework.pagination import PageNumberPagination
from openpyxl import Workbook

logger = logging.getLogger(__name__)

def _user_is_superadmin(user):
    role_names = list(get_user_role_names(user))
    return (
        has_role(role_names, ROLE_KEYS["SUPERADMIN"])
        or has_role(role_names, ROLE_KEYS["HO"])
        or has_role(role_names, ROLE_KEYS["OPERATOR"])
    )

def _user_is_staff_cabang(user):
    """Check if user has staff_cabang role"""
    return user_is_branch_restricted(user)

def _user_branch_code(user):
    return get_primary_user_branch_code(user)

def _user_branch_codes(user):
    return get_user_branch_codes(user)

def _requested_branch_scope(request):
    branch_code = (request.GET.get('branch') or '').strip()
    if not _user_is_staff_cabang(request.user):
        return [branch_code] if branch_code else []

    allowed_codes = _user_branch_codes(request.user)
    if not allowed_codes:
        return None
    if branch_code:
        return [branch_code] if branch_code in allowed_codes else None
    return allowed_codes

# 🔐 LOGIN API
@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    try:
        serializer = LoginSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            raw_password = request.data.get("password") or ""
            user = serializer.validated_data["user"]
            
            if serializer.is_valid():
                timezone.activate(pytz.timezone(settings.TIME_ZONE))

            force_password_change = False
            try:
                validate_password_strength(raw_password, user=user)
            except Exception:
                force_password_change = True

            request.session["force_password_change"] = force_password_change

            otp = generate_otp()
            user.otp_code = make_password(otp)
            user.otp_created_at = timezone.now()
            user.otp_attempts = 0
            user.save(update_fields=['otp_code', 'otp_created_at', 'otp_attempts'])

            message = textwrap.dedent(f"""\
            Halo User E-Fidusia,
            
            Berikut adalah kode OTP (One-Time Password) yang dapat digunakan untuk proses verifikasi login pada Web {settings.APP_BRAND_NAME}:
            
            Kode OTP Anda: {otp}

            Kode OTP ini berlaku selama 15 menit sejak email ini dikirimkan.
            
            Mohon untuk tidak membagikan kode OTP ini kepada siapa pun guna menjaga keamanan akun dan kerahasiaan data Anda pada Web E-Fidusia.

            Jika Anda tidak merasa melakukan permintaan login, silakan segera hubungi tim IT SLI.

            Terima kasih.

            Hormat kami,
            PT Sarana Legalitas Indonesia

            ---
            Thanks & Regards,
            PT Sarana Legalitas Indonesia

            Perhatian: E-mail ini (termasuk seluruh lampirannya, bila ada) hanya ditujukan kepada penerima yang tercantum di atas. Jika Anda bukan penerima <br> yang dituju, maka Anda tidak diperkenankan untuk memanfaatkan, menyebarkan, mendistribusikan, atau menggandakan e-mail ini beserta seluruh <br> lampirannya. Mohon kerjasamanya untuk segera memberitahukan PT Sarana Legalitas Indonesia di alamat email yang tercantum di atas serta menghapus <br> e-mail ini beserta seluruh lampirannya. Semua pendapat yang ada dalam e-mail ini merupakan pendapat pribadi dari pengirim yang bersangkutan dan <br> tidak serta merta mencerminkan pandangan PT Sarana Legalitas Indonesia, kecuali telah terdapat kesepakatan antara pengirim dan penerima bahwa e-<br>mail ini termasuk salah satu bentuk komunikasi kedinasan yang dapat diterima oleh kedua pihak.

            Caution: The information enclosed in this email (and any attachments) may be legally privileged and/or confidential and is intended only for the <br> use of the addressee(s). No addressee should forward, print, copy, or otherwise reproduce this message in any manner that would allow it to be <br> viewed by any individual not originally listed as a recipient. If the reader of this message is not the intended recipient, you are hereby <br> notified that any unauthorized disclosure, dissemination, distribution, copying or the taking of any action in reliance on the information herein <br> is strictly prohibited. If you have received this communication in error, please immediately notify the sender and delete this message. Unless it <br> is made by the authorized person, any views expressed in this message are those of the individual sender and may not necessarily reflect the views <br> of PT Sarana Legalitas Indonesia.
            """)
            
            html_message = f"""
            <html>
            <body style="margin:0;padding:0;background-color:#eef2f7;font-family:Arial,Helvetica,sans-serif;">

                <table align="center" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:40px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.08);">

                    <!-- HEADER -->
                    <tr>
                        <td style="background:linear-gradient(90deg,#1e3c72,#2a5298);padding:25px;text-align:center;color:#ffffff;">
                            <h2 style="margin:0;font-weight:600;">Verifikasi Login</h2>
                            <p style="margin:8px 0 0 0;font-size:14px;opacity:0.9;">
                                Web {settings.APP_BRAND_NAME}
                            </p>
                        </td>
                    </tr>

                    <!-- CONTENT -->
                    <tr>
                        <td style="padding:35px 30px;color:#333333;font-size:15px;line-height:1.6;">

                            <p style="margin-top:0;">
                                Halo <strong>User E-Fidusia</strong>,
                            </p>

                            <p>
                                Gunakan kode OTP berikut untuk menyelesaikan proses login Anda:
                            </p>

                            <!-- OTP BOX -->
                            <div style="text-align:center;margin:35px 0;">
                                <div style="
                                    display:inline-block;
                                    background:#f4f7fb;
                                    padding:20px 40px;
                                    border-radius:12px;
                                    border:2px dashed #2a5298;
                                    font-size:34px;
                                    letter-spacing:8px;
                                    font-weight:bold;
                                    color:#1e3c72;">
                                    {otp}
                                </div>
                            </div>

                            <p style="text-align:center;font-size:14px;color:#555;">
                                Berlaku selama <strong>15 menit</strong>
                            </p>

                            <hr style="margin:30px 0;border:none;border-top:1px solid #e5e5e5;">

                            <p style="font-size:14px;">
                                Demi keamanan akun Anda, jangan membagikan kode OTP ini kepada siapa pun.
                            </p>

                            <p style="font-size:14px;">
                                Jika Anda tidak merasa melakukan permintaan login, segera hubungi tim IT SLI.
                            </p>

                            <p style="margin-top:30px;">
                                Hormat kami,<br>
                                <strong>PT Sarana Legalitas Indonesia</strong>
                            </p>

                        </td>
                    </tr>

                    <!-- FOOTER -->
                    <tr>
                        <td style="background:#f8f9fb;padding:20px 25px;font-size:12px;color:#777;line-height:1.5;">
                            <strong>Perhatian:</strong> Email ini hanya ditujukan kepada penerima yang sah. 
                            Jika Anda bukan penerima yang dimaksud, harap segera menghapus email ini.

                            <br><br>

                            <strong>Caution:</strong> This email (including attachments) may contain confidential 
                            information intended only for the addressed recipient(s). Unauthorized use is prohibited.
                        </td>
                    </tr>

                </table>

            </body>
            </html>
            """

            send_mail(
                f'Kode OTP Verifikasi Login Web {settings.APP_BRAND_NAME}',
                message,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                html_message=html_message
            )

            request.session['otp_user_id'] = user.id

            return JsonResponse({
                "message": "OTP telah dikirim ke email Anda.",
                "user": UserSerializer(user).data,
                "need_otp": True
            })

        else:
            print("❌ Serializer not valid.")
            print("🧾 Serializer errors:", serializer.errors)

            errors = serializer.errors
            detail_msg = ""

            if "non_field_errors" in errors and isinstance(errors["non_field_errors"], list):
                detail_msg = errors["non_field_errors"][0]
            elif "detail" in errors:
                detail_msg = errors["detail"]
            else:
                detail_msg = "Periksa kembali Email/username dan Password Anda."

            return Response({"detail": detail_msg}, status=status.HTTP_401_UNAUTHORIZED)

    except Exception as e:
        print(traceback.format_exc())
        return Response({"detail": f"Internal server error: {str(e)}"}, status=500)
    
@api_view(["POST"])
@permission_classes([AllowAny])
def resend_otp(request):

    user_id = request.session.get("otp_user_id")

    if not user_id:
        return Response(
            {"detail": "Session OTP tidak ditemukan. Silakan login kembali."},
            status=400
        )

    try:
        user = User.objects.get(id=user_id)

        otp = generate_otp()

        user.otp_code = make_password(otp)
        user.otp_created_at = timezone.now()
        user.otp_attempts = 0
        user.save(update_fields=["otp_code", "otp_created_at", "otp_attempts"])


        message = textwrap.dedent(f"""\
            Halo User E-Fidusia,
            
            Berikut adalah kode OTP (One-Time Password) yang dapat digunakan untuk proses verifikasi login pada Web {settings.APP_BRAND_NAME}:
            
            Kode OTP Anda: {otp}

            Kode OTP ini berlaku selama 15 menit sejak email ini dikirimkan.
            
            Mohon untuk tidak membagikan kode OTP ini kepada siapa pun guna menjaga keamanan akun dan kerahasiaan data Anda pada Web E-Fidusia.

            Jika Anda tidak merasa melakukan permintaan login, silakan segera hubungi tim IT SLI.

            Terima kasih.

            Hormat kami,
            PT Sarana Legalitas Indonesia

            ---
            Thanks & Regards,
            PT Sarana Legalitas Indonesia

            Perhatian: E-mail ini (termasuk seluruh lampirannya, bila ada) hanya ditujukan kepada penerima yang tercantum di atas. Jika Anda bukan penerima <br> yang dituju, maka Anda tidak diperkenankan untuk memanfaatkan, menyebarkan, mendistribusikan, atau menggandakan e-mail ini beserta seluruh <br> lampirannya. Mohon kerjasamanya untuk segera memberitahukan PT Sarana Legalitas Indonesia di alamat email yang tercantum di atas serta menghapus <br> e-mail ini beserta seluruh lampirannya. Semua pendapat yang ada dalam e-mail ini merupakan pendapat pribadi dari pengirim yang bersangkutan dan <br> tidak serta merta mencerminkan pandangan PT Sarana Legalitas Indonesia, kecuali telah terdapat kesepakatan antara pengirim dan penerima bahwa e-<br>mail ini termasuk salah satu bentuk komunikasi kedinasan yang dapat diterima oleh kedua pihak.

            Caution: The information enclosed in this email (and any attachments) may be legally privileged and/or confidential and is intended only for the <br> use of the addressee(s). No addressee should forward, print, copy, or otherwise reproduce this message in any manner that would allow it to be <br> viewed by any individual not originally listed as a recipient. If the reader of this message is not the intended recipient, you are hereby <br> notified that any unauthorized disclosure, dissemination, distribution, copying or the taking of any action in reliance on the information herein <br> is strictly prohibited. If you have received this communication in error, please immediately notify the sender and delete this message. Unless it <br> is made by the authorized person, any views expressed in this message are those of the individual sender and may not necessarily reflect the views <br> of PT Sarana Legalitas Indonesia.
            """)

        html_message = f"""
            <html>
            <body style="margin:0;padding:0;background-color:#eef2f7;font-family:Arial,Helvetica,sans-serif;">

                <table align="center" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:40px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.08);">

                    <!-- HEADER -->
                    <tr>
                        <td style="background:linear-gradient(90deg,#1e3c72,#2a5298);padding:25px;text-align:center;color:#ffffff;">
                            <h2 style="margin:0;font-weight:600;">Verifikasi Login</h2>
                            <p style="margin:8px 0 0 0;font-size:14px;opacity:0.9;">
                                Web {settings.APP_BRAND_NAME}
                            </p>
                        </td>
                    </tr>

                    <!-- CONTENT -->
                    <tr>
                        <td style="padding:35px 30px;color:#333333;font-size:15px;line-height:1.6;">

                            <p style="margin-top:0;">
                                Halo <strong>User E-Fidusia</strong>,
                            </p>

                            <p>
                                Gunakan kode OTP berikut untuk menyelesaikan proses login Anda:
                            </p>

                            <!-- OTP BOX -->
                            <div style="text-align:center;margin:35px 0;">
                                <div style="
                                    display:inline-block;
                                    background:#f4f7fb;
                                    padding:20px 40px;
                                    border-radius:12px;
                                    border:2px dashed #2a5298;
                                    font-size:34px;
                                    letter-spacing:8px;
                                    font-weight:bold;
                                    color:#1e3c72;">
                                    {otp}
                                </div>
                            </div>

                            <p style="text-align:center;font-size:14px;color:#555;">
                                Berlaku selama <strong>15 menit</strong>
                            </p>

                            <hr style="margin:30px 0;border:none;border-top:1px solid #e5e5e5;">

                            <p style="font-size:14px;">
                                Demi keamanan akun Anda, jangan membagikan kode OTP ini kepada siapa pun.
                            </p>

                            <p style="font-size:14px;">
                                Jika Anda tidak merasa melakukan permintaan login, segera hubungi tim IT SLI.
                            </p>

                            <p style="margin-top:30px;">
                                Hormat kami,<br>
                                <strong>PT Sarana Legalitas Indonesia</strong>
                            </p>

                        </td>
                    </tr>

                    <!-- FOOTER -->
                    <tr>
                        <td style="background:#f8f9fb;padding:20px 25px;font-size:12px;color:#777;line-height:1.5;">
                            <strong>Perhatian:</strong> Email ini hanya ditujukan kepada penerima yang sah. 
                            Jika Anda bukan penerima yang dimaksud, harap segera menghapus email ini.

                            <br><br>

                            <strong>Caution:</strong> This email (including attachments) may contain confidential 
                            information intended only for the addressed recipient(s). Unauthorized use is prohibited.
                        </td>
                    </tr>

                </table>

            </body>
            </html>
            """

        send_mail(
            f"Kode OTP Verifikasi Login Web {settings.APP_BRAND_NAME}",
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            html_message=html_message
        )

        return Response({"message": "OTP baru telah dikirim."})

    except User.DoesNotExist:
        return Response({"detail": "User tidak ditemukan"}, status=404)
    
@api_view(["POST"])
@permission_classes([AllowAny])
def verify_otp(request):
    try:
        input_otp = request.data.get("otp")
        user_id = request.session.get("otp_user_id")

        if not user_id:
            return Response({"detail": "Session OTP tidak ditemukan."}, status=400)

        if not input_otp:
            return Response({"detail": "OTP wajib diisi."}, status=400)

        user = User.objects.get(id=user_id)

        if user.is_blocked:
            return Response({"detail": "Akun Anda sudah diblokir."}, status=403)

        if not user.otp_created_at:
            return Response({"detail": "OTP tidak valid."}, status=400)
        
        otp_time = user.otp_created_at

        if not otp_time:
            return Response({"detail": "OTP tidak valid."}, status=400)

        now = timezone.now()

        if now - user.otp_created_at > timedelta(minutes=15):
            return Response({"detail": "OTP sudah expired."}, status=400)

        if not check_password(input_otp, user.otp_code):

            user.otp_attempts += 1

            if user.otp_attempts >= 3:
                user.is_blocked = True
                user.save(update_fields=['otp_attempts', 'is_blocked'])
                return Response(
                    {"detail": "Akun diblokir karena 3x OTP salah."},
                    status=403
                )

            user.save(update_fields=['otp_attempts'])
            return Response({"detail": "OTP salah."}, status=400)

        # ===============================
        # OTP BENAR
        # ===============================

        user.otp_attempts = 0
        user.otp_code = None
        user.otp_created_at = None
        user.last_login = now

        user.save(update_fields=[
            'otp_attempts',
            'otp_code',
            'otp_created_at',
            'last_login'
        ])

        login(request, user, backend='AdminUser.authentication.EmailOrUsernameBackend')

        force_password_change = request.session.get("force_password_change", False)

        request.session.pop("otp_user_id", None)

        return JsonResponse({
            "message": "Login sukses!",
            "force_password_change": force_password_change,
            "redirect": "/profile/" if force_password_change else "/dashboard/",
        })

    except Exception:
        logger.exception("Terjadi error di verify_otp")
        return Response({"detail": "Internal server error"}, status=500)

# 🔁 REFRESH TOKEN
@api_view(["POST"])
@permission_classes([AllowAny])
def refresh_token_view(request):
    token = request.COOKIES.get("refresh_token")
    if not token:
        return Response({'error': 'Refresh token tidak ditemukan'}, status=400)
    try:
        refresh = RefreshToken(token)
        return Response({'access': str(refresh.access_token)})
    except TokenError:
        return Response({'error': 'Refresh token tidak valid'}, status=401)

@login_required(login_url="")
def view_user_profile_page(request):
    return render(request, "content/user-profile.html", {
        "user": request.user,
        "company": getattr(request.user, "company", None),
        "force_password_change": bool(request.session.get("force_password_change")),
    })

# 📍 API: List Province
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_provinces(request):
    try:
        provinces = Region.objects.filter(region_level='province').values("id", "region_name")
        return Response(list(provinces))
    except Exception as e:
        logger.error(f"❌ Error list_provinces: {str(e)}")
        return Response({"detail": "Internal Server Error"}, status=500)

# 🏢 API: List Company
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_companies(request):
    if not user_has_permission(request.user, 'read-companies'):
        return Response({'detail': 'Forbidden'}, status=403)
    try:
        companies = Company.objects.values("id", "company_name")
        return Response(list(companies))
    except Exception as e:
        logger.error(f"❌ Error list_companies: {str(e)}")
        return Response({"detail": "Internal Server Error"}, status=500)

# 👤 PROFILE
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_user_profile(request):
    logger.info(f"👤 Get user profile for: {request.user}")
    serializer = UserSerializer(request.user)
    return Response(serializer.data, status=status.HTTP_200_OK)

@api_view(["PUT", "PATCH"])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
def update_user_profile(request):
    user = request.user
    serializer = SelfUserUpdateSerializer(user, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        logger.info(f"✅ Profil diperbarui oleh: {user.email}")
        return Response(serializer.data, status=status.HTTP_200_OK)
    logger.warning(f"❌ Gagal update profil oleh: {user.email} | Errors: {serializer.errors}")
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
    if serializer.is_valid():
        serializer.save()
        request.session["force_password_change"] = False
        request.session.modified = True
        return Response({"message": "Password berhasil diperbarui."}, status=200)
    return Response(serializer.errors, status=400)

# 🚪 LOGOUT
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    refresh_token = request.COOKIES.get("refresh_token")
    if refresh_token:
        try:
            RefreshToken(refresh_token).blacklist()
        except Exception:
            pass

    logout(request)

    response = redirect("login")

    for cookie in [
        "refresh_token",
        "access_token",
        "csrftoken",
        "XSRF-TOKEN",
        "sidebar_menu_cache",
    ]:
        response.delete_cookie(cookie, path="/")

    return response
    # try:
    #     refresh_token = request.COOKIES.get("refresh_token")
    #     if refresh_token:
    #         try:
    #             token = RefreshToken(refresh_token)
    #             token.blacklist()
    #         except TokenError:
    #             pass

    #     request.session.flush()
    #     logout(request)

    #     response = redirect('login')
    #     response.delete_cookie("refresh_token")
    #     response.delete_cookie("access_token")
    #     response.delete_cookie("sessionid")
    #     response.delete_cookie("csrftoken")
    #     response.delete_cookie("XSRF-TOKEN")
    #     response.delete_cookie("sidebar_menu_cache")
    #     response.delete_cookie("taf_session")
    #     response.delete_cookie("cdn_sec_tc")
    #     response.delete_cookie("acw_tc")

    #     return response

    # except Exception as e:
    #     logger.error("Logout gagal: %s", str(e))
    #     return redirect('login')

# 🔁 GET CSRF TOKEN
@api_view(["GET"])
@permission_classes([AllowAny])
def get_csrf_token(request):
    csrf_token = get_token(request)
    response = Response({"csrf_token": csrf_token})
    response.set_cookie("csrftoken", csrf_token, secure=True, httponly=True, samesite='Lax')
    return response

TYPES = ("reguler", "oc", "mu", "pf")
ID_DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_graph(request):
    if not user_has_permission(request.user, 'read-dashboard'):
        return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    branch_codes = _requested_branch_scope(request)
  
    if branch_codes is None:
        return Response({'detail': 'Branch not set'}, status=status.HTTP_403_FORBIDDEN)

    today = date.today()
    start = today - timedelta(days=6)
    yesterday = today - timedelta(days=1)

    bar_labels = []
    bar_dates = []
    bar_series = {
        "reguler": [],
        "oc": [],
        "mu": [],
        "pf": []
    }

    rows_regueler = (
        Consumer.objects
        .annotate(day=Cast("upload_date", DateField()))
        .filter(day__range=(start, today))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )

    rows_oc = (
        ConsumerOC.objects
        .annotate(day=Cast("upload_date", DateField()))
        .filter(day__range=(start, today))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )

    rows_mu = (
        ConsumerMU.objects
        .annotate(day=Cast('upload_date', DateField()))
        .filter(day__range=(start, today))
        .exclude(agreement_number__isnull=True)
        .exclude(agreement_number__exact='')
        .values('day')
        .annotate(total=Count('agreement_number', distinct=True))  # unik per hari
        .order_by('day')
    )

    rows_change = (
        change_of_fiduciary.objects
        .annotate(day=Cast("created_at", DateField()))
        .filter(day__range=(start, today))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )

    if branch_codes:
        scope_q = branch_scope_q('branch__branch_code', branch_codes)
        rows_regueler = rows_regueler.filter(scope_q)
        rows_oc = rows_oc.filter(scope_q)
        rows_mu = rows_mu.filter(scope_q)
        rows_change = rows_change.filter(scope_q)
    
    user_roles = [r.lower() for r in get_user_role_names(request.user)]
    if ('notaris' in user_roles) or ('notary' in user_roles):
        rows_regueler = rows_regueler.filter(legal_documents__notary__user=request.user)
        rows_oc       = rows_oc.filter(legal_documents_oc__notary__user=request.user)
        rows_mu       = rows_mu.filter(legal_documents_mu__notary__user=request.user)
        rows_change   = rows_change.filter(legal_documents_pf__notary__user=request.user)

    counts_reguler = {r["day"]: r["total"] for r in rows_regueler}
    counts_oc = {r["day"]: r["total"] for r in rows_oc}
    counts_mu = {r["day"]: r["total"] for r in rows_mu}
    counts_change = {r["day"]: r["total"] for r in rows_change}

    series, cur = [], start
    while cur <= today:
        bar_labels.append(ID_DAYS[cur.weekday()])
        bar_dates.append(cur.strftime("%Y-%m-%d"))
        bar_series["reguler"].append(counts_reguler.get(cur, 0))
        bar_series["oc"].append(counts_oc.get(cur, 0))
        bar_series["mu"].append(counts_mu.get(cur, 0))
        bar_series["pf"].append(counts_change.get(cur, 0))

        cur += timedelta(days=1)

    return Response({
        "bar_labels": bar_labels,
        "bar_series": bar_series,
        "bar_dates": bar_dates,
    })

def _bucket():
    return {"total": 0, "details": {k: 0 for k in TYPES}}

def _count(model, filt):
    if isinstance(filt, Q):
        return model.objects.filter(filt).count()
    return model.objects.filter(**filt).count() if model.__name__ != 'ConsumerMU' else model.objects.filter(**filt).exclude(agreement_number__isnull=True).exclude(agreement_number='').aggregate(total=Count('agreement_number', distinct=True))['total'] or 0

@vary_on_headers('Accept')   # opsional, cegah cache salah konten
@cache_page(60)              # cache 60 detik
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    try:
        if not user_has_permission(request.user, 'read-dashboard'):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        branch_codes = _requested_branch_scope(request)
      
        if branch_codes is None:
            return Response({'detail': 'Branch not set'}, status=status.HTTP_403_FORBIDDEN)
        # registry model & filter per jenis dan status
        REGISTRY = {
            "reguler": {
                "uploaded": (Consumer, {}),
                "processed": (Consumer, {"st_process": "1"}),
                "done": (LegalDocument, {
                    "certificate_path__isnull": False,
                    "pnbp_path__isnull": False,
                }),
            },
            "oc": {
                "uploaded": (ConsumerOC, {}),
                "processed": (ConsumerOC, {"st_process": "1"}),
                "done": (LegalDocumentOC, {
                    "certificate_path__isnull": False,
                    "pnbp_path__isnull": False,
                }),
            },
            "mu": {
                "uploaded": (ConsumerMU, {}),
                "processed": (ConsumerMU, {"st_process": "1"}),
                "done": (LegalDocumentMU, {
                    "certificate_path__isnull": False,
                    "pnbp_path__isnull": False,
                }),
            },
            "pf": {
                # PF: created_at berupa datetime → pakai __date via Q
                "uploaded": (change_of_fiduciary, Q()),
                "processed": (change_of_fiduciary, Q(st_process="1")),
                "done": (LegalDocumentPF, {
                    "certificate_path__isnull": False,
                    "pnbp_path__isnull": False,
                }),
            },
        }

        # >>> NOTARIS PATCH: deteksi role & peta field relasi
        user_roles = [r.lower() for r in get_user_role_names(request.user)]
        is_notary = ('notaris' in user_roles) or ('notary' in user_roles)

        NOTARY_FIELD = {
            Consumer: 'legal_documents__notary__user',
            LegalDocument: 'notary__user',
            ConsumerOC: 'legal_documents_oc__notary__user',
            LegalDocumentOC: 'notary__user',
            ConsumerMU: 'legal_documents_mu__notary__user',
            LegalDocumentMU: 'notary__user',
            change_of_fiduciary: 'legal_documents_pf__notary__user',   # sesuaikan jika nama reverse beda
            LegalDocumentPF: 'notary__user',
        }

        def apply_notary_filter(model, filt):
            if not is_notary:
                return filt
            field = NOTARY_FIELD.get(model)
            if not field:
                return filt
            if isinstance(filt, dict):
                nf = dict(filt)
                nf[field] = request.user
                return nf
            if isinstance(filt, Q):
                return filt & Q(**{field: request.user})
            return filt
        # <<< NOTARIS PATCH

        uploaded = _bucket()
        processed = _bucket()
        done = _bucket()

        for kind, cfg in REGISTRY.items():
            m_u, f_u = cfg["uploaded"]
            m_p, f_p = cfg["processed"]
            m_d, f_d = cfg["done"]

            if branch_codes:
                if isinstance(f_u, dict):
                    f_u['branch__branch_code__in'] = branch_codes
                elif isinstance(f_u, Q):
                    f_u = f_u & branch_scope_q('branch__branch_code', branch_codes)

                if isinstance(f_p, dict):
                    f_p['branch__branch_code__in'] = branch_codes
                elif isinstance(f_p, Q):
                    f_p = f_p & branch_scope_q('branch__branch_code', branch_codes)

                fieldBranchCode = 'consumer__branch__branch_code'
                if isinstance(f_d, dict):
                    f_d[f'{fieldBranchCode}__in'] = branch_codes
                elif isinstance(f_d, Q):
                    f_d = f_d & branch_scope_q(fieldBranchCode, branch_codes)

            # >>> NOTARIS PATCH: terapkan pembatasan notaris
            f_u = apply_notary_filter(m_u, f_u)
            f_p = apply_notary_filter(m_p, f_p)
            f_d = apply_notary_filter(m_d, f_d)
            # <<< NOTARIS PATCH

            uploaded["details"][kind]  = _count(m_u, f_u)
            processed["details"][kind] = _count(m_p, f_p)
            done["details"][kind]      = _count(m_d, f_d)

        uploaded["total"]  = sum(uploaded["details"].values())
        processed["total"] = sum(processed["details"].values())
        done["total"]      = sum(done["details"].values())

        return Response({
            "percent_reguler": round(done["details"]["reguler"] / uploaded["details"]["reguler"] * 100, 2) if uploaded["details"]["reguler"] > 0 else 0,
            "percent_oc": round(done["details"]["oc"] / uploaded["details"]["oc"] * 100, 2) if uploaded["details"]["oc"] > 0 else 0,
            "percent_mu": round(done["details"]["mu"] / uploaded["details"]["mu"] * 100, 2) if uploaded["details"]["mu"] > 0 else 0,
            "percent_change": round(done["details"]["pf"] / uploaded["details"]["pf"] * 100, 2) if uploaded["details"]["pf"] > 0 else 0,
            "browser_labels": ['Reguler', 'Over Credit', 'Multi Unit', 'Perubahan'],
            "browser_series": [
                uploaded["details"]["reguler"],
                uploaded["details"]["oc"],
                uploaded["details"]["mu"],
                uploaded["details"]["pf"]
            ],
            'uploaded': uploaded,
            'processed': processed,
            'done': done,
        })
    except Exception as e:
        import traceback
        print("dashboard_stats error:", e)
        print(traceback.format_exc())
        return Response({"error": "Internal server error", "detail": str(e)}, status=500)

@never_cache
@require_GET
def view_login_page(request):
    """
    Menampilkan halaman login (HTML murni).
    Jika sudah login, langsung arahkan ke dashboard.
    """
    if request.user.is_authenticated:
        return redirect('/dashboard/')

    return render(request, "my_app/login.html")

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_kelengkapan(request):
    if not user_has_permission(request.user, 'read-dashboard'):
        return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    branch_codes = _requested_branch_scope(request)
    if branch_codes is None:
        return Response({'detail': 'Branch not set'}, status=status.HTTP_403_FORBIDDEN)

    qs = Consumer.objects.all()
    if branch_codes:
        qs = qs.filter(branch_scope_q('branch__branch_code', branch_codes))

    data = {
        'ktp': {
            'yes': qs.exclude(bpkb_ktp__isnull=True).exclude(bpkb_ktp='').count(),
            'no': qs.filter(bpkb_ktp__isnull=True).count() + qs.filter(bpkb_ktp='').count(),
        },
        'kk': {
            'yes': qs.exclude(company_npwp__isnull=True).exclude(company_npwp='').count(),  # misal untuk KK
            'no': qs.filter(company_npwp__isnull=True).count() + qs.filter(company_npwp='').count(),
        },
        'perjanjian': {
            'yes': qs.exclude(agreement_number__isnull=True).exclude(agreement_number='').count(),
            'no': qs.filter(agreement_number__isnull=True).count() + qs.filter(agreement_number='').count(),
        },
        'kuasa': {
            'yes': qs.exclude(deed_number__isnull=True).exclude(deed_number='').count(),
            'no': qs.filter(deed_number__isnull=True).count() + qs.filter(deed_number='').count(),
        },
    }
    return Response(data)

# 🏠 HALAMAN UTAMA
@login_required
def index(request):
    role = ''
    roles = request.user.roles.values_list('name', flat=True)
    
    user_permissions = list(get_user_permissions(request.user))
    user_roles = list(get_user_role_names(request.user))
    
    roles_lower = [r.lower() for r in user_roles]
    perms_lower = [p.lower() for p in user_permissions]
    is_notaris = 'notaris' in roles_lower
    if roles:
        role = roles[0]
    company = request.user.company

    login_history = LoginHistory.objects.filter(user=request.user).order_by('-created_at')[:5]

    return render(request, "content/index.html", {
        "user": request.user,
        "role": role,
        'user_permissions': perms_lower,
        'is_notaris': is_notaris,
        "company": company,
        "login_history": login_history,
    })

@permission_required('read-users')
def historyuser(request):
    return render(request, "content/modal/history_log_user.html", {"user": request.user})

@permission_required('read-users')
def login_history_api(request):
    logs = LoginHistory.objects.all().order_by('-created_at')

    data = []
    for log in logs:
        user = log.user
        user_status = 'Unknown'

        if user:
            user_status = 'Non Active' if user.count_last_login == 90 else 'Active'

        data.append({
            "username": log.user.username,
            "status": log.status,
            "ip_address": log.ip_address,
            "last_login": user_status,
            "created_at": timezone.localtime(log.created_at).strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": timezone.localtime(log.updated_at).strftime("%Y-%m-%d %H:%M:%S"),
        })

    return JsonResponse({"data": data})

# 🌐 ADMIN USER VIEWS
@permission_required('read-users')
def view_users_admin(request):
    context = {
        "user": request.user,
        "user_id": request.user.id
    }
    return render(request, "content/page-users-list.html", context)

@permission_required('create-users')
def view_create_users_admin(request):
    return render(request, "content/modal/page-users-create.html", {"user": request.user})

# 📦 API CRUD User
class UserPagination(PageNumberPagination):
    page_size = 20                          # default per halaman
    page_size_query_param = 'page_size'     # ?page_size=50
    max_page_size = 200

class UserViewSet(ModelViewSet):
    serializer_class = UserSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    permission_classes = [IsAuthenticated]
    pagination_class = UserPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'username', 'email']   # enable ?search=
    ordering_fields = ['id', 'name', 'email']       # enable ?ordering=name / -name
    ordering = ['id']                                # default ordering

    def get_queryset(self):
        user = self.request.user

        # Base queryset hemat query
        qs = (User.objects
              .select_related('branch')        # kalau ada FK branch
              .prefetch_related('roles', 'branch_links__branch')
              .all())

        if not user_has_permission(user, 'read-users'):
            return User.objects.none()

        if user.roles.filter(name__in=['superadministrator', 'administrator']).exists():
            return qs

        return qs.filter(id=user.id)

    def list(self, request, *args, **kwargs):
        if not user_has_permission(request.user, 'read-users'):
            return Response({'message': 'Forbidden'}, status=403)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        if not user_has_permission(request.user, 'read-users'):
            return Response({'message': 'Forbidden'}, status=403)
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if not user_has_permission(request.user, 'create-users'):
            return Response({'message': 'Forbidden'}, status=403)
        return super().create(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        logger.debug(f"📦 Payload PATCH: {request.data}")
        if not user_has_permission(request.user, 'update-users'):
            return Response({'message': 'Forbidden'}, status=403)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not user_has_permission(request.user, 'delete-users'):
            return Response({'message': 'Forbidden'}, status=403)
        return super().destroy(request, *args, **kwargs)

# 🌐 API List Role
class RoleListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @class_permission_required('view_role')
    def get(self, request):
        roles = Role.objects.values('id', 'name')
        return Response(list(roles))

# 🌐 API List Branch
class BranchListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        if _user_is_staff_cabang(user):
            codes = _user_branch_codes(user)
            if not codes:
                return Response([], status=200)
            qs = Branch.objects.filter(branch_code__in=codes).values('branch_code', 'branch_name')
        else:
            qs = Branch.objects.values('branch_code', 'branch_name')

        return Response(list(qs))

@api_view(['GET'])
@permission_classes([IsAuthenticated])
@permission_required('read-dashboard')
def dashboard_excel(request):
    """
    Download Excel file from Dashboard data with detailed information

    Query params:
    - start (Required): YYYY-MM-DD
    - end (Required): YYYY-MM-DD
    - branch (Optional): branch code
    - type (Required): one of 'reguler', 'mu', 'oc', 'pf'
    """
    # Get and validate query parameters
    start_date = request.GET.get('start')
    end_date = request.GET.get('end')
    branch_code = (request.GET.get('branch') or '').strip()
    branch_codes = _requested_branch_scope(request)
    data_type = request.GET.get('type')

    # Validate required parameters
    if not start_date or not end_date:
        return JsonResponse({
            'success': False,
            'message': 'Parameter start dan end harus diisi'
        })

    # Validate date format
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({
            'success': False,
            'message': 'Format tanggal tidak valid. Gunakan format YYYY-MM-DD'
        })

    # Validate date range
    if start > end:
        return JsonResponse({
            'success': False,
            'message': 'Tanggal awal tidak boleh lebih besar dari tanggal akhir'
        })

    # Validate type
    valid_types = ['reguler', 'mu', 'oc', 'pf']
    if not data_type or data_type not in valid_types:
        return JsonResponse({
            'success': False,
            'message': f'Parameter type harus salah satu dari: {", ".join(valid_types)}'
        })

    if branch_codes is None:
        return JsonResponse({
            'success': False,
            'message': 'Branch tidak ditemukan untuk user ini'
        }, status=403)

    # Validate branch if provided (superadmin only)
    if branch_code:
        branch_exists = Branch.objects.filter(branch_code=branch_code).exists()
        if not branch_exists:
            return JsonResponse({
                'success': False,
                'message': f'Branch dengan kode {branch_code} tidak ditemukan'
            })

    user_roles = [r.lower() for r in get_user_role_names(request.user)]
    is_notary = ('notaris' in user_roles) or ('notary' in user_roles)

    # Create workbook
    wb = Workbook()
    ws = wb.active
    ws.title = f"Dashboard {data_type.upper()}"

    # Add headers
    headers = [
        'Nama Cabang',
        'Tanggal Upload',
        'No Perjanjian',
        'Nama Debitur',
        'Jenis Nasabah',
        'Nama Notaris',
        'No Akta',
        'Tanggal Akta',
        'Kode Voucher',
        'No Sertifikat',
        'Tanggal Sertifikat',
        'Status Dokumen'
    ]

    for col_num, header in enumerate(headers, 1):
        ws.cell(row=1, column=col_num).value = header
        # Apply some styling to the header
        cell = ws.cell(row=1, column=col_num)
        cell.font = openpyxl.styles.Font(bold=True)

    # Get data based on type
    row_num = 2
    data_found = False

    if data_type == 'reguler':
        # Get consumers with their legal documents
        queryset = Consumer.objects.filter(upload_date__range=[start, end])
        if branch_codes:
            queryset = queryset.filter(branch_scope_q('branch__branch_code', branch_codes))
        if is_notary:
            queryset = queryset.filter(legal_documents__notary__user=request.user)

        # Use select_related to optimize queries
        queryset = queryset.select_related('branch')

        for item in queryset:
            # Get related legal document if exists
            legal_docs = LegalDocument.objects.filter(consumer=item).select_related('notary').first()

            ws.cell(row=row_num, column=1).value = item.branch.branch_name if item.branch else ''
            ws.cell(row=row_num, column=2).value = item.upload_date.strftime('%Y-%m-%d') if item.upload_date else ''
            ws.cell(row=row_num, column=3).value = item.agreement_number
            ws.cell(row=row_num, column=4).value = item.debtor_name
            ws.cell(row=row_num, column=5).value = item.customer_type

            # Legal document related fields
            if legal_docs:
                ws.cell(row=row_num, column=6).value = legal_docs.notary.firm_name if legal_docs.notary else ''
                ws.cell(row=row_num, column=7).value = legal_docs.document_number
                ws.cell(row=row_num, column=8).value = legal_docs.document_date.strftime('%Y-%m-%d') if legal_docs.document_date else ''
                ws.cell(row=row_num, column=9).value = legal_docs.voucher_code
                ws.cell(row=row_num, column=10).value = legal_docs.certificate_number
                ws.cell(row=row_num, column=11).value = legal_docs.certificate_date.strftime('%Y-%m-%d') if legal_docs.certificate_date else ''

            # Status document
            status_map = {
                'uncomplete': 'Belum Diproses',
                'complete': 'Selesai',
                'process': 'Dalam Proses',
                'revisi': 'Revisi'
            }
            raw = item.status
            label = 'Belum Diproses' if raw in (None, '', 0) else status_map.get(str(raw).strip().lower(), str(raw))
            ws.cell(row=row_num, column=12).value = label

            row_num += 1
            data_found = True

    elif data_type == 'mu':
        # Get consumers with their legal documents
        queryset = ConsumerMU.objects.filter(upload_date__range=[start, end])
        if branch_codes:
            queryset = queryset.filter(branch_scope_q('branch__branch_code', branch_codes))
        if is_notary:
            queryset = queryset.filter(legal_documents_mu__notary__user=request.user)

        # Use select_related to optimize queries
        queryset = queryset.select_related('branch')

        for item in queryset:
            # Get related legal document if exists
            from warkahDokumentMU.models import LegalDocumentMU
            legal_docs = LegalDocumentMU.objects.filter(consumer=item).select_related('notary').first()

            ws.cell(row=row_num, column=1).value = item.branch.branch_name if item.branch else ''
            ws.cell(row=row_num, column=2).value = item.upload_date.strftime('%Y-%m-%d') if item.upload_date else ''
            ws.cell(row=row_num, column=3).value = item.agreement_number
            ws.cell(row=row_num, column=4).value = item.debtor_name
            ws.cell(row=row_num, column=5).value = item.customer_type

            # Legal document related fields
            if legal_docs:
                ws.cell(row=row_num, column=6).value = legal_docs.notary.firm_name if legal_docs.notary else ''
                ws.cell(row=row_num, column=7).value = legal_docs.document_number
                ws.cell(row=row_num, column=8).value = legal_docs.document_date.strftime('%Y-%m-%d') if legal_docs.document_date else ''
                ws.cell(row=row_num, column=9).value = legal_docs.voucher_code
                ws.cell(row=row_num, column=10).value = legal_docs.certificate_number
                ws.cell(row=row_num, column=11).value = legal_docs.certificate_date.strftime('%Y-%m-%d') if legal_docs.certificate_date else ''

            # Status document
            status_map = {
                'uncomplete': 'Belum Diproses',
                'complete': 'Selesai',
                'process': 'Dalam Proses',
                'revisi': 'Revisi'
            }
            raw = item.status
            label = 'Belum Diproses' if raw in (None, '', 0) else status_map.get(str(raw).strip().lower(), str(raw))
            ws.cell(row=row_num, column=12).value = label

            row_num += 1
            data_found = True

    elif data_type == 'oc':
        # Get consumers with their legal documents
        queryset = ConsumerOC.objects.filter(upload_date__range=[start, end])
        if branch_codes:
            queryset = queryset.filter(branch_scope_q('branch__branch_code', branch_codes))
        if is_notary:
            queryset = queryset.filter(legal_documents_oc__notary__user=request.user)

        # Use select_related to optimize queries
        queryset = queryset.select_related('branch')

        for item in queryset:
            # Get related legal document if exists
            from warkahDokumentOC.models import LegalDocumentOC
            legal_docs = LegalDocumentOC.objects.filter(consumer=item).select_related('notary').first()

            ws.cell(row=row_num, column=1).value = item.branch.branch_name if item.branch else ''
            ws.cell(row=row_num, column=2).value = item.upload_date.strftime('%Y-%m-%d') if item.upload_date else ''
            ws.cell(row=row_num, column=3).value = item.agreement_number
            ws.cell(row=row_num, column=4).value = item.debtor_name
            ws.cell(row=row_num, column=5).value = item.customer_type

            # Legal document related fields
            if legal_docs:
                ws.cell(row=row_num, column=6).value = legal_docs.notary.firm_name if legal_docs.notary else ''
                ws.cell(row=row_num, column=7).value = legal_docs.document_number
                ws.cell(row=row_num, column=8).value = legal_docs.document_date.strftime('%Y-%m-%d') if legal_docs.document_date else ''
                ws.cell(row=row_num, column=9).value = legal_docs.voucher_code
                ws.cell(row=row_num, column=10).value = legal_docs.certificate_number
                ws.cell(row=row_num, column=11).value = legal_docs.certificate_date.strftime('%Y-%m-%d') if legal_docs.certificate_date else ''

            # Status document
            status_map = {
                'uncomplete': 'Belum Diproses',
                'complete': 'Selesai',
                'process': 'Dalam Proses',
                'revisi': 'Revisi'
            }
            raw = item.status
            label = 'Belum Diproses' if raw in (None, '', 0) else status_map.get(str(raw).strip().lower(), str(raw))
            ws.cell(row=row_num, column=12).value = label

            row_num += 1
            data_found = True

    elif data_type == 'pf':
        # Get change of fiduciary records
        queryset = change_of_fiduciary.objects.filter(created_at__date__range=[start, end])
        if branch_codes:
            queryset = queryset.filter(branch_scope_q('branch__branch_code', branch_codes))
        if is_notary:
            queryset = queryset.filter(legal_documents_pf__notary__user=request.user)

        # Use select_related to optimize queries
        queryset = queryset.select_related('branch')

        for item in queryset:
            # Get related legal document if exists
            from assignaktaPF.models import LegalDocumentPF
            legal_docs = LegalDocumentPF.objects.filter(consumer_id=item.id).select_related('notary').first()

            ws.cell(row=row_num, column=1).value = item.branch.branch_name if item.branch else ''
            ws.cell(row=row_num, column=2).value = item.created_at.strftime('%Y-%m-%d') if item.created_at else ''
            ws.cell(row=row_num, column=3).value = item.agreement_number
            ws.cell(row=row_num, column=4).value = item.debtor_name
            ws.cell(row=row_num, column=5).value = item.customer_type

            # Legal document related fields
            if legal_docs:
                ws.cell(row=row_num, column=6).value = legal_docs.notary.firm_name if legal_docs.notary else ''
                ws.cell(row=row_num, column=7).value = legal_docs.document_number
                ws.cell(row=row_num, column=8).value = legal_docs.document_date.strftime('%Y-%m-%d') if legal_docs.document_date else ''
                ws.cell(row=row_num, column=9).value = legal_docs.voucher_code
                ws.cell(row=row_num, column=10).value = legal_docs.certificate_number
                ws.cell(row=row_num, column=11).value = legal_docs.certificate_date.strftime('%Y-%m-%d') if legal_docs.certificate_date else ''

            # Status document
            status_map = {
                'uncomplete': 'Belum Diproses',
                'complete': 'Selesai',
                'process': 'Dalam Proses',
                'revisi': 'Revisi'
            }
            raw = item.status
            label = 'Belum Diproses' if raw in (None, '', 0) else status_map.get(str(raw).strip().lower(), str(raw))
            ws.cell(row=row_num, column=12).value = label

            row_num += 1
            data_found = True

    # Add a message if no data found
    if not data_found:
        ws.cell(row=2, column=1).value = "Tidak ada data untuk kriteria yang diberikan"
        for i in range(2, len(headers) + 1):
            ws.cell(row=2, column=i).value = ""

    # Auto-adjust column widths
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        adjusted_width = (max_length + 2)
        ws.column_dimensions[column].width = adjusted_width

    # Prepare response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    branch_label = branch_code or ("multi" if branch_codes else "all")
    filename = f"{data_type}_{branch_label}_{start_date}_{end_date}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Save workbook to response
    wb.save(response)
    return response
