import streamlit as st
from streamlit_option_menu import option_menu
import json
import os
import pandas as pd
from datetime import datetime, timedelta, time
import calendar
from streamlit_gsheets import GSheetsConnection
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import qrcode
from io import BytesIO

# --- SİSTEM URL AYARI (BUNU KENDİ YAYIN LİNKİNİZLE DEĞİŞTİRİN) ---
SISTEM_CANLI_LINKI = "https://helix-erp-sistemi-ezpfhhar8yk7apvyh3hkdm.streamlit.app/"

# --- SAYFA YAPILANDIRMASI ---
st.set_page_config(
    page_title="Helix ERP v3.0 (Live Sync & Saha Ops)",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- VERİ YÖNETİMİ (GOOGLE SHEETS + AKILLI BELLEK ADAPTÖRÜ) ---
KULLANICI_DOSYASI = "kullanicilar.json"
PERSONEL_DOSYASI = "personel.json"
PLAN_DOSYASI = "plan.json"
VARDIYA_DOSYASI = "vardiya.json"
PDKS_DOSYASI = "pdks.json"
IZIN_DOSYASI = "izin.json"
BAKIM_DOSYASI = "bakim.json"
EKIPMAN_DOSYASI = "ekipman.json"
IZIN_TALEP_DOSYASI = "izin_talepleri.json"

conn = st.connection("gsheets", type=GSheetsConnection)

TABLO_MAP = {
    "kullanicilar.json": "Kullanicilar",
    "personel.json": "Personel",
    "plan.json": "Plan",
    "vardiya.json": "Vardiya",
    "pdks.json": "PDKS",
    "izin.json": "Izin",
    "bakim.json": "Bakim",
    "ekipman.json": "Ekipman",
    "izin_talepleri.json": "Izin_Talepleri"
}

def unflatten_vardiya(df):
    vardiya_dict = {}
    if not df.empty and "Personel" in df.columns:
        for _, row in df.iterrows():
            p = str(row["Personel"])
            t = str(row["Tarih"])
            v = str(row["Vardiya"])
            if p and t and p != "nan" and t != "nan":
                if p not in vardiya_dict:
                    vardiya_dict[p] = {}
                vardiya_dict[p][t] = v
    return vardiya_dict

def flatten_vardiya(vardiya_dict):
    rows = []
    for p, dates in vardiya_dict.items():
        for t, v in dates.items():
            rows.append({"Personel": p, "Tarih": t, "Vardiya": v})
    return pd.DataFrame(rows)

def veri_yukle(dosya_adi, varsayilan):
    sheet_adi = TABLO_MAP.get(dosya_adi, dosya_adi)
    session_key = f"db_{sheet_adi}"

    if session_key in st.session_state:
        return st.session_state[session_key]

    try:
        df = conn.read(worksheet=sheet_adi, ttl="10m")
        df = df.dropna(how="all").fillna("")
        
        if sheet_adi == "Vardiya":
            sonuc = unflatten_vardiya(df)
        else:
            if df.empty:
                sonuc = varsayilan
            else:
                sonuc = df.to_dict(orient="records")
                
        st.session_state[session_key] = sonuc
        return sonuc
    except Exception as e:
        return varsayilan

def veri_kaydet(dosya_adi, veri):
    sheet_adi = TABLO_MAP.get(dosya_adi, dosya_adi)
    session_key = f"db_{sheet_adi}"

    st.session_state[session_key] = veri

    try:
        if sheet_adi == "Vardiya":
            df = flatten_vardiya(veri)
        else:
            df = pd.DataFrame(veri)
            
        if df.empty:
            df = pd.DataFrame(columns=["id"])
            
        conn.update(worksheet=sheet_adi, data=df)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"⚠️ Google Sheets Kayıt Hatası ({sheet_adi}): {e}")

def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return sourcedate.replace(year=year, month=month, day=day)

def mail_gonder_smtp(gonderen, sifre, alici_listesi, konu, df):
    try:
        msg = MIMEMultipart()
        msg['From'] = gonderen
        msg['To'] = alici_listesi
        msg['Subject'] = konu
        html_tablo = df.to_html(index=False, border=1)
        html_icerik = f"""
        <html>
        <head>
        <style>
            table {{border-collapse: collapse; width: 100%; font-family: Arial, sans-serif;}} 
            th, td {{padding: 10px; text-align: left; border: 1px solid #ddd;}} 
            th {{background-color: #2C3E50; color: white;}}
            tr:nth-child(even) {{background-color: #f2f2f2;}}
        </style>
        </head>
        <body>
            <h2 style="color: #2C3E50;">{konu}</h2>
            <p>Sistem üzerinden otomatik olarak oluşturulmuş tablo aşağıdadır:</p>
            {html_tablo}
        </body>
        </html>
        """
        msg.attach(MIMEText(html_icerik, 'html'))
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(gonderen, sifre)
        server.send_message(msg)
        server.quit()
        return True, ""
    except Exception as e:
        return False, str(e)

# --- SİSTEM SABİTLERİ ---
DEPARTMAN_BIRIMLERI = {
    "Yönetim": ["İnsan Kaynakları (İK)", "Muhasebe & Finans", "Bilgi İşlem (IT)", "İdari İşler & Resepsiyon", "Üst Yönetim / Genel Müdürlük"],
    "Üretim / Saha": ["Hat-1 Montaj", "Hat-2 Paketleme", "Kalıphane & Talaşlı İmalat", "Kalite Kontrol", "İş Sağlığı ve Güvenliği (İSG)"],
    "Depo / Lojistik": ["Mal Kabul", "Hammadde Deposu", "Mamul Ürün Deposu", "Sevkiyat & Lojistik"],
    "Satış / Pazarlama": ["Yurtiçi Satış", "Yurtdışı Satış (İhracat)", "Dijital Pazarlama & Kurumsal İletişim"],
    "Satın Alma": ["Tedarikçi Yönetimi", "Operasyonel Satın Alma"],
    "Teknik Destek / Bakım": ["Elektrik Bakım Onarım", "Mekanik Bakım Onarım", "Otomasyon & PLC", "Tesis / Enerji Yönetimi"]
}

EKIPMAN_KATEGORILERI = [
    "⚡ Elektrik & Enerji (Pano, Trafo, Sürücü, Jeneratör)",
    "❄️ Yardımcı Tesisler & İklimlendirme (Chiller, Pompa, Kompresör)",
    "⚙️ Üretim / Saha Makineleri (Motor, Redüktör, Bant)",
    "💻 Bilişim & Otomasyon (PLC, Bilgisayar, Switch, UPS)",
    "🏗️ Diğer / Genel Cihazlar"
]

KAN_GRUPLARI = ["A+", "A-", "B+", "B-", "AB+", "AB-", "0+", "0-"]
CALISMA_SEKILLERI = ["Tam Zamanlı (Kadrolu)", "Yarı Zamanlı (Part-Time)", "Sözleşmeli / Proje Bazlı", "Taşeron / Dış Kaynak"]
BAKIM_PERIYOTLARI = ["Günlük ⏱️", "Haftalık 📅", "Aylık 🗓️", "3 Aylık 📊", "6 Aylık ⏳", "Senelik (Yıllık) 🚀"]
BAKIM_TURLERI = ["Önleyici / Periyodik Bakım 🛡️", "Kestirimci Bakım (Termal/Vibrasyon vb.) 🔍", "Korektif / Arıza Bakımı 🛠️", "Büyük Revizyon / Modernizasyon 🏗️"]
BAKIM_DURUMLARI = ["Bekliyor 🟡", "Haftalık Plana Gönderildi 📅", "Devam Ediyor 🔵", "Tamamlandı 🟢", "İptal Edildi 🔴"]
EKIPMAN_DURUMLARI = ["Çalışıyor 🟢", "Bakımda / Revizyonda 🔵", "Arızalı (Üretim Durdu!) 🔴", "Hurda / Pasif ⚫"]

VARDİYALAR = [
    "Gündüz (Normal)", "Sabah Vardiyası (08:00 - 16:00)", "Akşam Vardiyası (16:00 - 00:00)",
    "Gece Vardiyası (00:00 - 08:00)", "Sabit (08:00 - 18:00)", "Mesaili Gündüz (08:00 - 20:00)",
    "Mesaili Gece (20:00 - 08:00)", "Özel Vardiya", "Haftalık İzin 🏖️", "Yıllık İzin ✈️",
    "Sağlık Raporu 🏥", "Ücretsiz İzin 🛑", "Mazeret İzni 📝"
]

VARDİYA_STANDART_BRUT = {
    "Gündüz (Normal)": 8.0, "Sabah Vardiyası (08:00 - 16:00)": 8.0, "Akşam Vardiyası (16:00 - 00:00)": 8.0,
    "Gece Vardiyası (00:00 - 08:00)": 8.0, "Sabit (08:00 - 18:00)": 10.0, "Mesaili Gündüz (08:00 - 20:00)": 8.0,
    "Mesaili Gece (20:00 - 08:00)": 8.0, "Özel Vardiya": 8.0, "Haftalık İzin 🏖️": 0.0, "Yıllık İzin ✈️": 0.0,
    "Sağlık Raporu 🏥": 0.0, "Ücretsiz İzin 🛑": 0.0, "Mazeret İzni 📝": 0.0
}

VARDİYA_SAATLERI = {
    "Gündüz (Normal)": {"bas": time(8,0), "bit": time(16,0)},
    "Sabah Vardiyası (08:00 - 16:00)": {"bas": time(8,0), "bit": time(16,0)},
    "Akşam Vardiyası (16:00 - 00:00)": {"bas": time(16,0), "bit": time(0,0)},
    "Gece Vardiyası (00:00 - 08:00)": {"bas": time(0,0), "bit": time(8,0)},
    "Sabit (08:00 - 18:00)": {"bas": time(8,0), "bit": time(18,0)},
    "Mesaili Gündüz (08:00 - 20:00)": {"bas": time(8,0), "bit": time(20,0)},
    "Mesaili Gece (20:00 - 08:00)": {"bas": time(20,0), "bit": time(8,0)}
}

# --- VERİLERİ YÜKLE ---
kullanici_listesi = veri_yukle(KULLANICI_DOSYASI, [])
if not kullanici_listesi:
    kullanici_listesi = [{"username": "admin", "sifre": "admin123", "rol": "Yönetici", "ad_soyad": "Sistem Yöneticisi"}]
    veri_kaydet(KULLANICI_DOSYASI, kullanici_listesi)

personel_listesi = veri_yukle(PERSONEL_DOSYASI, [])
haftalik_plan = veri_yukle(PLAN_DOSYASI, [])
if isinstance(haftalik_plan, dict): haftalik_plan = []
vardiya_programi = veri_yukle(VARDIYA_DOSYASI, {})
pdks_kayitlari = veri_yukle(PDKS_DOSYASI, [])
izin_kayitlari = veri_yukle(IZIN_DOSYASI, [])
bakim_planlari = veri_yukle(BAKIM_DOSYASI, [])
ekipman_listesi = veri_yukle(EKIPMAN_DOSYASI, [])
izin_talepleri = veri_yukle(IZIN_TALEP_DOSYASI, [])

for p in personel_listesi:
    p.setdefault("tc_no", "—"); p.setdefault("telefon", "—"); p.setdefault("eposta", "—")
    p.setdefault("dogum_tarihi", "1995-01-01"); p.setdefault("kan_grubu", "A+"); p.setdefault("ise_giris_tarihi", "2026-01-01")
    p.setdefault("departman", "Yönetim"); p.setdefault("birim", "Genel / Belirtilmedi"); p.setdefault("unvan", "—")
    p.setdefault("calisma_sekli", "Tam Zamanlı (Kadrolu)"); p.setdefault("saatlik_ucret", 150.0)
    p.setdefault("acil_yakini", "—"); p.setdefault("acil_telefon", "—"); p.setdefault("adres", "—"); p.setdefault("durum", "Aktif")

for e in ekipman_listesi:
    e.setdefault("kategori", "⚙️ Üretim / Saha Makineleri (Motor, Redüktör, Bant)")
    e.setdefault("marka", "—"); e.setdefault("model", "—"); e.setdefault("seri_no", "—")
    e.setdefault("imal_yili", "—"); e.setdefault("teknik_ozellikler", "—")
    e.setdefault("fiziksel_konum", "Fabrika Sahası"); e.setdefault("durum", "Çalışıyor 🟢")
    e.setdefault("proje_linki", "") # YENİ EKLENEN PROJE LİNKİ VARSAYILANI

