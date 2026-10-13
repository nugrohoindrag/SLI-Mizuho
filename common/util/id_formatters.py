from __future__ import annotations
from datetime import datetime, date
import random
from dateutil import parser
from typing import Tuple
from django.core.cache import cache
from django.db import connection
from num2words import num2words

from common.const.id_calendar import BULAN_ID

def generate_otp():
    return str(random.randint(100000, 999999))

def format_rupiah(value) -> str:
    try:
        value = str(value).replace(",", ".")
        value = int(float(value))
        return f"{value:,}".replace(",", ".")
    except (ValueError, TypeError):
        return str(value)

def tanggal_terbilang(dt: date | datetime | None) -> str:
    if not dt:
        return ""
    d = dt.day
    m = BULAN_ID.get(dt.month, "")
    y = dt.year
    hasil = f"{num2words(d, lang='id')} {m} {num2words(y, lang='id')}"
    return hasil.title()

def safe_split_birth_info(birth_info_raw: str | None) -> Tuple[str, str]:
    if isinstance(birth_info_raw, str) and ',' in birth_info_raw:
        tempat, tgl = birth_info_raw.split(',', 1)
        return tempat.strip(), tgl.strip()
    return "", ""

def terbilang_angka(nilai) -> str:
    if nilai is None:
        return ""
    try:
        nilai = str(nilai).replace(",", ".")
        nilai = int(float(nilai))
        return num2words(nilai, lang='id').capitalize() + " rupiah"
    except Exception:
        return str(nilai)
    
def terbilang_unit(nilai) -> str:
    if nilai is None:
        return ""
    try:
        nilai = int(float(nilai))
        return num2words(nilai, lang='id').capitalize()
    except Exception:
        return str(nilai)


def tanggal_indo(tgl_str: str) -> str:
    try:
        dt = parser.parse(tgl_str, dayfirst=True)
        return f"{dt.day} {BULAN_ID[dt.month]} {dt.year}"
    except Exception as e:
        print(f"Error parsing date: {e}")
        return ""

def terbilang_tanggal_lahir(tgl_str: str) -> str:
    try:
        dt = datetime.strptime(tgl_str, '%d-%m-%Y')
        bulan = BULAN_ID.get(dt.month, dt.strftime('%B'))
        hasil = f"{num2words(dt.day, lang='id')} {bulan} {num2words(dt.year, lang='id')}"
        return hasil.title()
    except Exception:
        return ""

def get_region_name_or_placeholder(province_id: int | None) -> str:
    if not province_id:
        return ""
    cache_key = f"region_name:{province_id}"
    name = cache.get(cache_key)
    if name is not None:
        return name

    with connection.cursor() as cursor:
        cursor.execute("SELECT region_name FROM regions WHERE region_code = %s", [str(province_id)])
        row = cursor.fetchone()

    name = row[0] if row else f"[province_id: {province_id}]"
    cache.set(cache_key, name, 3600)  # 1 jam
    return name

def get_nationality_full(nationality_code):
    mapping = {
        "WNI": "Warga Negara Indonesia",
        "WNA": "Warga Negara Asing"
    }
    return mapping.get(str(nationality_code).upper(), str(nationality_code))

def get_time_zone_full(nationality_code):
    mapping = {
        "WIB": "Waktu Indonesia Barat",
        "WITA": "Waktu Indonesia Tengah",
        "WIT": "Waktu Indonesia Timur",
    }
    return mapping.get(str(nationality_code).upper(), str(nationality_code))