for k in pdks_kayitlari:
    k.setdefault("gecikme_dk", 0)
    k.setdefault("erken_cikis_dk", 0)

# =====================================================================
# 🚨 MOBİL QR KOD SİCİL VE BAKIM EKRANI (LİNK DİNLEYİCİ)
# =====================================================================
qr_parametreleri = st.query_params
if "makine" in qr_parametreleri:
    hedef_makine_kodu = qr_parametreleri["makine"]
    st.markdown("<h2 style='text-align: center; color: #2C3E50;'>📱 Saha Operasyon & CMMS Merkezi</h2>", unsafe_allow_html=True)
    
    makine_obj = next((e for e in ekipman_listesi if str(e["kod"]) == hedef_makine_kodu), None)
    
    if not makine_obj:
        st.error(f"❌ HATA: Sistemde '{hedef_makine_kodu}' kodlu bir cihaz bulunamadı.")
        st.stop()
        
    st.markdown(f"### 🏭 {makine_obj['kod']} - {makine_obj.get('ad', '')}")
    st.info(f"**Kategori:** {makine_obj.get('kategori', '')} | **📍 Konum:** {makine_obj.get('fiziksel_konum', '')} | **🚥 Durum:** {makine_obj.get('durum', '')}")
    
    if makine_obj.get("proje_linki"):
        st.markdown(f"""
        <a href="{makine_obj['proje_linki']}" target="_blank">
            <button style="width:100%; padding:12px; background-color:#2980b9; color:white; border:none; border-radius:6px; font-weight:bold; font-size:16px; margin-bottom:15px; cursor:pointer;">
                📐 Elektrik Şeması / Projesini Görüntüle
            </button>
        </a>
        """, unsafe_allow_html=True)
        
    st.markdown("---")
    st.markdown("### 📋 Aktif Bakım Formu ve Kontrol Listesi")
    
    aktif_bakimlar = [b for b in bakim_planlari if str(b.get("ekipman_kod")) == hedef_makine_kodu and b.get("durum") in ["Bekliyor 🟡", "Haftalık Plana Gönderildi 📅", "Devam Ediyor 🔵"]]
    
    if aktif_bakimlar:
        for b in aktif_bakimlar:
            with st.expander(f"⚙️ {b.get('bakim_turu', '')} | Periyot: {b.get('periyot', 'Periyodik')}", expanded=True):
                with st.form(key=f"qr_bakim_form_{b['id']}"):
                    st.write("**📋 Yapılması Gereken Bakım Adımları (Check-List):**")
                    st.info(b.get("detaylar", "Bakım talimatı belirtilmemiş."))
                    
                    st.markdown("**⚡ Canlı Saha Ölçüm Girişleri**")
                    col_m1, col_m2, col_m3 = st.columns(3)
                    akim = col_m1.number_input("Çekilen Akım (Amper)", min_value=0.0, key=f"akim_{b['id']}")
                    gerilim = col_m2.number_input("Çalışma Gerilimi (Volt)", min_value=0.0, key=f"gerilim_{b['id']}")
                    sicaklik = col_m3.number_input("Pano/Motor Sıcaklığı (°C)", min_value=0.0, key=f"sicaklik_{b['id']}")
                    
                    saha_notu = st.text_area("Usta Saha Notları (Değişen parça, tespitler vb.)", key=f"not_{b['id']}")
                    kritik_sorun = st.checkbox("🚨 Tesis için tehlike arz eden KRİTİK BİR SORUN var!", key=f"kritik_{b['id']}")
                    
                    if st.form_submit_button("🔒 Bakımı Tamamla ve Verileri Kilitle", type="primary", use_container_width=True):
                        b["durum"] = "Tamamlandı 🟢"
                        b["gerceklesme_tarihi"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        b["olcumler"] = f"{akim}A / {gerilim}V / {sicaklik}°C"
                        b["saha_notu"] = saha_notu.strip()
                        b["kritik_uyari"] = "EVET" if kritik_sorun else "HAYIR"
                        
                        veri_kaydet(BAKIM_DOSYASI, bakim_planlari)
                        st.success("🎉 Bakım başarıyla tamamlandı ve yönetici paneline raporlandı!")
                        st.rerun()
    else:
        st.success("✅ Bu cihaz üzerinde şu an yapılması gereken aktif bir bakım görevi bulunmuyor.")
        
    st.markdown("---")
    st.markdown("#### 🕰️ Cihazın Geçmiş Bakım Sicili (Son 3 İşlem)")
    gecmis_bakimlar = [b for b in bakim_planlari if str(b.get("ekipman_kod")) == hedef_makine_kodu and b.get("durum") == "Tamamlandı 🟢"]
    if gecmis_bakimlar:
        gecmis_bakimlar = sorted(gecmis_bakimlar, key=lambda x: x.get("gerceklesme_tarihi", ""), reverse=True)
        for gb in gecmis_bakimlar[:3]:
            st.markdown(f"📅 **{gb.get('gerceklesme_tarihi', '—')}** | 👤 Sorumlu: {gb.get('sorumlu_personel', '')}")
            st.write(f"- *Ölçüm Değerleri:* `{gb.get('olcumler', '—')}`")
            st.write(f"- *Usta Notu:* {gb.get('saha_notu', 'Not yok.')}")
            if gb.get("kritik_uyari") == "EVET": st.error("🚨 Bu bakımda kritik sorun rapor edilmiş!")
            st.markdown("---")
    else:
        st.write("Cihaza ait geçmiş dijital kayıt bulunmuyor.")
    st.stop()
# =====================================================================

# --- LOGIN (GİRİŞ EKRANI) DUVARI ---
if "oturum_acildi" not in st.session_state:
    st.session_state.oturum_acildi = False
    st.session_state.aktif_kullanici = None
    st.session_state.aktif_rol = None
    st.session_state.aktif_ad_soyad = None

if not st.session_state.oturum_acildi:
    st.markdown("<br><br>", unsafe_allow_html=True)
    c_log1, c_log2, c_log3 = st.columns([1.5, 2, 1.5])
    with c_log2:
        st.markdown("<h2 style='text-align: center; color: #2C3E50;'>🏢 Helix ERP Giriş Paneli</h2>", unsafe_allow_html=True)
        with st.form("login_formu", clear_on_submit=False):
            input_user = st.text_input("Kullanıcı Adı veya T.C. Kimlik No")
            input_pass = st.text_input("Şifre", type="password")
            btn_login = st.form_submit_button("Güvenli Giriş Yap 🚀", use_container_width=True)
            
            if btn_login:
                kullanici_match = next((k for k in kullanici_listesi if str(k["username"]) == input_user.strip() and str(k["sifre"]) == input_pass.strip()), None)
                if kullanici_match:
                    st.session_state.oturum_acildi = True
                    st.session_state.aktif_kullanici = kullanici_match["username"]
                    st.session_state.aktif_rol = kullanici_match["rol"]
                    st.session_state.aktif_ad_soyad = kullanici_match["ad_soyad"]
                    st.success(f"Hoş geldiniz, {kullanici_match['ad_soyad']}!")
                    st.rerun()
                else:
                    st.error("❌ Hatalı Kullanıcı Adı veya Şifre! Lütfen bilgilerinizi kontrol edin.")
    st.stop()

# --- ROL BAZLI MENÜ YAPILANDIRMASI ---
current_role = st.session_state.aktif_rol

if current_role == "Yönetici":
    menu_options = ["Ana Sayfa", "Personel Özlük", "Bakım Planlama 🔧", "Haftalık İş Planı 📅", "Vardiya Yönetimi", "Giriş-Çıkış Takibi (PDKS)", "İzin Yönetimi", "Raporlar & Analiz"]
    menu_icons = ["house", "people", "wrench", "calendar-week", "clock-history", "box-arrow-in-right", "calendar-x", "graph-up-arrow"]
else:
    menu_options = ["Ana Sayfa", "Vardiya Dünyam 📋", "Tesis Bakım Planı 🔧", "PDKS Geçmişim ⏱️", "İzin İşlemlerim ✈️"]
    menu_icons = ["house", "calendar-date", "wrench", "alarm", "airplane"]

# --- SOL MENÜ ---
with st.sidebar:
    st.image("https://www.gstatic.com/images/branding/product/2x/avatar_anonymous_96x96dp.png", width=80)
    st.title("Helix ERP v2.27")
    st.write(f"👤 {st.session_state.aktif_ad_soyad}")
    st.write(f"🔑 Yetki Grubu: `{current_role}`")
    st.write("---")
    
    secilen_modul = option_menu(
        menu_title="Ana Menü",
        options=menu_options,
        icons=menu_icons,
        menu_icon="cast",
        default_index=0,
        styles={
            "container": {"padding": "5px!", "background-color": "#fafafa"},
            "icon": {"color": "#4A90E2", "font-size": "18px"}, 
            "nav-link": {"font-size": "14px", "text-align": "left", "margin":"0px", "--hover-color": "#eee"},
            "nav-link-selected": {"background-color": "#2C3E50"},
        }
    )
    
    if st.button("🔴 Güvenli Çıkış Yap", use_container_width=True):
        st.session_state.oturum_acildi = False
        st.rerun()

# --- MODÜLLER VE AKSİYONLAR ---

if secilen_modul == "Ana Sayfa":
    st.title(f"🏢 Helix ERP Stratejik Özet Paneli")
    st.write(f"Mevcut İş Günü: **{datetime.now().strftime('%d %B %Y')}**")
    
    if current_role == "Yönetici":
        # 🚨 YENİ EKLENEN KRİTİK SAHA BİLDİRİMİ
        kritik_bakimlar = [b for b in bakim_planlari if b.get("kritik_uyari") == "EVET" and b.get("durum") == "Tamamlandı 🟢"]
        if kritik_bakimlar:
            st.error("🚨 **DİKKAT: Sahadaki Ustalardan Kritik Arıza/Anomali Bildirimi Yapıldı!**")
            for kb in kritik_bakimlar:
                st.warning(f"⚠️ **Cihaz:** {kb.get('ekipman_ad')} ({kb.get('ekipman_kod')}) | **Usta:** {kb.get('sorumlu_personel')} | **Not:** {kb.get('saha_notu')}")
                
        col1, col2, col3, col4 = st.columns(4)
        aktif_personel = len([p for p in personel_listesi if p.get("durum") == "Aktif"])
        toplam_mesai = sum(float(k.get("fazla_mesai", 0)) for k in pdks_kayitlari)
        bugun_str = datetime.now().strftime("%Y-%m-%d")
        bugun_izinli = sum(1 for p in izin_kayitlari if p["baslangic"] <= bugun_str <= p["bitis"])
        aktif_bakimlar = len([b for b in bakim_planlari if "Tamamlandı" not in b.get("durum","")])

        col1.metric(label="Toplam Aktif Personel", value=f"{aktif_personel} Kişi")
        col2.metric(label="Sistemdeki Toplam Mesai", value=f"{toplam_mesai:.2f} Saat")
        col3.metric(label="Bugün İzinli / Raporlu", value=f"{bugun_izinli} Kişi")
        col4.metric(label="Bekleyen/Aktif Bakımlar", value=f"{aktif_bakimlar} İş")
    else:
        st.info(f"Hoş geldiniz, {st.session_state.aktif_ad_soyad}. Helix ERP personel portalı üzerinden günlük vardiya programınızı ve giriş-çıkış hareketlerinizi anlık olarak inceleyebilirsiniz.")

elif secilen_modul == "Personel Özlük" and current_role == "Yönetici":
    st.title("👥 Gelişmiş Personel Özlük ve İK Yönetimi")
    sekme_liste, sekme_detay, sekme_ekle = st.tabs([
        "📋 Personel Listesi ve Yönetim", "🔍 Detaylı 360° Personel Özlük Kartı", "➕ Yeni Personel Kartı / Giriş Hesabı Oluştur"
    ])
    
    with sekme_liste:
        if "edit_pers_target" not in st.session_state: st.session_state.edit_pers_target = None
        if "delete_pers_target" not in st.session_state: st.session_state.delete_pers_target = None

        if not personel_listesi: 
            st.info("Sisteme henüz personel kaydedilmemiş.")
        else:
            h1, h2, h3, h4, h5, h6 = st.columns([2.5, 2.5, 2, 1.5, 1, 1])
            h1.markdown("**Ad Soyad**")
            h2.markdown("**Departman & Birim**")
            h3.markdown("**Unvan / Rol**")
            h4.markdown("**Durum**")
            h5.markdown("**Düzenle**")
            h6.markdown("**Sil**")
            st.markdown("---")

            for idx, p in enumerate(personel_listesi):
                p_ad = p["ad_soyad"]
                if st.session_state.delete_pers_target == p_ad:
                    st.warning(f"⚠️ `{p_ad}` isimli personeli silmek istediğinize emin misiniz?")
                    c_evet, c_hayir = st.columns([1, 1])
                    if c_evet.button("✅ Evet, Sistemden Sil", key=f"del_yes_{idx}", type="primary", use_container_width=True):
                        personel_listesi.pop(idx); veri_kaydet(PERSONEL_DOSYASI, personel_listesi)
                        kullanici_listesi_local = [k for k in kullanici_listesi if k["ad_soyad"] != p_ad]
                        veri_kaydet(KULLANICI_DOSYASI, kullanici_listesi_local)
                        st.session_state.delete_pers_target = None; st.success(f"{p_ad} silindi!"); st.rerun()
                    if c_hayir.button("❌ İptal Et", key=f"del_no_{idx}", use_container_width=True): st.session_state.delete_pers_target = None; st.rerun()
                    continue
                
                r1, r2, r3, r4, r5, r6 = st.columns([2.5, 2.5, 2, 1.5, 1, 1])
                r1.write(f"**{p_ad}**")
                r2.write(f"{p.get('departman','')} - {p.get('birim','')}")
                r3.write(p.get('unvan',''))
                r4.write("🟢 Aktif" if p.get("durum") == "Aktif" else "🔴 Pasif")
                if r5.button("✏️", key=f"edit_btn_{idx}", use_container_width=True): st.session_state.edit_pers_target = p_ad; st.session_state.delete_pers_target = None; st.rerun()
                if r6.button("🗑️", key=f"del_btn_{idx}", use_container_width=True): st.session_state.delete_pers_target = p_ad; st.session_state.edit_pers_target = None; st.rerun()
                st.markdown("---")

            if st.session_state.edit_pers_target:
                target_p = st.session_state.edit_pers_target
                try:
                    p_idx = next(i for i, pp in enumerate(personel_listesi) if pp["ad_soyad"] == target_p)
                    p_edit = personel_listesi[p_idx]
                    
                    try: dt_dogum = datetime.strptime(p_edit["dogum_tarihi"], "%Y-%m-%d").date()
                    except: dt_dogum = datetime(1995, 1, 1).date()
                    try: dt_giris = datetime.strptime(p_edit["ise_giris_tarihi"], "%Y-%m-%d").date()
                    except: dt_giris = datetime.now().date()
                    
                    eski_dep = p_edit.get("departman", "Yönetim")
                    if eski_dep not in DEPARTMAN_BIRIMLERI: eski_dep = "Yönetim"
                    dep_index = list(DEPARTMAN_BIRIMLERI.keys()).index(eski_dep)
                    en_yeni_dep = st.selectbox("Departman * (Seçim birimleri değiştirir)", list(DEPARTMAN_BIRIMLERI.keys()), index=dep_index, key="edit_dep_box")
                    mevcut_birimler = DEPARTMAN_BIRIMLERI[en_yeni_dep]
                    eski_birim = p_edit.get("birim", "")
                    birim_index = mevcut_birimler.index(eski_birim) if eski_birim in mevcut_birimler else 0
                    en_yeni_birim = st.selectbox("Bağlı Birim *", mevcut_birimler, index=birim_index, key="edit_birim_box")
                    
                    with st.form("inline_edit_personel"):
                        col_e1, col_e2 = st.columns(2)
                        with col_e1:
                            en_yeni_ad = st.text_input("Ad Soyad *", value=p_edit["ad_soyad"])
                            en_yeni_tc = st.text_input("T.C. Kimlik Numarası", value=p_edit["tc_no"], max_chars=11)
                            en_yeni_dogum = st.date_input("Doğum Tarihi", value=dt_dogum)
                            en_yeni_kan = st.selectbox("Kan Grubu 🩸", KAN_GRUPLARI, index=KAN_GRUPLARI.index(p_edit["kan_grubu"]) if p_edit["kan_grubu"] in KAN_GRUPLARI else 0)
                            en_yeni_durum = st.selectbox("Çalışan Sistem Durumu", ["Aktif", "Pasif"], index=0 if p_edit.get("durum","Aktif") == "Aktif" else 1)
                            en_yeni_unvan = st.text_input("Unvan / Rol *", value=p_edit["unvan"])
                        with col_e2:
                            en_yeni_giris = st.date_input("İşe Giriş Tarihi", value=dt_giris)
                            en_yeni_sekil = st.selectbox("Çalışma Şekli", CALISMA_SEKILLERI, index=CALISMA_SEKILLERI.index(p_edit["calisma_sekli"]) if p_edit["calisma_sekli"] in CALISMA_SEKILLERI else 0)
                            en_yeni_ucret = st.number_input("Saatlik Baz Ücret (TL) *", min_value=0.0, value=float(p_edit["saatlik_ucret"]), step=5.0)
                            en_yeni_tel = st.text_input("Telefon Numarası", value=p_edit["telefon"])
                            en_yeni_eposta = st.text_input("E-posta Adresi", value=p_edit["eposta"])
                        
                        en_yeni_acil_ad = st.text_input("Acil Durum Yakını (Ad Soyad)", value=p_edit["acil_yakini"])
                        en_yeni_acil_tel = st.text_input("Acil Durum Yakını Telefonu", value=p_edit["acil_telefon"])
                        en_yeni_adres = st.text_area("İkametgah Adresi", value=p_edit["adres"])
                        
                        cb_col1, cb_col2 = st.columns(2)
                        if cb_col1.form_submit_button("🔄 Değişiklikleri Kaydet", type="primary", use_container_width=True):
                            if en_yeni_ad.strip() and en_yeni_unvan.strip():
                                eski_ad = p_edit["ad_soyad"]; yeni_ad = en_yeni_ad.strip()
                                personel_listesi[p_idx] = {
                                    "ad_soyad": yeni_ad, "tc_no": en_yeni_tc.strip() if en_yeni_tc else "—", 
                                    "telefon": en_yeni_tel.strip() if en_yeni_tel else "—", "eposta": en_yeni_eposta.strip() if en_yeni_eposta else "—", 
                                    "dogum_tarihi": en_yeni_dogum.strftime("%Y-%m-%d"), "kan_grubu": en_yeni_kan, 
                                    "ise_giris_tarihi": en_yeni_giris.strftime("%Y-%m-%d"), "departman": en_yeni_dep, "birim": en_yeni_birim, 
                                    "unvan": en_yeni_unvan.strip(), "calisma_sekli": en_yeni_sekil, "saatlik_ucret": round(en_yeni_ucret, 2), 
                                    "acil_yakini": en_yeni_acil_ad.strip() if en_yeni_acil_ad else "—", 
                                    "acil_telefon": en_yeni_acil_tel.strip() if en_yeni_acil_tel else "—", "adres": en_yeni_adres.strip() if en_yeni_adres else "—", 
                                    "durum": en_yeni_durum
                                }
                                veri_kaydet(PERSONEL_DOSYASI, personel_listesi)
                                
                                if yeni_ad != eski_ad:
                                    if eski_ad in vardiya_programi: vardiya_programi[yeni_ad] = vardiya_programi.pop(eski_ad)
                                    for k in pdks_kayitlari: 
                                        if k["personel"] == eski_ad: k["personel"] = yeni_ad
                                    for k in izin_kayitlari: 
                                        if k["personel"] == eski_ad: k["personel"] = yeni_ad
                                    veri_kaydet(VARDIYA_DOSYASI, vardiya_programi)
                                    veri_kaydet(PDKS_DOSYASI, pdks_kayitlari)
                                    veri_kaydet(IZIN_DOSYASI, izin_kayitlari)
                                
                                st.session_state.edit_pers_target = None
                                st.success("✔️ Bilgiler başarıyla güncellendi!"); st.rerun()
                        
                        if cb_col2.form_submit_button("❌ İptal Et", use_container_width=True):
                            st.session_state.edit_pers_target = None
                            st.rerun()
                except StopIteration:
                    st.session_state.edit_pers_target = None

    with sekme_detay:
        if not personel_listesi: 
            st.info("Profil kartı görüntülenecek personel bulunmuyor.")
        else:
            tüm_isimler = [p["ad_soyad"] for p in personel_listesi]
            secilen_profil = st.selectbox("360° Dijital Özlük Klasörünü İncelemek İstediğiniz Çalışanı Seçin", tüm_isimler, key="ozluk_360_p_box")
            p_kart = next(p for p in personel_listesi if p["ad_soyad"] == secilen_profil)
            
            p_pdks = [k for k in pdks_kayitlari if k["personel"] == secilen_profil]
            toplam_gecikme = sum(int(k.get("gecikme_dk", 0)) for k in p_pdks)
            toplam_mesai = sum(float(k.get("fazla_mesai", 0)) for k in p_pdks)
            
            aktif_bakimlar_p = [b for b in bakim_planlari if b.get("sorumlu_personel") == secilen_profil and "Tamamlandı" not in b.get("durum", "")]
            aktif_görevler_p = [g for g in haftalik_plan if g.get("sorumlu") == secilen_profil and g.get("durum") != "Tamamlandı"]
            
            bugun_str = datetime.now().strftime("%Y-%m-%d")
            yarin_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            bugun_vardiya = vardiya_programi.get(secilen_profil, {}).get(bugun_str, "Gündüz (Normal)")
            yarin_vardiya = vardiya_programi.get(secilen_profil, {}).get(yarin_str, "Gündüz (Normal)")
            
            st.write("<br>", unsafe_allow_html=True)
            
            col_prof1, col_prof2 = st.columns([1, 3])
            
            with col_prof1:
                st.markdown(f"""
                <div style="background-color:#f8f9fa; padding:25px; border-radius:12px; border-top: 6px solid #2C3E50; text-align:center; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                    <img src="https://www.gstatic.com/images/branding/product/2x/avatar_anonymous_96x96dp.png" style="width:110px; border-radius:50%; border: 3px solid #2C3E50; margin-bottom:12px;">
                    <h3 style="margin:0; color:#2C3E50; font-size:20px;">{p_kart['ad_soyad']}</h3>
                    <p style="margin:5px 0 15px 0; color:#7f8c8d; font-weight:500; font-size:14px;">{p_kart['unvan']}</p>
                    <span style="background-color:{'#2ecc71' if p_kart['durum']=='Aktif' else '#e74c3c'}; color:white; padding:5px 15px; border-radius:30px; font-size:12px; font-weight:bold;">
                        {'🟢 ÇALIŞAN AKTİF / SAHADA' if p_kart['durum']=='Aktif' else '🔴 PASİF DURUMDA'}
                    </span>
                </div>
                """, unsafe_allow_html=True)
                
                st.write("<br>", unsafe_allow_html=True)
                st.markdown("##### ⚡ Hızlı Yönetici Aksiyonları")
                
                with st.expander("➕ Direkt İş Emri / Görev Ata", expanded=False):
                    with st.form(f"quick_action_task_{secilen_profil}", clear_on_submit=True):
                        q_g_ad = st.text_input("Görev / Arıza Başlığı *")
                        q_g_tarih = st.date_input("Hedef Termin Tarihi", datetime.now())
                        q_g_detay = st.text_area("İş Emri Detay Tanımı")
                        if st.form_submit_button("⚡ Görevi Atama Havuzuna İşle", use_container_width=True):
                            if q_g_ad.strip():
                                haftalik_plan.append({"id": len(haftalik_plan)+1, "görev_adi": f"📋 {q_g_ad.strip()}", "ilgili_birim": p_kart["birim"], "sorumlu": secilen_profil, "hedef_tarih": q_g_tarih.strftime("%Y-%m-%d"), "detay": q_g_detay.strip(), "durum": "Başlamadı"})
                                veri_kaydet(PLAN_DOSYASI, haftalik_plan)
                                st.success(f"✔️ İş emri başarıyla {secilen_profil} üzerine atandı!"); st.rerun()
                                
                with st.expander("🏖️ Hızlı İzin / Rapor Tanımla", expanded=False):
                    with st.form(f"quick_action_leave_{secilen_profil}", clear_on_submit=True):
                        q_iz_tur = st.selectbox("İzin/Mazeret Türü", ["Yıllık Ücretli İzin ✈️", "Sağlık Raporu 🏥", "Mazeret İzni 📝", "Ücretsiz İzin 🛑"])
                        q_iz_bas = st.date_input("İzin Başlangıcı", datetime.now())
                        q_iz_bit = st.date_input("İzin Bitişi (Dahil)", datetime.now())
                        if st.form_submit_button("🔒 İzni Onayla ve Takvime Kilitle", use_container_width=True):
                            if q_iz_bit >= q_iz_bas:
                                gün_s = (q_iz_bit - q_iz_bas).days + 1
                                izin_kayitlari.append({"personel": secilen_profil, "tur": q_iz_tur, "baslangic": q_iz_bas.strftime("%Y-%m-%d"), "bitis": q_iz_bit.strftime("%Y-%m-%d"), "toplam_gun": gün_s})
                                if secilen_profil not in vardiya_programi: vardiya_programi[secilen_profil] = {}
                                v_etiket = "Yıllık İzin ✈️" if "Yıllık" in q_iz_tur else "Sağlık Raporu 🏥" if "Sağlık" in q_iz_tur else "Ücretsiz İzin 🛑" if "Ücretsiz" in q_iz_tur else "Mazeret İzni 📝"
                                for d in range(gün_s): vardiya_programi[secilen_profil][(q_iz_bas + timedelta(days=d)).strftime("%Y-%m-%d")] = v_etiket
                                veri_kaydet(IZIN_DOSYASI, izin_kayitlari); veri_kaydet(VARDIYA_DOSYASI, vardiya_programi)
                                st.success("🎉 İzin rezerve edildi ve takvime işlendi!"); st.rerun()

            with col_prof2:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric(label="💵 Saatlik Baz Ücret", value=f"{float(p_kart['saatlik_ucret']):.2f} TL")
                m2.metric(label="🔥 Bu Ayki Fazla Mesai", value=f"{toplam_mesai:.2f} Saat")
                m3.metric(label="🚨 Toplam Gecikme İhlali", value=f"{toplam_gecikme} Dakika", delta=f"{toplam_gecikme} dk" if toplam_gecikme > 0 else "0 dk", delta_color="inverse")
                m4.metric(label="📅 Bugünkü Vardiyası", value=bugun_vardiya)
                
                sub_tab1, sub_tab2, sub_tab3 = st.tabs(["🗂️ Temel Özlük Dosyası", "⏱️ Canlı PDKS Logları & Vardiya", "🔧 Üzerindeki Aktif CMMS / İş Emirleri"])
                
                with sub_tab1:
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        st.markdown("<h5 style='color:#2C3E50;'>🪪 Resmi Kimlik Bilgileri</h5>", unsafe_allow_html=True)
                        st.write(f"**T.C. Kimlik Numarası:** {p_kart['tc_no']}")
                        st.write(f"**Doğum Tarihi:** {p_kart['dogum_tarihi']}")
                        st.write(f"**Kan Grubu Parametresi:** ` {p_kart['kan_grubu']} `")
                        st.write(f"**Güncel İkametgah Adresi:** {p_kart['adres']}")
                    with cc2:
                        st.markdown("<h5 style='color:#2C3E50;'>💼 Kurumsal Pozisyon Verileri</h5>", unsafe_allow_html=True)
                        st.write(f"**Çalıştığı Departman:** {p_kart['departman']}")
                        st.write(f"**Bağlı Bulunduğu Birim/Saha:** `{p_kart['birim']}`")
                        st.write(f"**Resmi Sözleşme Türü:** {p_kart['calisma_sekli']}")
                        st.write(f"**Tesis Giriş / İşe Başlama Tarihi:** {p_kart['ise_giris_tarihi']}")
                    st.write("---")
                    st.error(f"🚨 **Acil Durum İletişim Yakını:** {p_kart.get('acil_yakini', '—')} (Tel: {p_kart.get('acil_telefon', '—')})")
                    
                with sub_tab2:
                    st.markdown("<h5 style='color:#2C3E50;'>📅 Kısa Vadeli Roster Çizelgesi</h5>", unsafe_allow_html=True)
                    st.write(f"**Bugünkü Vardiya Modeli ({bugun_str}):** `{bugun_vardiya}`")
                    st.write(f"**Yarından İtibaren Çalışma Modeli ({yarin_str}):** `{yarin_vardiya}`")
                    st.write("---")
                    st.markdown("<h5 style='color:#2C3E50;'>⏱️ Bulut Tabanlı Son PDKS Giriş-Çıkış Hareketleri</h5>", unsafe_allow_html=True)
                    if p_pdks:
                        df_p_pdks = pd.DataFrame(p_pdks)
                        st.dataframe(df_p_pdks[["tarih", "giris", "cikis", "gecikme_dk", "toplam_saat", "fazla_mesai"]], use_container_width=True, hide_index=True)
                    else:
                        st.info("Bu personele ait sisteme işlenmiş canlı PDKS mobil giriş-çıkış kaydı bulunamadı.")
                        
                with sub_tab3:
                    col_tasks1, col_tasks2 = st.columns(2)
                    with col_tasks1:
                        st.markdown("<h5 style='color:#2C3E50;'>🛠️ Atanmış Aktif Periyodik Bakım İşleri</h5>", unsafe_allow_html=True)
                        if aktif_bakimlar_p:
                            st.dataframe(pd.DataFrame(aktif_bakimlar_p)[["planlanan_tarih", "ekipman_ad", "bakim_turu", "durum"]], use_container_width=True, hide_index=True)
                        else:
                            st.info("Personelin üzerine zimmetli bekleyen aktif periyodik bakım görevi bulunmuyor.")
                    with col_tasks2:
                        st.markdown("<h5 style='color:#2C3E50;'>📅 Sorumlu Olduğu Aktif Genel Görevler / Arızalar</h5>", unsafe_allow_html=True)
                        if aktif_görevler_p:
                            st.dataframe(pd.DataFrame(aktif_görevler_p)[["hedef_tarih", "görev_adi", "durum"]], use_container_width=True, hide_index=True)
                        else:
                            st.info("Personelin üzerine atanmış bekleyen aktif genel iş emri bulunmuyor.")

    with sekme_ekle:
        st.subheader("➕ Yeni Personel Kayıt ve Portal Giriş Tanımlama Formu")
        secilen_dep = st.selectbox("Departman Seçimi *", list(DEPARTMAN_BIRIMLERI.keys()))
        secilen_birim = st.selectbox("Bağlı Olacağı Birim *", DEPARTMAN_BIRIMLERI[secilen_dep])
        with st.form("gelismis_personel_formu", clear_on_submit=True):
            col_f1, col_f2 = st.columns(2)
            with col_f1: 
                ad_soyad = st.text_input("Ad Soyad *")
                tc_no = st.text_input("T.C. Kimlik Numarası", max_chars=11)
                dogum_tarihi = st.date_input("Doğum Tarihi", datetime(1995, 1, 1))
                kan_grubu = st.selectbox("Kan Grubu 🩸", KAN_GRUPLARI)
                unvan = st.text_input("Unvan / Rol *")
            with col_f2: 
                ise_giris_tarihi = st.date_input("İşe Giriş Tarihi", datetime.now())
                calisma_sekli = st.selectbox("Çalışma Şekli", CALISMA_SEKILLERI)
                saatlik_ucret = st.number_input("Saatlik Baz Çalışma Ücreti (TL) *", min_value=0.0, value=150.0, step=5.0)
                telefon = st.text_input("Telefon Numarası")
                eposta = st.text_input("E-posta Adresi")
            acil_yakini = st.text_input("Acil Durumda Aranacak Kişi")
            acil_telefon = st.text_input("Acil Durum Yakını Telefonu")
            adres = st.text_area("İkametgah Adresi")
            
            st.markdown("---")
            st.markdown("**🔐 Personelin Mobil/Web Portal Giriş Bilgileri**")
            p_username = st.text_input("Portal Kullanıcı Adı (T.C. veya Örn: ahmet.yilmaz)")
            p_password = st.text_input("Portal Giriş Şifresi", type="password")
            p_role = st.selectbox("Uygulama Yetki Grubu", ["Personel", "Yönetici"])
            
            if st.form_submit_button("💾 Personel Özlük Dosyasını ve Giriş Hesabını Kaydet", type="primary", use_container_width=True):
                if ad_soyad.strip() and unvan.strip() and p_username.strip() and p_password.strip():
                    personel_listesi.append({"ad_soyad": ad_soyad.strip(), "tc_no": tc_no.strip() if tc_no else "—", "telefon": telefon.strip() if telefon else "—", "eposta": eposta.strip() if eposta else "—", "dogum_tarihi": dogum_tarihi.strftime("%Y-%m-%d"), "kan_grubu": kan_grubu, "ise_giris_tarihi": ise_giris_tarihi.strftime("%Y-%m-%d"), "departman": secilen_dep, "birim": secilen_birim, "unvan": unvan.strip(), "calisma_sekli": calisma_sekli, "saatlik_ucret": round(saatlik_ucret, 2), "acil_yakini": acil_yakini.strip() if acil_yakini else "—", "acil_telefon": acil_telefon.strip() if acil_telefon else "—", "adres": adres.strip() if adres else "—", "durum": "Aktif"})
                    veri_kaydet(PERSONEL_DOSYASI, personel_listesi)
                    kullanici_listesi.append({"username": p_username.strip(), "sifre": p_password.strip(), "rol": p_role, "ad_soyad": ad_soyad.strip()})
                    veri_kaydet(KULLANICI_DOSYASI, kullanici_listesi)
                    st.success("🎉 Personel ve Giriş Hesabı başarıyla buluta kilitlendi!"); st.rerun()

elif secilen_modul == "Bakım Planlama 🔧" and current_role == "Yönetici":
    st.title("🔧 Endüstriyel Planlı Bakım ve Envanter Yönetimi (CMMS)")
    sekme_envanter, sekme_bakim_paneli, sekme_bakim_tanimla = st.tabs([
        "🏭 1. Ekipman & Makine Envanteri", "📊 2. Yıllık / Dönemsel Bakım Takvimi", "➕ 3. Yeni Detaylı Bakım Planı Oluştur"
    ])
    
    with sekme_envanter:
        st.markdown("### 🏭 Tesis Makine ve Cihaz Envanteri Yönetimi")
        tab_env_yonetim, tab_env_ekle = st.tabs(["📋 Envanter Listesi ve Aksiyonlar", "➕ Yeni Ekipman Ekle (Dinamik Form)"])
        
        with tab_env_yonetim:
            if "edit_equip_target" not in st.session_state: st.session_state.edit_equip_target = None
            if "delete_equip_target" not in st.session_state: st.session_state.delete_equip_target = None
            
            if not ekipman_listesi:
                st.info("Envanterde kayıtlı makine/ekipman bulunmuyor.")
            else:
                h1, h2, h3, h4, h5, h6, h7 = st.columns([1.2, 2.0, 2.5, 2.0, 1.5, 1.0, 1.0])
                h1.markdown("**Kod/Barkod**"); h2.markdown("**Makine/Cihaz Adı**"); h3.markdown("**Kategori**")
                h4.markdown("**Konum**"); h5.markdown("**Anlık Durum**"); h6.markdown("**Düzenle**"); h7.markdown("**Sil**")
                st.markdown("---")
                
                for idx, e in enumerate(ekipman_listesi):
                    if st.session_state.delete_equip_target == e["kod"]:
                        st.warning(f"⚠️ `{e['kod']} - {e['ad']}` ekipmanını silmek istediğinize emin misiniz?")
                        c_evet, c_hayir = st.columns([1, 1])
                        if c_evet.button("✅ Evet, Eminim Sil", key=f"conf_yes_{e['kod']}_{idx}", type="primary", use_container_width=True):
                            ekipman_listesi.pop(idx); veri_kaydet(EKIPMAN_DOSYASI, ekipman_listesi); st.session_state.delete_equip_target = None; st.rerun()
                        if c_hayir.button("❌ Hayır, İptal Et", key=f"conf_no_{e['kod']}_{idx}", use_container_width=True): st.session_state.delete_equip_target = None; st.rerun()
                        continue
                        
                    r1, r2, r3, r4, r5, r6, r7 = st.columns([1.2, 2.0, 2.5, 2.0, 1.5, 1.0, 1.0])
                    r1.write(f"**{e['kod']}**"); r2.write(f"{e['ad']}")
                    r3.write(e.get("kategori", "—")); r4.write(e.get("fiziksel_konum", "—"))
                    r5.write(e.get("durum", "—"))
                    if r6.button("✏️", key=f"btn_edit_act_{e['kod']}_{idx}", use_container_width=True): st.session_state.edit_equip_target = e["kod"]; st.session_state.delete_equip_target = None; st.rerun()
                    if r7.button("🗑️", key=f"btn_del_act_{e['kod']}_{idx}", use_container_width=True): st.session_state.delete_equip_target = e["kod"]; st.session_state.edit_equip_target = None; st.rerun()
                    st.markdown("---")
                
                if st.session_state.edit_equip_target:
                    target_k = st.session_state.edit_equip_target; e_index = next(i for i, eq in enumerate(ekipman_listesi) if eq["kod"] == target_k); e_edit = ekipman_listesi[e_index]
                    with st.form("form_inline_edit_ekipman"):
                        yeni_kategori = st.selectbox("Ekipman Kategorisi *", EKIPMAN_KATEGORILERI, index=EKIPMAN_KATEGORILERI.index(e_edit.get("kategori")) if e_edit.get("kategori") in EKIPMAN_KATEGORILERI else 0)
                        st.markdown("---")
                        c_en1, c_en2 = st.columns(2)
                        yeni_kod = c_en1.text_input("Ekipman Kodu *", value=e_edit["kod"])
                        yeni_ad = c_en1.text_input("Ekipman Adı *", value=e_edit["ad"])
                        yeni_marka = c_en1.text_input("Marka", value=e_edit.get("marka",""))
                        yeni_model = c_en1.text_input("Model", value=e_edit.get("model",""))
                        yeni_seri = c_en2.text_input("Seri No", value=e_edit.get("seri_no",""))
                        yeni_imal = c_en2.text_input("İmal Yılı", value=e_edit.get("imal_yili",""))
                        yeni_durum = c_en2.selectbox("Anlık Durum *", EKIPMAN_DURUMLARI, index=EKIPMAN_DURUMLARI.index(e_edit["durum"]) if e_edit["durum"] in EKIPMAN_DURUMLARI else 0)
                        yeni_kritiklik = c_en2.select_slider("Kritiklik Derecesi", options=["Düşük", "Orta", "Yüksek", "KRİTİK 🚨"], value=e_edit.get("kritiklik", "Orta"))
                        st.markdown("---")
                        yeni_konum = st.text_input("📍 Sahadaki Tam Konumu / Bölgesi *", value=e_edit.get("fiziksel_konum",""))
                        yeni_proje_linki = st.text_input("🔗 Varsa Elektrik Projesi / Şema Linki", value=e_edit.get("proje_linki", ""))
                        yeni_teknik = st.text_area("Ekstra Teknik Özellikler ve Notlar", value=e_edit.get("teknik_ozellikler",""))
                        cb_col1, cb_col2 = st.columns(2)
                        if cb_col1.form_submit_button("🔄 Ekipmanı Güncelle", type="primary", use_container_width=True):
                            ekipman_listesi[e_index] = {
                                "kod": yeni_kod.strip().upper(), "ad": yeni_ad.strip(), 
                                "kategori": yeni_kategori,
                                "marka": yeni_marka.strip(), "model": yeni_model.strip(), 
                                "seri_no": yeni_seri.strip(), "imal_yili": yeni_imal.strip(), 
                                "fiziksel_konum": yeni_konum.strip(), "durum": yeni_durum, 
                                "kritiklik": yeni_kritiklik, "teknik_ozellikler": yeni_teknik.strip(),
                                "proje_linki": yeni_proje_linki.strip()
                            }
                            veri_kaydet(EKIPMAN_DOSYASI, ekipman_listesi); st.session_state.edit_equip_target = None; st.rerun()
                        if cb_col2.form_submit_button("❌ İptal Et", use_container_width=True): st.session_state.edit_equip_target = None; st.rerun()
            
            # YENİ EKLENEN QR ETİKET ÜRETİM MERKEZİ (Döngü Dışı)
            st.write("---")
            st.markdown("### 🖨️ Endüstriyel QR Kod Etiket Üretim Merkezi")
            if ekipman_listesi:
                qr_secilen = st.selectbox("Saha Etiketi Basılacak Ekipman veya Panoyu Seçin", [f"{e['kod']} - {e['ad']}" for e in ekipman_listesi], key="saha_qr_secim_kutusu")
                if qr_secilen:
                    hedef_kod = qr_secilen.split(" - ")[0].strip()
                    qr_url = f"{SISTEM_CANLI_LINKI}/?makine={hedef_kod}"
                    
                    qr = qrcode.QRCode(version=1, box_size=10, border=4)
                    qr.add_data(qr_url)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    
                    buf = BytesIO()
                    img.save(buf, format="PNG")
                    
                    col_qr_v1, col_qr_v2 = st.columns([1, 3])
                    col_qr_v1.image(buf.getvalue(), caption=f"Kod: {hedef_kod}", width=180)
                    col_qr_v2.info(f"👆 **{hedef_kod}** cihazı için dinamik karekod üretilmiştir. Bu resmi sağ tıklayıp bilgisayarınıza kaydederek yazıcıdan çıktı alabilir ve sahaya yapıştırabilirsiniz.")
        
        with tab_env_ekle:
            st.markdown("### 1. Ekipman Sınıfını Belirleyin")
            secilen_dinamik_kategori = st.selectbox("Ekipman Kategorisi *", EKIPMAN_KATEGORILERI, key="dinamik_kat_secim")
            
            st.markdown("### 2. Ekipman Detaylarını Girin")
            with st.form("yeni_ekipman_formu", clear_on_submit=True):
                c_en1, c_en2 = st.columns(2)
                ekipman_kodu = c_en1.text_input("Ekipman Kodu / Demirbaş No *")
                ekipman_adi = c_en1.text_input("Ekipman Adı *")
                marka = c_en1.text_input("Marka")
                model = c_en1.text_input("Model")
                
                seri_no = c_en2.text_input("Seri No")
                imal_yili = c_en2.text_input("İmal Yılı")
                ekipman_durum = c_en2.selectbox("Anlık Durum *", EKIPMAN_DURUMLARI)
                kritiklik = c_en2.select_slider("Kritiklik Derecesi", options=["Düşük", "Orta", "Yüksek", "KRİTİK 🚨"])
                
                st.markdown("---")
                fiziksel_konum = st.text_input("📍 Sahadaki Tam Konumu / Bölgesi *", placeholder="Örn: Ana Şalt Sahası, Hat-1 ACS880 Sürücü Panosu, D500-LITE Jeneratör Odası vb.")
                
                st.markdown(f"#### ⚙️ '{secilen_dinamik_kategori.split('(')[0].strip()}' Sınıfına Özel Parametreler")
                
                dinamik_metin = ""
                
                if "Elektrik" in secilen_dinamik_kategori:
                    d_guc = st.text_input("Çalışma Gücü (kW / kVA)")
                    d_gerilim = st.text_input("Gerilim (V)")
                    d_akim = st.text_input("Akım (A)")
                    d_ip = st.text_input("IP Koruma Sınıfı (Örn: IP65)")
                
                elif "İklimlendirme" in secilen_dinamik_kategori:
                    d_kapasite = st.text_input("Soğutma/Isıtma Kapasitesi (BTU / kW)")
                    d_gaz = st.text_input("Kullanılan Gaz Türü (Örn: R410A)")
                    d_basinc = st.text_input("Çalışma Basıncı (Bar)")
                
                elif "Üretim" in secilen_dinamik_kategori:
                    d_rpm = st.text_input("Motor Devri (RPM)")
                    d_rulman = st.text_input("Kullanılan Rulman Tipi / Kodu")
                
                elif "Bilişim" in secilen_dinamik_kategori:
                    d_ip_adres = st.text_input("Cihaz IP Adresi (Örn: 192.168.1.10)")
                    d_mac = st.text_input("Cihaz MAC Adresi")
                    d_os = st.text_input("İşletim Sistemi / Firmware Sürümü")
                
                genel_notlar = st.text_area("Genel Notlar ve Açıklamalar")
                proje_linki_input = st.text_input("🔗 Varsa Elektrik Projesi / Şema PDF Linki (Google Drive vb.)", placeholder="https://drive.google.com/...")
                
                if st.form_submit_button("💾 Dinamik Ekipmanı Envantere Kaydet", use_container_width=True, type="primary"):
                    if ekipman_kodu.strip() and ekipman_adi.strip() and fiziksel_konum.strip():
                        if "Elektrik" in secilen_dinamik_kategori:
                            dinamik_metin = f"[ELEKTRİK VERİLERİ] Güç: {d_guc} | Gerilim: {d_gerilim}V | Akım: {d_akim}A | Koruma: {d_ip}\n\n"
                        elif "İklimlendirme" in secilen_dinamik_kategori:
                            dinamik_metin = f"[İKLİMLENDİRME VERİLERİ] Kapasite: {d_kapasite} | Gaz Türü: {d_gaz} | Basınç: {d_basinc} Bar\n\n"
                        elif "Üretim" in secilen_dinamik_kategori:
                            dinamik_metin = f"[MEKANİK VERİLERİ] Devir: {d_rpm} RPM | Rulman: {d_rulman}\n\n"
                        elif "Bilişim" in secilen_dinamik_kategori:
                            dinamik_metin = f"[BİLİŞİM VERİLERİ] IP: {d_ip_adres} | MAC: {d_mac} | OS/Yazılım: {d_os}\n\n"
                            
                        nihai_teknik_ozellikler = dinamik_metin + "Genel Notlar:\n" + genel_notlar
                        
                        ekipman_listesi.append({
                            "kod": ekipman_kodu.strip().upper(), "ad": ekipman_adi.strip(), 
                            "kategori": secilen_dinamik_kategori,
                            "marka": marka.strip(), "model": model.strip(), 
                            "seri_no": seri_no.strip(), "imal_yili": imal_yili.strip(), 
                            "fiziksel_konum": fiziksel_konum.strip(), "durum": ekipman_durum, 
                            "kritiklik": kritiklik, "teknik_ozellikler": nihai_teknik_ozellikler.strip(),
                            "proje_linki": proje_linki_input.strip()
                        })
                        veri_kaydet(EKIPMAN_DOSYASI, ekipman_listesi)
                        st.success("🎉 Cihaza özel parametrelerle kaydedildi.")
                        st.rerun()
                    else:
                        st.error("Lütfen yıldızlı (*) olan zorunlu alanları doldurunuz.")

    with sekme_bakim_paneli:
        if not bakim_planlari: st.info("Planlı bakım bulunmuyor.")
        else:
            bakim_df = pd.DataFrame(bakim_planlari)
            edited_bakim_df = st.data_editor(bakim_df, hide_index=True, disabled=["id", "ekipman_kod", "ekipman_ad", "birim", "periyot", "bakim_turu", "detaylar", "sorumlu_personel", "planlanan_tarih"], column_config={"durum": st.column_config.SelectboxColumn("Mevcut Durum 🚨", options=BAKIM_DURUMLARI, width="medium")}, use_container_width=True, key="bakim_editor_grid_v2")
            if st.button("💾 Değişiklikleri Veritabanına Kaydet", type="secondary", use_container_width=True): veri_kaydet(BAKIM_DOSYASI, edited_bakim_df.to_dict(orient="records")); st.success("Durumlar güncellendi!"); st.rerun()
            
            st.write("---")
            with st.expander("📧 Bakım Planını E-Posta İle Gönder"):
                with st.form("mail_form_bakim"):
                    st.info("Not: Gmail kullanıyorsanız, Google Hesabınızdan 'Uygulama Şifresi' (App Password) oluşturmanız gerekir.")
                    m_gonderen = st.text_input("Gönderen E-Posta (Gmail)")
                    m_sifre = st.text_input("Uygulama Şifresi", type="password")
                    m_alici = st.text_input("Alıcı E-Posta Adresleri (Virgülle ayırın)")
                    if st.form_submit_button("E-Postayı Gönder 🚀", type="primary"):
                        if m_gonderen and m_sifre and m_alici:
                            basari, hata = mail_gonder_smtp(m_gonderen, m_sifre, m_alici, "Tesis Periyodik Bakım Planı", bakim_df)
                            if basari:
                                st.success("E-posta başarıyla gönderildi!")
                            else:
                                st.error(f"E-posta gönderilemedi: {hata}")
                        else:
                            st.warning("Lütfen tüm alanları doldurun.")
                            
            st.write("---")
            bakim_secenekleri = [f"{b['id']} - {b['ekipman_ad']} ({b['periyot']} - {b['planlanan_tarih']})" for b in bakim_planlari if b.get("durum") != "Haftalık Plana Gönderildi 📅"]
            if bakim_secenekleri:
                c_go1, c_go2 = st.columns([3, 1]); secilen_gonderim = c_go1.selectbox("Haftalık Plana Aktarılacak Bakım Görevi", bakim_secenekleri)
                if c_go2.button("🚀 Haftalık İş Planına Ekle", use_container_width=True, type="primary"):
                    b_id = int(secilen_gonderim.split(" - ")[0]); orijinal_bakim = next(b for b in bakim_planlari if b["id"] == b_id)
                    haftalik_plan.append({"id": len(haftalik_plan) + 1, "görev_adi": f"⚙️ BAKIM: {orijinal_bakim['ekipman_ad']} ({orijinal_bakim['bakim_turu']})", "ilgili_birim": orijinal_bakim.get("birim", "Teknik"), "sorumlu": orijinal_bakim["sorumlu_personel"], "hedef_tarih": orijinal_bakim["planlanan_tarih"], "detay": f"Frekans: {orijinal_bakim['periyot']}\nDetaylar:\n{orijinal_bakim['detaylar']}", "durum": "Başlamadı"})
                    veri_kaydet(PLAN_DOSYASI, haftalik_plan); orijinal_bakim["durum"] = "Haftalık Plana Gönderildi 📅"; veri_kaydet(BAKIM_DOSYASI, bakim_planlari); st.success("✔️ İş Emri Gönderildi!"); st.rerun()
            st.write("---")
            tab_tekli_sil, tab_toplu_sil = st.tabs(["1️⃣ Tek Bir Planı Sil", "2️⃣ Ekipmana Ait Tüm Bekleyenleri Sil"])
            with tab_tekli_sil:
                tum_plan_secenekleri = [f"{b['id']} - {b['ekipman_ad']} ({b['periyot']} - {b['planlanan_tarih']})" for b in bakim_planlari]
                if tum_plan_secenekleri:
                    c_del1, c_del2 = st.columns([3, 1]); secilen_sil = c_del1.selectbox("Sistemden Tamamen Silinecek Planı Seçin", tum_plan_secenekleri)
                    if c_del2.button("🔴 Seçili Planı Sil", use_container_width=True): bakim_planlari = [b for b in bakim_planlari if b["id"] != int(secilen_sil.split(" - ")[0])]; veri_kaydet(BAKIM_DOSYASI, bakim_planlari); st.success("✔️ Silindi!"); st.rerun()
            with tab_toplu_sil:
                ekipman_adlari_sil = list(set([b["ekipman_ad"] for b in bakim_planlari]))
                if ekipman_adlari_sil:
                    c_tdel1, c_tdel2 = st.columns([3, 1]); secilen_ek_sil = c_tdel1.selectbox("Toplu Silme Yapılacak Makineyi Seçin", ekipman_adlari_sil)
                    if c_tdel2.button("⚠️ Tüm Bekleyenleri Sil", use_container_width=True): bakim_planlari = [b for b in bakim_planlari if not (b["ekipman_ad"] == secilen_ek_sil and b["durum"] == "Bekliyor 🟡")]; veri_kaydet(BAKIM_DOSYASI, bakim_planlari); st.success("✔️ Temizlendi!"); st.rerun()

    with sekme_bakim_tanimla:
        st.markdown("### 📝 Detaylı Periyodik Bakım Planı Sihirbazı")
        if not ekipman_listesi: 
            st.warning("⚠️ Önce envantere ekipman kaydetmelisiniz!")
        else:
            col_bt1, col_bt2 = st.columns(2)
            ekipman_secenekler = [f"{e['kod']} - {e['ad']}" for e in ekipman_listesi]
            secilen_e_str = col_bt1.selectbox("Bakım Yapılacak Ekipman / Makine Seçin *", ekipman_secenekler)
            
            e_kod_ayrik = secilen_e_str.split(" - ")[0].strip()
            secilen_ekipman_obj = next((e for e in ekipman_listesi if str(e["kod"]).strip() == e_kod_ayrik), None)
            
            if secilen_ekipman_obj is None:
                st.error("🚨 HATA: Seçilen ekipmanın kodu veritabanındaki kayıtlarla eşleşmedi.")
            else:
                col_bt2.info(f"📂 **Kategori:** {secilen_ekipman_obj.get('kategori', 'Belirtilmedi')} \n📍 **Saha Lokasyonu:** {secilen_ekipman_obj.get('fiziksel_konum', 'Belirtilmedi')} | 🚨 **Kritiklik:** {secilen_ekipman_obj.get('kritiklik', 'Orta')}   \n🏭 **Marka/Model:** {secilen_ekipman_obj.get('marka', '—')} - {secilen_ekipman_obj.get('model', '—')} (Seri: {secilen_ekipman_obj.get('seri_no', '—')})   \n🚥 **Anlık Durum:** {secilen_ekipman_obj.get('durum', 'Çalışıyor 🟢')}")
                
                uygun_personeller = [p["ad_soyad"] for p in personel_listesi if p.get("durum") == "Aktif"]
                
                with st.form("detayli_bakim_formu_v2", clear_on_submit=True):
                    c_f1, c_f2 = st.columns(2)
                    bakim_turu = c_f1.selectbox("Bakım Türü *", BAKIM_TURLERI)
                    bakim_periyodu = c_f1.selectbox("Bakım Frekansı *", BAKIM_PERIYOTLARI)
                    sorumlu_p = c_f2.selectbox("Sorumlu Usta / Teknisyen *", uygun_personeller)
                    bakim_tarihi = c_f2.date_input("İlk Planlanan Bakım Tarihi", datetime.now())
                    bakim_detaylari = st.text_area("Detaylı Kontrol Listesi *")
                    st.markdown("---")
                    tum_yila_yay = st.checkbox("🔄 Bu Planı 1 Yıla Yay (Seçilen frekansa göre 1 yıllık iş emri üretir)", value=True)
                    
                    if st.form_submit_button("🔒 Bakım Planını Takvime İşle", type="primary", use_container_width=True):
                        if bakim_detaylari.strip():
                            p_obj = next((p for p in personel_listesi if p["ad_soyad"] == sorumlu_p), None)
                            s_birim = p_obj["birim"] if p_obj else "Teknik Saha"

                            olusturulacak_tarihler = []
                            if tum_yila_yay:
                                iter_date = bakim_tarihi; end_date = bakim_tarihi.replace(year=bakim_tarihi.year + 1)
                                while iter_date < end_date:
                                    olusturulacak_tarihler.append(iter_date)
                                    if "Günlük" in bakim_periyodu: iter_date += timedelta(days=1)
                                    elif "Haftalık" in bakim_periyodu: iter_date += timedelta(weeks=1)
                                    elif "Aylık" in bakim_periyodu: iter_date = add_months(iter_date, 1)
                                    elif "3 Aylık" in bakim_periyodu: iter_date = add_months(iter_date, 3)
                                    elif "6 Aylık" in bakim_periyodu: iter_date = add_months(iter_date, 6)
                                    elif "Senelik" in bakim_periyodu: iter_date = iter_date.replace(year=iter_date.year + 1)
                            else: olusturulacak_tarihler.append(bakim_tarihi)
                            
                            baslangic_id = len(bakim_planlari) + 1 if not bakim_planlari else max(b["id"] for b in bakim_planlari) + 1
                            
                            for i, t in enumerate(olusturulacak_tarihler): 
                                bakim_planlari.append({
                                    "id": baslangic_id + i, 
                                    "ekipman_kod": secilen_ekipman_obj["kod"], 
                                    "ekipman_ad": secilen_ekipman_obj["ad"], 
                                    "birim": s_birim, 
                                    "periyot": bakim_periyodu, 
                                    "bakim_turu": bakim_turu, 
                                    "detaylar": bakim_detaylari.strip(), 
                                    "sorumlu_personel": sorumlu_p, 
                                    "planlanan_tarih": t.strftime("%Y-%m-%d"), 
                                    "durum": "Bekliyor 🟡"
                                })
                            veri_kaydet(BAKIM_DOSYASI, bakim_planlari); st.success(f"🚀 Toplam {len(olusturulacak_tarihler)} adet bakım işi takvime eklendi!"); st.rerun()

elif secilen_modul == "Haftalık İş Planı 📅" and current_role == "Yönetici":
    st.title("📅 Haftalık İş Planı & Görev Dağıtım Paneli")
    sekme_g_liste, sekme_g_ekle = st.tabs(["📋 Aktif Görevler ve İş Emirleri", "➕ Genel Görev / Arıza Bildirimi Oluştur"])
    with sekme_g_liste:
        if not haftalik_plan: st.info("Aktif görev bulunmuyor.")
        else:
            plan_df = pd.DataFrame(haftalik_plan); edited_plan_df = st.data_editor(plan_df, hide_index=True, disabled=["id", "görev_adi", "ilgili_birim", "sorumlu", "hedef_tarih", "detay"], column_config={"durum": st.column_config.SelectboxColumn("İş Durumu", options=["Başlamadı", "Devam Ediyor", "Tamamlandı", "Engellendi 🛑"], width="medium")}, use_container_width=True, key="plan_editor_grid")
            if st.button("💾 Görev Durumlarını Güncelle", type="primary", use_container_width=True): veri_kaydet(PLAN_DOSYASI, edited_plan_df.to_dict(orient="records")); st.success("✔️ Güncellendi!"); st.rerun()
    with sekme_g_ekle:
        g_dep = st.selectbox("Görevlendirilecek Ana Departman", list(DEPARTMAN_BIRIMLERI.keys())); g_birim = st.selectbox("Görevlendirilecek Alt Birim", DEPARTMAN_BIRIMLERI[g_dep]); p_adaylari = [p["ad_soyad"] for p in personel_listesi if p.get("birim") == g_birim and p.get("durum") == "Aktif"]
        with st.form("manuel_görev_formu", clear_on_submit=True):
            g_ad = st.text_input("İş Emri / Görev Başlığı *"); g_sorumlu = st.selectbox("Sorumlu", p_adaylari if p_adaylari else ["Havuz"]); g_tarih = st.date_input("Termin", datetime.now()); g_detay = st.text_area("İş Tanımı")
            if st.form_submit_button("💼 Görevi Panoya Ekle", use_container_width=True):
                if g_ad.strip(): haftalik_plan.append({"id": len(haftalik_plan)+1, "görev_adi": g_ad.strip(), "ilgili_birim": g_birim, "sorumlu": g_sorumlu, "hedef_tarih": g_tarih.strftime("%Y-%m-%d"), "detay": g_detay.strip(), "durum": "Başlamadı"}); veri_kaydet(PLAN_DOSYASI, haftalik_plan); st.success("🎉 Görev atandı!"); st.rerun()

elif secilen_modul == "Vardiya Yönetimi" and current_role == "Yönetici":
    st.title("⏳ Günlük Vardiya Çizelgesi ve Roster Planlama")
    sekme_cizelge, sekme_vardiya_yaz = st.tabs(["📊 Toplu Vardiya Çizelgesi (Matrix)", "✍️ Gün Gün Vardiya Girişi"])
    with sekme_cizelge:
        col_m1, col_m2 = st.columns(2); matrix_baslangic = col_m1.date_input("Çizelge Başlangıç Tarihi", datetime.now(), key="m_bas"); gosterim_gunu = col_m2.selectbox("Görünüm Süresi", [7, 15, 30]); tarih_kolonlari = [(matrix_baslangic + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(gosterim_gunu)]; aktif_personeller = [p["ad_soyad"] for p in personel_listesi if p["durum"] == "Aktif"]
        if aktif_personeller:
            matrix_data = []
            for p_ad in aktif_personeller:
                personel_satiri = {"Personel Ad Soyad": p_ad}
                for t_str in tarih_kolonlari: personel_satiri[t_str] = vardiya_programi.get(p_ad, {}).get(t_str, "—")
                matrix_data.append(personel_satiri)
            st.dataframe(pd.DataFrame(matrix_data), use_container_width=True, hide_index=True)
            
            st.write("---")
            with st.expander("📧 Vardiya Çizelgesini E-Posta İle Gönder"):
                with st.form("mail_form_vardiya"):
                    st.info("Not: Gmail kullanıyorsanız, Google Hesabınızdan 'Uygulama Şifresi' (App Password) oluşturmanız gerekir.")
                    v_gonderen = st.text_input("Gönderen E-Posta (Gmail)")
                    v_sifre = st.text_input("Uygulama Şifresi", type="password")
                    v_alici = st.text_input("Alıcı E-Posta Adresleri (Virgülle ayırın)")
                    if st.form_submit_button("E-Postayı Gönder 🚀", type="primary"):
                        if v_gonderen and v_sifre and v_alici:
                            v_df = pd.DataFrame(matrix_data)
                            basari, hata = mail_gonder_smtp(v_gonderen, v_sifre, v_alici, f"Haftalık Vardiya Çizelgesi ({matrix_baslangic.strftime('%d.%m.%Y')})", v_df)
                            if basari:
                                st.success("Vardiya çizelgesi e-posta olarak gönderildi!")
                            else:
                                st.error(f"E-posta gönderilemedi: {hata}")
                        else:
                            st.warning("Lütfen tüm alanları doldurun.")
                            
    with sekme_vardiya_yaz:
        aktif_personeller = [p["ad_soyad"] for p in personel_listesi if p["durum"] == "Aktif"]
        if aktif_personeller:
            col_v1, col_v2 = st.columns(2); secilen_p = col_v1.selectbox("Personel", aktif_personeller); plan_baslangic = col_v1.date_input("Başlangıç Tarihi", datetime.now(), key="p_bas"); plan_bitis = col_v2.date_input("Bitiş Tarihi", datetime.now() + timedelta(days=6), key="p_bit")
            if plan_bitis >= plan_baslangic:
                gün_sayisi = (plan_bitis - plan_baslangic).days + 1; TURKCE_GUNLER = {"Monday": "Pazartesi", "Tuesday": "Salı", "Wednesday": "Çarşamba", "Thursday": "Perşembe", "Friday": "Cuma", "Saturday": "Cumartesi", "Sunday": "Pazar"}; gun_verileri = []
                for d in range(gün_sayisi):
                    guncel_tarih_dt = plan_baslangic + timedelta(days=d); guncel_tarih_str = guncel_tarih_dt.strftime("%Y-%m-%d")
                    gun_verileri.append({"Tarih": guncel_tarih_str, "Gün": TURKCE_GUNLER.get(guncel_tarih_dt.strftime("%A"), guncel_tarih_dt.strftime("%A")), "Atanacak Vardiya Modeli": vardiya_programi.get(secilen_p, {}).get(guncel_tarih_str, "Sabit (08:00 - 18:00)")})
                edited_df = st.data_editor(pd.DataFrame(gun_verileri), hide_index=True, disabled=["Tarih", "Gün"], column_config={"Atanacak Vardiya Modeli": st.column_config.SelectboxColumn("Vardiya Seçimi", options=VARDİYALAR, width="large")}, use_container_width=True, key=f"v_ed_{secilen_p}")
                if st.button(f"💾 {secilen_p} İçin Günlük Vardiyaları Kaydet", type="primary", use_container_width=True):
                    if secilen_p not in vardiya_programi: vardiya_programi[secilen_p] = {}
                    for idx, row in edited_df.iterrows(): vardiya_programi[secilen_p][row["Tarih"]] = row["Atanacak Vardiya Modeli"]
                    veri_kaydet(VARDIYA_DOSYASI, vardiya_programi); st.success("Vardiyalar kaydedildi!"); st.rerun()

elif secilen_modul == "Giriş-Çıkış Takibi (PDKS)" and current_role == "Yönetici":
    st.title("⏱️ PDKS Otomasyon & Mobil Sinyal Test Merkezi")
    sekme_pdks_rapor, sekme_pdks_ekle = st.tabs(["📋 Günlük Giriş-Çıkış & İhlal Raporu", "📡 Mobil Cihaz API Sinyal Simülatörü"])
    with sekme_pdks_rapor:
        if not pdks_kayitlari: st.info("Sistemde henüz işlenmiş otomatik mobil PDKS verisi bulunmuyor.")
        else:
            gosterim_listesi = []
            for k in pdks_kayitlari:
                gecikme_str = f"🔴 {k['gecikme_dk']} Dk Geç Giriş" if k.get('gecikme_dk', 0) > 0 else "🟢 Zamanında"
                erken_str = f"⚠️ {k['erken_cikis_dk']} Dk Erken Ayrılma" if k.get('erken_cikis_dk', 0) > 0 else "🟢 Normal Çıkış"
                gosterim_listesi.append({"Tarih": k["tarih"], "Personel": k["personel"], "Atanan Vardiya": k["vardiya"], "Uygulama Giriş (GPS)": k["giris"], "Uygulama Çıkış (GPS)": k["cikis"], "Gecikme Durumu": gecikme_str, "Erken Çıkış Durumu": erken_str, "Net Mesai Hakediş": f"🔥 {float(k['fazla_mesai']):.2f} Saat" if float(k['fazla_mesai']) > 0 else "0.00 Saat"})
            st.dataframe(pd.DataFrame(gosterim_listesi), use_container_width=True, hide_index=True)
    with sekme_pdks_ekle:
        st.markdown("### 📡 Mobil Uygulama Arka Plan Sinyal Entegrasyon Test Paneli")
        aktif_personeller = [p["ad_soyad"] for p in personel_listesi if p["durum"] == "Aktif"]
        if not aktif_personeller: st.warning("Sinyal simülasyonu yapabilmek için sistemde aktif personel olmalıdır.")
        else:
            col_tol1, col_tol2 = st.columns([2, 1])
            with col_tol1: tolerans_dk = st.slider("Kurumsal İşe Geç Kalma Toleransı (Dakika)", min_value=0, max_value=60, value=10, step=5)
            with st.form("pdks_mobil_simulasyon_formu", clear_on_submit=True):
                col_s1, col_s2 = st.columns(2)
                p_sec = col_s1.selectbox("Sinyal Tetikleyecek Mobil Cihaz Sahibi (Personel)", aktif_personeller)
                t_sec = col_s1.date_input("Sinyal Tarihi", datetime.now())
                st.markdown("**📱 Mobil GPS/Beacon Tarafından Gönderilen Ham Zaman Sinyalleri**")
                g_saat = col_s2.time_input("Uygulamanın Yakaladığı Giriş Sinyal Saati (Check-In)", time(8, 0), key="sim_in")
                c_saat = col_s2.time_input("Uygulamanın Yakaladığı Çıkış Sinyal Saati (Check-Out)", time(16, 0), key="sim_out")
                if st.form_submit_button("📡 Otomatik Mobil Giriş/Çıkış Sinyal Paketini Gönder", use_container_width=True, type="primary"):
                    t_str = t_sec.strftime("%Y-%m-%d"); atanan_v = vardiya_programi.get(p_sec, {}).get(t_str, "Gündüz (Normal)")
                    dt_g = datetime.combine(t_sec, g_saat); dt_c = datetime.combine(t_sec, c_saat)
                    if dt_c < dt_g: dt_c += timedelta(days=1)
                    toplam_s = (dt_c - dt_g).total_seconds() / 3600.0; zorunlu_s = VARDİYA_STANDART_BRUT.get(atanan_v, 8.0); fm = max(0.0, toplam_s - zorunlu_s)
                    gecikme_hesap = 0; erken_cikis_hesap = 0
                    if atanan_v in VARDİYA_SAATLERI:
                        of_bas = VARDİYA_SAATLERI[atanan_v]["bas"]; of_bit = VARDİYA_SAATLERI[atanan_v]["bit"]; dt_of_bas = datetime.combine(t_sec, of_bas); dt_of_bit = datetime.combine(t_sec, of_bit)
                        if dt_of_bit < dt_of_bas: dt_of_bit += timedelta(days=1)
                        if dt_g > dt_of_bas:
                            fark_dk = (dt_g - dt_of_bas).total_seconds() / 60.0
                            if fark_dk > tolerans_dk: gecikme_hesap = int(fark_dk)
                        if dt_c < dt_of_bit:
                            fark_dk = (dt_of_bit - dt_c).total_seconds() / 60.0
                            if fark_dk > 0: erken_cikis_hesap = int(fark_dk)
                    pdks_kayitlari.append({"personel": p_sec, "tarih": t_str, "vardiya": atanan_v, "giris": g_saat.strftime("%H:%M"), "cikis": c_saat.strftime("%H:%M"), "toplam_saat": round(toplam_s, 2), "fazla_mesai": round(fm, 2), "gecikme_dk": gecikme_hesap, "erken_cikis_dk": erken_cikis_hesap})
                    veri_kaydet(PDKS_DOSYASI, pdks_kayitlari); st.success(f"📡 API BAĞLANTISI BAŞARILI! Sinyal alındı. Personel o gün takvimde tanımlı olan `{atanan_v}` vardiyası üzerinden otomatik işlendi.")
                    if gecikme_hesap > 0: st.error(f"🚨 İHLAL LOGU: Cihaz sahibi tolerans sınırını aşarak {gecikme_hesap} dakika geç giriş yapmıştır!")
                    if erken_cikis_hesap > 0: st.warning(f"⚠️ İHLAL LOGU: Cihaz sahibi vardiya bitiş saatinden {erken_cikis_hesap} dakika erken ayrılmıştır!")
                    st.rerun()

# --- YENİ EKLENEN 30 SANİYELİK CANLI KAPSÜL MODÜLÜ ---
elif secilen_modul == "İzin Yönetimi" and current_role == "Yönetici":
    st.title("📴 Kurumsal İzin & Talep Yönetim Modülü")
    
    @st.fragment(run_every="30s")
    def izin_yonetimi_canli_paneli():
        st.cache_data.clear()
        if "db_Izin_Talepleri" in st.session_state: del st.session_state["db_Izin_Talepleri"]
        if "db_Izin" in st.session_state: del st.session_state["db_Izin"]
        if "db_Vardiya" in st.session_state: del st.session_state["db_Vardiya"]
        
        canli_talepler = veri_yukle(IZIN_TALEP_DOSYASI, [])
        canli_izinler = veri_yukle(IZIN_DOSYASI, [])
        canli_vardiya = veri_yukle(VARDIYA_DOSYASI, {})
        
        sekme_iz_liste, sekme_iz_talep, sekme_iz_yaz = st.tabs(["📋 Aktif İzin Takvimi", "🔔 Personel Talepleri", "➕ Manuel İzin / Rapor Gir"])
        
        with sekme_iz_liste:
            if not canli_izinler: st.info("Kayıtlı izin yok.")
            else: st.dataframe(pd.DataFrame(canli_izinler), use_container_width=True, hide_index=True)
            
        with sekme_iz_talep:
            bekleyenler = [t for t in canli_talepler if t.get("durum") == "Bekliyor 🟡"]
            if not bekleyenler:
                st.info("Şu an onay bekleyen yeni bir izin talebi bulunmuyor. (Ekran 30 saniyede bir canlı güncellenir)")
            else:
                for idx, t in enumerate(canli_talepler):
                    if t.get("durum") == "Bekliyor 🟡":
                        with st.container():
                            st.markdown(f"**👤 {t['personel']}** | 📝 **Tür:** {t['tur']} | 📅 **Tarih:** {t['baslangic']} ile {t['bitis']} arası ({t['toplam_gun']} Gün)")
                            col_t1, col_t2 = st.columns([1, 1])
                            if col_t1.button("✅ Onayla ve Takvime İşle", key=f"onay_{idx}", type="primary", use_container_width=True):
                                t["durum"] = "Onaylandı 🟢"
                                canli_izinler.append({"personel": t["personel"], "tur": t["tur"], "baslangic": t["baslangic"], "bitis": t["bitis"], "toplam_gun": t["toplam_gun"]})
                                if t["personel"] not in canli_vardiya: canli_vardiya[t["personel"]] = {}
                                v_etiket = "Yıllık İzin ✈️" if "Yıllık" in t["tur"] else "Sağlık Raporu 🏥" if "Sağlık" in t["tur"] else "Ücretsiz İzin 🛑" if "Ücretsiz" in t["tur"] else "Mazeret İzni 📝"
                                bas_dt = datetime.strptime(t["baslangic"], "%Y-%m-%d")
                                for d in range(int(t["toplam_gun"])): 
                                    canli_vardiya[t["personel"]][(bas_dt + timedelta(days=d)).strftime("%Y-%m-%d")] = v_etiket
                                    
                                veri_kaydet(IZIN_DOSYASI, canli_izinler)
                                veri_kaydet(VARDIYA_DOSYASI, canli_vardiya)
                                veri_kaydet(IZIN_TALEP_DOSYASI, canli_talepler)
                                st.success(f"✔️ {t['personel']} izni onaylandı ve vardiya takvimine otomatik işlendi!")
                                st.rerun()

                            if col_t2.button("❌ Reddet", key=f"red_{idx}", use_container_width=True):
                                t["durum"] = "Reddedildi 🔴"
                                veri_kaydet(IZIN_TALEP_DOSYASI, canli_talepler)
                                st.error("Talep reddedildi.")
                                st.rerun()
                            st.write("---")

            st.markdown("#### 📜 Geçmiş Talepler")
            gecmis = [t for t in canli_talepler if t.get("durum") != "Bekliyor 🟡"]
            if gecmis:
                st.dataframe(pd.DataFrame(gecmis)[["personel", "tur", "baslangic", "bitis", "durum"]], use_container_width=True, hide_index=True)

        with sekme_iz_yaz:
            aktif_personeller = [p["ad_soyad"] for p in personel_listesi if p["durum"] == "Aktif"]
            with st.form("izin_giris_formu", clear_on_submit=True):
                iz_p = st.selectbox("İzin Kullanan Personel", aktif_personeller); iz_tur = st.selectbox("İzin Türü", ["Yıllık Ücretli İzin ✈️", "Sağlık Raporu 🏥", "Mazeret İzni 📝", "Ücretsiz İzin 🛑"]); iz_bas = st.date_input("İzin Başlangıç Tarihi", datetime.now()); iz_bit = st.date_input("İzin Bitiş Tarihi (Dahil)", datetime.now())
                if st.form_submit_button("🔒 İzni Onayla ve Rezerve Et"):
                    if iz_bit >= iz_bas:
                        gün_s = (iz_bit - iz_bas).days + 1; canli_izinler.append({"personel": iz_p, "tur": iz_tur, "baslangic": iz_bas.strftime("%Y-%m-%d"), "bitis": iz_bit.strftime("%Y-%m-%d"), "toplam_gun": gün_s})
                        if iz_p not in canli_vardiya: canli_vardiya[iz_p] = {}
                        v_etiket = "Yıllık İzin ✈️" if "Yıllık" in iz_tur else "Sağlık Raporu 🏥" if "Sağlık" in iz_tur else "Ücretsiz İzin 🛑"
                        for d in range(gün_s): canli_vardiya[iz_p][(iz_bas + timedelta(days=d)).strftime("%Y-%m-%d")] = v_etiket
                        veri_kaydet(IZIN_DOSYASI, canli_izinler); veri_kaydet(VARDIYA_DOSYASI, canli_vardiya); st.success("🎉 İzin kaydedildi!"); st.rerun()
                        
    izin_yonetimi_canli_paneli()

elif secilen_modul == "Raporlar & Analiz" and current_role == "Yönetici":
    st.title("📊 Gelişmiş Finansal ve Operasyonel Raporlama")
    if not pdks_kayitlari: st.info("Rapor oluşturabilmek için PDKS modülünden veri girilmelidir.")
    else:
        df_pdks = pd.DataFrame(pdks_kayitlari)
        ucret_sozlugu = {p["ad_soyad"]: p.get("saatlik_ucret", 150.0) for p in personel_listesi}
        df_pdks["Mesaî Maliyeti (TL)"] = [round(float(row["fazla_mesai"]) * float(ucret_sozlugu.get(row["personel"], 150.0)) * 1.5, 2) for idx, row in df_pdks.iterrows()]
        col_r1, col_r2 = st.columns(2)
        with col_r1:
            st.subheader("👤 Personel Bazlı Mesai Yükü")
            mesai_ozet = df_pdks.groupby("personel").agg({"fazla_mesai": "sum", "Mesaî Maliyeti (TL)": "sum"}).reset_index()
            mesai_ozet.columns = ["Personel Adı", "Toplam Mesai (Saat)", "Ödenecek Ek Mesai Ücreti (TL)"]
            st.dataframe(mesai_ozet, use_container_width=True, hide_index=True)
        with col_r2:
            st.subheader("📊 Şirket Maliyet Dağılımı")
            st.bar_chart(data=mesai_ozet, x="Personel Adı", y="Ödenecek Ek Mesai Ücreti (TL)")

# --- PERSONEL PORTALI MODÜLLERİ ---

elif secilen_modul == "Tesis Bakım Planı 🔧" and current_role == "Personel":
    st.title("🔧 Tesis Aktif Bakım Planı")
    st.write("Tesisimizdeki tüm ekipmanların genel periyodik bakım takvimini aşağıdan inceleyebilirsiniz.")
    if not bakim_planlari:
        st.info("Sistemde planlı bakım bulunmuyor.")
    else:
        df_bakim_pers = pd.DataFrame(bakim_planlari)
        st.dataframe(df_bakim_pers[["id", "ekipman_kod", "ekipman_ad", "birim", "periyot", "bakim_turu", "sorumlu_personel", "planlanan_tarih", "durum"]], use_container_width=True, hide_index=True)

elif secilen_modul == "Vardiya Dünyam 📋" and current_role == "Personel":
    st.title("📋 Kişisel Vardiya ve Çalışma Programım")
    p_name = st.session_state.aktif_ad_soyad
    st.write(f"Sayın **{p_name}**, önümüzdeki 15 günlük resmi çalışma takviminiz aşağıda listelenmiştir:")
    p_bas = datetime.now(); gun_verileri = []; TURKCE_GUNLER = {"Monday": "Pazartesi", "Tuesday": "Salı", "Wednesday": "Çarşamba", "Thursday": "Perşembe", "Friday": "Cuma", "Saturday": "Cumartesi", "Sunday": "Pazar"}
    for d in range(15):
        g_date = p_bas + timedelta(days=d); g_str = g_date.strftime("%Y-%m-%d"); v_model = vardiya_programi.get(p_name, {}).get(g_str, "Gündüz (Normal) / Belirlenmedi")
        gun_verileri.append({"Tarih": g_str, "Gün": TURKCE_GUNLER.get(g_date.strftime("%A"), g_date.strftime("%A")), "Atanacak Vardiya / İzin Modeli": v_model})
    st.dataframe(pd.DataFrame(gun_verileri), use_container_width=True, hide_index=True)
    
    st.write("---")
    st.markdown("<h4 style='color:#2C3E50;'>👥 Tüm Ekibin Vardiya Çizelgesi</h4>", unsafe_allow_html=True)
    aktif_personeller_p = [p["ad_soyad"] for p in personel_listesi if p["durum"] == "Aktif"]
    if aktif_personeller_p:
        matrix_data_p = []
        tarih_kolonlari_p = [(p_bas + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(7)]
        for pr in aktif_personeller_p:
            personel_satiri_p = {"Personel Ad Soyad": pr}
            for t_str_p in tarih_kolonlari_p: 
                personel_satiri_p[t_str_p] = vardiya_programi.get(pr, {}).get(t_str_p, "—")
            matrix_data_p.append(personel_satiri_p)
        st.dataframe(pd.DataFrame(matrix_data_p), use_container_width=True, hide_index=True)

elif secilen_modul == "PDKS Geçmişim ⏱️" and current_role == "Personel":
    st.title("⏱️ Mobil Giriş-Çıkış (Geofencing) ve İhlal Hareketlerim")
    p_name = st.session_state.aktif_ad_soyad
    kisisel_pdks = [k for k in pdks_kayitlari if k["personel"] == p_name]
    if not kisisel_pdks: st.info("Sistemde henüz adınıza üretilmiş bir otomatik mobil giriş-çıkış kaydı bulunmuyor.")
    else:
        gosterim_listesi = []
        for k in kisisel_pdks:
            gecikme_str = f"🔴 {k['gecikme_dk']} Dk Geç Giriş" if k.get('gecikme_dk', 0) > 0 else "🟢 Zamanında"
            erken_str = f"⚠️ {k['erken_cikis_dk']} Dk Erken Ayrılma" if k.get('erken_cikis_dk', 0) > 0 else "🟢 Normal Çıkış"
            gosterim_listesi.append({"Tarih": k["tarih"], "Çalışılan Vardiya": k["vardiya"], "Mobil Check-In Sinyali": k["giris"], "Mobil Check-Out Sinyali": k["cikis"], "Gecikme Durumu": gecikme_str, "Erken Çıkış Durumu": erken_str, "Net Mesai Hakediş": f"🔥 {float(k['fazla_mesai']):.2f} Saat" if float(k['fazla_mesai']) > 0 else "0.00 Saat"})
        st.dataframe(pd.DataFrame(gosterim_listesi), use_container_width=True, hide_index=True)

# --- YENİ EKLENEN 30 SANİYELİK CANLI KAPSÜL MODÜLÜ (PERSONEL) ---
elif secilen_modul == "İzin İşlemlerim ✈️" and current_role == "Personel":
    st.title("✈️ İzin Talebi ve Durum Takibi")
    p_name = st.session_state.aktif_ad_soyad
    
    @st.fragment(run_every="30s")
    def personel_izin_canli_paneli():
        st.cache_data.clear()
        if "db_Izin_Talepleri" in st.session_state: del st.session_state["db_Izin_Talepleri"]
        
        canli_talepler = veri_yukle(IZIN_TALEP_DOSYASI, [])
        
        sekme_p_talep, sekme_p_gecmis = st.tabs(["➕ Yeni Talep Oluştur", "📜 Talep Geçmişim"])
        
        with sekme_p_talep:
            with st.form("personel_talep_formu", clear_on_submit=True):
                st.write("Yöneticinize iletilmek üzere yeni bir izin veya rapor talebi oluşturun:")
                t_iz_tur = st.selectbox("İzin / Rapor Türü", ["Yıllık Ücretli İzin ✈️", "Sağlık Raporu 🏥", "Mazeret İzni 📝", "Ücretsiz İzin 🛑"])
                t_iz_bas = st.date_input("Başlangıç Tarihi", datetime.now())
                t_iz_bit = st.date_input("Bitiş Tarihi (Dahil)", datetime.now())
                
                if st.form_submit_button("📤 Talebi Yöneticime Gönder", type="primary", use_container_width=True):
                    if t_iz_bit >= t_iz_bas:
                        gün_s = (t_iz_bit - t_iz_bas).days + 1
                        canli_talepler.append({
                            "id": len(canli_talepler) + 1,
                            "personel": p_name,
                            "tur": t_iz_tur,
                            "baslangic": t_iz_bas.strftime("%Y-%m-%d"),
                            "bitis": t_iz_bit.strftime("%Y-%m-%d"),
                            "toplam_gun": gün_s,
                            "durum": "Bekliyor 🟡",
                            "talep_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M")
                        })
                        veri_kaydet(IZIN_TALEP_DOSYASI, canli_talepler)
                        st.success("Talebiniz başarıyla yöneticinize iletildi. Onaylandığında vardiya takviminize otomatik yansıyacaktır.")
                        st.rerun()
                    else:
                        st.error("Bitiş tarihi başlangıç tarihinden önce olamaz!")
                        
        with sekme_p_gecmis:
            p_talepler = [t for t in canli_talepler if t["personel"] == p_name]
            if not p_talepler:
                st.info("Henüz oluşturduğunuz bir talep bulunmuyor. (Ekran 30 saniyede bir güncellenir)")
            else:
                st.dataframe(pd.DataFrame(p_talepler)[["talep_tarihi", "tur", "baslangic", "bitis", "toplam_gun", "durum"]], use_container_width=True, hide_index=True)
                
    personel_izin_canli_paneli()