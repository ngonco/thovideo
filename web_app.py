import streamlit as st
import pandas as pd
import requests
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import bcrypt
import time
import html  # <--- Thêm thư viện này để xử lý ký tự đặc biệt
import re  # <--- [MỚI] Thêm thư viện Regular Expression để xử lý văn bản mạnh mẽ
from supabase import create_client, Client
from streamlit_mic_recorder import mic_recorder
import extra_streamlit_components as stx # <--- Thư viện Cookie
import uuid # <--- Để tạo mã Token ngẫu nhiên
import struct # <--- [MỚI] Để xử lý file âm thanh WAV
import base64 # <--- [QUAN TRỌNG] Thêm dòng này để giải mã âm thanh

# --- THÊM ĐOẠN NÀY VÀO SAU CÁC DÒNG IMPORT ---
# Hàm này giúp kết nối Supabase và giữ kết nối không bị ngắt
# Dùng cache_resource cho KẾT NỐI (Database, ML models...)
@st.cache_resource
def init_supabase():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

# Khởi tạo kết nối ngay lập tức
supabase = init_supabase()

# --- [NEW] QUẢN LÝ COOKIE ---
# [ĐÃ SỬA] Bỏ @st.cache_resource vì CookieManager là Widget, không được cache
def get_cookie_manager():
    # Thêm key="cookie_manager" để định danh duy nhất, tránh reload lỗi
    return stx.CookieManager(key="cookie_manager")

cookie_manager = get_cookie_manager()

# --- [NEW] RATE LIMIT (CHỐNG SPAM) ---
def check_rate_limit(user_email):
    # Key lưu thời gian lần cuối request
    last_req_key = f"last_req_{user_email}"
    current_time = time.time()
    
    if last_req_key in st.session_state:
        # Nếu khoảng cách giữa 2 lần bấm < 5 giây -> Chặn
        if current_time - st.session_state[last_req_key] < 5:
            return False
    
    st.session_state[last_req_key] = current_time
    return True


# --- [NEW] HÀM XỬ LÝ TOKEN (AUTO LOGIN) ---

def update_session_token(user_id, token):
    try:
        supabase.table('users').update({"session_token": token}).eq('id', user_id).execute()
    except Exception as e:
        print(f"Lỗi update token: {e}")

def login_by_token():
    # Lấy token từ cookie
    token = cookie_manager.get(cookie="user_session_token")
    if token:
        try:
            # Tìm user có token này trong DB
            response = supabase.table('users').select("*").eq('session_token', token).execute()
            if response.data and len(response.data) > 0:
                user_data = response.data[0]
                # Xóa mật khẩu khỏi session vì lý do bảo mật
                if 'password' in user_data: del user_data['password']
                return user_data
        except Exception as e:
            print(f"Lỗi auto login: {e}")
    return None



# FILE: web_app.py (VERSION 7.2 - FULL SETTINGS RESTORED)

# --- [FIX] HÀM LÀM SẠCH DỮ LIỆU (BẢO MẬT) ---
def sanitize_input(text):
    if text is None: return ""
    text = str(text).strip()
    
    # 1. Ngăn chặn Formula Injection (Google Sheets)
    if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
         text = "'" + text
    
    # 2. Xóa các ký tự điều khiển nguy hiểm (Null bytes...)
    text = text.replace('\0', '')
    
    # 3. Mã hóa HTML (Chống XSS)
    return html.escape(text)

# --- [NEW] HÀM MẬT KHẨU AN TOÀN ---
def hash_password(plain_text_password):
    # Mã hóa mật khẩu
    return bcrypt.hashpw(plain_text_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_text_password, hashed_password):
    # Kiểm tra mật khẩu
    return bcrypt.checkpw(plain_text_password.encode('utf-8'), hashed_password.encode('utf-8'))



# --- [NEW] CẬP NHẬT QUOTA ---
def update_user_usage_supabase(user_id, current_used):
    try:
        supabase.table('users').update({"quota_used": current_used + 1}).eq('id', user_id).execute()
    except Exception as e:
        print(f"Lỗi update quota: {e}")

# --- [NEW] LƯU CÀI ĐẶT NGƯỜI DÙNG ---
def save_user_settings_supabase(user_id, settings_dict):
    try:
        supabase.table('users').update({"settings": settings_dict}).eq('id', user_id).execute()
        return True
    except Exception as e:
        st.error(f"Lỗi lưu cài đặt: {e}")
        return False

# --- [NEW] CÁC HÀM QUẢN LÝ USER & QUOTA ---
# --- [UPDATE] LOGIC ĐĂNG NHẬP CHUẨN SUPABASE (ĐÃ XÓA BACKDOOR) ---
def check_login(email, password):
    try:
        # 1. Tìm user trong Supabase (Bảng 'users')
        response = supabase.table('users').select("*").eq('email', email).execute()
        
        if response.data and len(response.data) > 0:
            user_data = response.data[0]
            stored_hash = user_data['password']
            
            # 2. Kiểm tra mật khẩu (Dùng bcrypt để so sánh password nhập vào và hash trong DB)
            if verify_password(password, stored_hash):
                # [BẢO MẬT] Xóa mật khẩu khỏi dữ liệu trước khi lưu vào session
                if 'password' in user_data:
                    del user_data['password']

                # Đảm bảo các trường số liệu không bị None để tránh lỗi cộng trừ sau này
                if user_data.get('quota_used') is None: user_data['quota_used'] = 0
                if user_data.get('quota_max') is None: user_data['quota_max'] = 10
                
                # [FIX] Thêm dòng này: Nếu không có stock_level thì mặc định là 1000 kết quả
                if user_data.get('stock_level') is None: user_data['stock_level'] = 1000 
                
                # Trả về thông tin user để lưu vào session
                return user_data
    except Exception as e:
        # In lỗi ra màn hình đen (console) để admin sửa
        print(f"DEBUG LOGIN ERROR: {e}") 
        # Chỉ báo lỗi chung chung cho người dùng để bảo mật
        st.error("Đã xảy ra lỗi kết nối. Vui lòng thử lại sau.")
    
    # [BẢO MẬT] Làm chậm hacker 2 giây nếu đăng nhập thất bại
    time.sleep(2) 
    return None

# --- [NEW] HÀM ĐỔI MẬT KHẨU (SUPABASE VERSION) ---
def change_password_action(email, old_pass_input, new_pass_input):
    try:
        # 1. Lấy mật khẩu hash hiện tại từ Supabase
        response = supabase.table('users').select("password").eq('email', email).execute()
        
        if response.data:
            stored_hash = response.data[0]['password']
            
            # 2. Kiểm tra mật khẩu cũ (dùng bcrypt verify)
            if verify_password(old_pass_input, stored_hash):
                # 3. Mã hóa mật khẩu mới
                new_hashed = hash_password(new_pass_input)
                
                # 4. Cập nhật vào DB
                supabase.table('users').update({"password": new_hashed}).eq('email', email).execute()
                return True, "✅ Đổi mật khẩu thành công!"
            else:
                return False, "❌ Mật khẩu cũ không đúng!"
    except Exception as e:
        return False, f"Lỗi hệ thống: {e}"
    return False, "❌ Không tìm thấy tài khoản!"


# --- [NEW] HÀM LƯU VÀ TẢI BẢN NHÁP (SUPABASE VERSION) ---
def save_draft_to_supabase(email, content):
    try:
        safe_content = sanitize_input(content)
        data = {
            "email": email,
            "content": safe_content,
            "updated_at": datetime.utcnow().isoformat()
        }
        # Lưu thẳng vào Supabase, cực nhanh
        supabase.table('drafts').upsert(data).execute()
        return True
    except Exception as e:
        st.error(f"Lỗi lưu nháp: {e}")
        return False

def load_draft_from_supabase(email):
    try:
        response = supabase.table('drafts').select("content").eq('email', email).execute()
        if response.data:
            return response.data[0]['content']
    except:
        pass
    return ""

# --- [NEW] HÀM CALLBACK ĐỂ AUTO-SAVE ---
def auto_save_callback():
    # Kiểm tra xem đã đăng nhập chưa
    if 'user_info' in st.session_state and st.session_state['user_info']:
        user_email = st.session_state['user_info']['email']
        # Lấy nội dung mới nhất từ ô nhập liệu (thông qua key)
        current_content = st.session_state['main_content_area']
        
        # Gọi hàm lưu vào Supabase
        save_draft_to_supabase(user_email, current_content)
        
        # Hiện thông báo nhỏ góc dưới (Toast) để người dùng yên tâm
        st.toast("Đã tự động lưu nháp! ✅")

# --- [UPDATE] HÀM LẤY LỊCH SỬ TỪ SHEET ORDERS ---
# [ĐÃ SỬA] Thêm Cache để không gọi API liên tục (ttl=300 nghĩa là lưu cache 300 giây/5 phút)
# Sửa st.cache_data thành st.cache (để chạy được trên server cũ)
def get_user_history(email):
    try:
        # Gọi trực tiếp Supabase, chỉ lấy dữ liệu của user đó (Bảo mật hơn)
        # Chỉ lấy tối đa 15 video gần nhất để đảm bảo tốc độ tải trang
        response = supabase.table('orders').select("*").eq('email', email).order('created_at', desc=True).limit(15).execute()        
        if response.data:
            df = pd.DataFrame(response.data)
            # Đổi tên cột cho khớp với giao diện hiển thị
            df = df.rename(columns={
                'created_at': 'NgayTao', 
                'result_link': 'LinkKetQua', 
                'status': 'TrangThai',
                'id': 'ID',
                'audio_link': 'LinkGiongNoi',
                'content': 'NoiDung'
            })
            return df
    except Exception as e:
        print(f"Lỗi tải lịch sử Supabase: {e}")
    
    # Trả về bảng rỗng nếu có lỗi hoặc không có dữ liệu
    return pd.DataFrame()

def update_user_usage(user_row, current_used):
    try:
        gc = get_gspread_client()
        ws = gc.open(DB_SHEET_NAME).worksheet("users")
        ws.update_cell(user_row, 5, current_used + 1)
    except: pass

def log_history(order_id, email, link, date):
    try:
        gc = get_gspread_client()
        ws = gc.open(DB_SHEET_NAME).worksheet("history")
        ws.append_row([order_id, email, link, date])
    except: pass

# --- CẤU HÌNH & SETUP ---
st.set_page_config(page_title="hạt bụi nhỏ - làm video", page_icon="📻", layout="centered")



def get_app_style():
    # Định nghĩa kích thước chuẩn
    base_size = "22px"  # [ĐÃ TĂNG] Cỡ chữ chung to hơn (cũ là 16px)
    title_size = "18px" # [ĐÃ GIẢM] Tiêu đề chính nhỏ lại (cũ là 38px)
    input_height = "45px"
    
    return f"""
    <style>
    /* 1. CẤU TRÚC CHUNG */
    .stApp {{ background-color: #FDF5E6; color: #3E2723; font-family: 'Georgia', serif; }}
    
    /* 2. TIÊU ĐỀ CHÍNH (Đã giảm kích thước) */
    h1 {{
        color: #8B4513 !important; font-size: {title_size} !important; text-align: center;
        border-bottom: none !important; padding-bottom: 10px; margin-bottom: 20px;
        font-weight: bold; 
    }}
    
    /* 3. STEP LABEL (Nhãn bước 1, bước 2...) */
    .step-label {{
        font-size: 22px !important; font-weight: bold; color: #5D4037;
        background-color: #fcefe3; padding: 10px 15px; border-left: 6px solid #8B4513;
        margin-top: 25px; margin-bottom: 15px; border-radius: 0 5px 5px 0;
    }}
    
    /* 4. LABEL & CAPTION (Tăng kích thước các câu hỏi/tiêu đề con) */
    .stRadio label p, .stCheckbox label p, .stSlider label p, 
    .stNumberInput label p, .stSelectbox label p, .stTextInput label p {{
        color: #3E2723 !important; font-weight: 700 !important; 
        font-size: 20px !important; /* [ĐÃ TĂNG] Chữ to rõ hơn */
    }}
    .stMarkdown p, .stCaption {{ color: #5D4037 !important; font-size: 18px !important; }}
    
    /* 5. EXPANDER (Cài đặt & Lịch sử - Đã Phóng to & Cách xa) */
    /* Chỉnh khoảng cách giữa các dòng lịch sử */
    div[data-testid="stExpander"] {{
        margin-bottom: 20px !important; /* Cách nhau 20px cho dễ bấm */
        border-radius: 10px !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05); /* Đổ bóng nhẹ cho đẹp */
    }}
    
    /* Chỉnh kích thước thanh tiêu đề (Cài đặt, Dòng lịch sử) */
    div[data-testid="stExpander"] details > summary {{
        background-color: #FFF8DC !important; color: #3E2723 !important; 
        font-size: 26px !important;  /* [ĐÃ TĂNG] Chữ to hơn nữa (24px) */
        font-weight: bold; 
        border: 2px solid #D7CCC8; border-radius: 10px;
        min-height: 65px !important; /* [ĐÃ TĂNG] Chiều cao tối thiểu 70px cho dễ bấm */
        padding-top: 20px !important; /* Căn giữa chữ theo chiều dọc */
        padding-bottom: 20px !important;
    }}
    div[data-testid="stExpander"] details > summary svg {{ 
        fill: #3E2723 !important; 
        width: 30px !important; /* Phóng to mũi tên */
        height: 30px !important;
    }}
    
    /* 6. NÚT BẤM (Đăng nhập & Zalo đồng nhất) */
    .stButton button, a[data-testid="stLinkButton"] {{
        background-color: #8B4513 !important; 
        color: #FFFFFF !important; 
        font-weight: bold !important; 
        font-size: 18px !important;
        border-radius: 8px !important; 
        border: none !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important;
        padding: 10px 20px !important;
        text-decoration: none !important;
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        transition: all 0.3s ease !important;
    }}
    
    .stButton button:hover, a[data-testid="stLinkButton"]:hover {{
        background-color: #5D4037 !important;
        transform: translateY(-2px);
    }}

    /* SỬA LỖI NÚT HIỆN MẬT KHẨU (EYE ICON) BỊ ĐEN */
    button[aria-label="Show password"] {{
        background-color: transparent !important; /* Xóa nền đen */
        color: #8B4513 !important; /* Đổi icon sang màu nâu */
        border: none !important;
        box-shadow: none !important;
    }}
    
    /* ĐỔI MÀU NÚT ZALO SANG NÂU */
    .zalo-button-container a[data-testid="stLinkButton"] {{
        background-color: #8B4513 !important;
        color: white !important;
        border: 1px solid #5D4037 !important;
    }}

    /* KIỂU CHO DÒNG GIỚI THIỆU */
    .intro-column {{
        padding: 40px 20px;
        border-right: 1px solid #D7CCC8;
    }}
    .intro-item {{
        font-size: 20px;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        gap: 10px;
        color: #5D4037;
    }}
    /* Hiệu ứng khi di chuột vào nút Zalo */
    a[data-testid="stLinkButton"]:hover {{
        background-color: #5D4037 !important;
        color: #FFF8DC !important;
        transform: translateY(-2px);
    }}

    /* 7. INPUT FIELDS */
    .stTextInput input, .stNumberInput input, .stSelectbox div, .stTextArea textarea {{
        background-color: #FFF8DC !important; color: #3E2723 !important;
        font-size: 18px !important;
    }}

    /* ============================================================
       QUAN TRỌNG: CSS RIÊNG CHO ĐIỆN THOẠI (Màn hình nhỏ)
       ============================================================ */
    @media only screen and (max-width: 600px) {{
        
        /* 1. Ép các lựa chọn Radio (Nguồn, Giọng đọc) xuống dòng */
        div[data-testid="stRadio"] > div {{
            flex-direction: column !important; /* Xếp dọc */
            align-items: flex-start !important;
        }}

        /* 1. Thu nhỏ tiêu đề */
        h1 {{
            font-size: 20px !important; /* [ĐÃ SỬA] Giảm xuống 20px cho đồng bộ */
            margin-bottom: 10px !important;
            padding-bottom: 5px !important;
        }}
        
        /* 2. Tăng khoảng cách giữa các lựa chọn để dễ bấm */
        div[data-testid="stRadio"] label {{
            margin-bottom: 12px !important;
            background: #FFF3E0;
            padding: 12px;
            border-radius: 8px;
            width: 100%; /* Full chiều ngang */
        }}

        /* 3. Canh lề lại cho gọn và giảm khoảng trống trên cùng */
        .main .block-container {{
            padding-top: 0rem !important; /* Đưa hẳn về 0 */
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }}
        
        /* Triệt tiêu hoàn toàn khoảng trống phía trên tiêu đề H1 */
        h1 {{
            margin-top: -45px !important; /* Đẩy tiêu đề lên cao hơn nữa */
            padding-top: 0px !important;
        }}

        /* Giảm khoảng cách giữa logo và form đăng nhập trên mobile */
        .intro-column {{
            padding-top: 10px !important;
            padding-bottom: 10px !important;
        }}

        /* 4. [FIX] AUDIO PLAYER TRÊN MOBILE (STYLE GIỐNG PC) */
        audio {{
            width: 100% !important;     
            height: 50px !important;     /* Chiều cao vừa phải giống PC */
            margin-top: 15px !important;
            margin-bottom: 15px !important;
            border-radius: 30px !important; /* Bo tròn mạnh giống PC */
            box-shadow: none !important; /* Bỏ bóng đen mặc định */
        }}
        
        /* [QUAN TRỌNG] Đổi màu nền xám mặc định của điện thoại thành màu Nâu Nhạt */
        audio::-webkit-media-controls-panel {{
            background-color: #D7CCC8 !important; /* Mã màu nâu nhạt (Cafe sữa) */
            border: 1px solid #8D6E63 !important; /* Viền nâu đậm nhẹ */
        }}
        
        /* Chỉnh nút Play trên điện thoại cho nổi bật nhưng không quá to */
        audio::-webkit-media-controls-play-button {{
            background-color: #5D4037 !important; /* Nút màu nâu đậm */
            border-radius: 50% !important;
            color: white !important;
            transform: scale(1.3) !important; /* Phóng to vừa phải (1.3) thay vì 1.8 */
        }}
    }}
    
    footer {{visibility: hidden;}}
    </style>
    """


# --- [UPDATED] HÀM KIỂM TRA LINK (MẠNH HƠN) ---
@st.cache_data(ttl=86400) # Lưu kết quả kiểm tra trong 24 giờ
def check_link_exists(url):
    if not url: return False
    try:
        # 1. Giả danh trình duyệt thật (User-Agent) để không bị chặn
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # 2. Tăng thời gian chờ lên 5 giây (phòng khi mạng chậm)
        # 3. allow_redirects=True: Rất quan trọng với link HuggingFace/Drive
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=5)
        
        # Nếu mã trả về là 200 (OK) hoặc 302 (Chuyển hướng thành công) thì là có file
        if response.status_code in [200, 302]:
            return True
            
        # [PHÒNG HỜ] Nếu head thất bại, thử gọi get nhẹ 1 cái (stream=True để không tải hết file)
        if response.status_code in [403, 405]:
            r2 = requests.get(url, headers=headers, stream=True, timeout=5)
            r2.close() # Đóng kết nối ngay
            return r2.status_code == 200
            
        return False
    except Exception as e:
        print(f"Lỗi check link: {e}")
        # [QUAN TRỌNG] Nếu lỗi mạng (không kết nối được), 
        # TẠM THỜI TRẢ VỀ TRUE để thà hiện player còn hơn là mất tính năng
        return True

# Inject CSS ngay lập tức (Không cần tham số nữa)
st.markdown(get_app_style(), unsafe_allow_html=True)

# [ĐÃ XÓA LINK ZALO CŨ ĐỂ CHUYỂN VÀO TỪNG MÀN HÌNH CỤ THỂ]
pass

DB_SHEET_NAME = "VideoAutomation_DB"
DB_WORKSHEET = "orders"
# Lấy ID từ secrets, nếu không có thì dùng chuỗi rỗng để tránh lỗi crash
LIBRARY_SHEET_ID = st.secrets.get("sheets", {}).get("library_id", "")


# --- HÀM XỬ LÝ BACKEND (GIỮ NGUYÊN TUYỆT ĐỐI) ---
def get_creds():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if "gcp_service_account" in st.secrets:
        return ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    return ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)

def get_gspread_client(): return gspread.authorize(get_creds())

@st.cache_data(ttl=3600)
def get_library_structure():
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(LIBRARY_SHEET_ID)
        all_sheets = sh.worksheets()
        WANTED_TABS = ["duoi_60s", "duoi_90s", "duoi_180s", "tren_180s"] 
        final_list = []
        for ws in all_sheets:
            if ws.title in WANTED_TABS: final_list.append(ws.title)
        return final_list
    except Exception as e: return [f"Lỗi: {str(e)}"]

# --- ĐÃ SỬA ĐỂ HỖ TRỢ PHÂN QUYỀN STOCK ---
@st.cache_data(ttl=600) # Chỉ giữ cache 10 phút để tiết kiệm RAM
def get_scripts_from_supabase_by_category(category_name, limit=50):
    try:
        # Chỉ lấy 50 bản ghi thay vì 1000 để giảm tải RAM cho Streamlit
        response = supabase.table('library').select("*").eq('category', category_name).limit(limit).execute()
        return response.data
    except Exception as e:
        print(f"Lỗi load kịch bản: {e}")
        return []

# [NEW] TÌM KIẾM TRONG DATABASE (Nhanh hơn Sheet rất nhiều)
def search_global_library(keyword):
    try:
        keyword = keyword.strip()
        if not keyword: return []
        
        # TỐI ƯU: Chỉ lấy các cột cần thiết để nhẹ dung lượng truyền tải
        # Sử dụng .or_ để tìm cả trong nội dung và danh mục
        response = supabase.table('library') \
            .select("content, audio_url, category") \
            .ilike('content', f'%{keyword}%') \
            .limit(20) \
            .execute()
        
        results = []
        for item in response.data:
            results.append({
                "content": item['content'],
                "audio": item['audio_url'],
                "source_sheet": item['category']
            })
        return results
    except Exception as e:
        st.error(f"Lỗi tìm kiếm: {e}")
        return []


def upload_to_catbox(file_obj, custom_name=None):
    # [NÂNG CẤP] Sử dụng hạ tầng CLOUDINARY (Siêu nhanh & Ổn định)
    import io
    
    # --- CẤU HÌNH TỪ SECRETS (BẢO MẬT) ---
    if "cloudinary" in st.secrets:
        CLOUD_NAME = st.secrets["cloudinary"]["cloud_name"]
        UPLOAD_PRESET = st.secrets["cloudinary"]["upload_preset"]
    else:
        # Giá trị mặc định nếu chưa cấu hình secrets
        CLOUD_NAME = "nothing" 
        UPLOAD_PRESET = "nothing"
    # ----------------------------------------

    try:
        # API của Cloudinary
        url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/video/upload"
        
        # 1. Xử lý file (Tương tự logic cũ)
        if custom_name:
            filename = custom_name
        else:
            filename = getattr(file_obj, "name", "audio.wav")
            
        if isinstance(file_obj, bytes):
            file_stream = io.BytesIO(file_obj)
        else:
            file_stream = file_obj

        # 2. Gửi file lên Cloudinary
        # Lưu ý: resource_type='video' dùng chung cho cả Audio và Video
        data = {
            "upload_preset": UPLOAD_PRESET
        }
        files = {
            "file": (filename, file_stream)
        }
        
        with st.spinner("Đang tải lên Cloudinary Server tốc độ cao..."):
            r = requests.post(url, data=data, files=files, timeout=60)
            
        if r.status_code == 200:
            # Lấy link bảo mật (https) từ kết quả trả về
            return r.json()['secure_url']
        else:
            st.error(f"Lỗi Cloudinary: {r.text}")
            
    except Exception as e:
        print(f"Lỗi upload: {e}")
        st.error(f"Lỗi hệ thống: {e}")
        
    return None


# --- [NEW] HÀM LÀM SẠCH RIÊNG CHO TTS (BẢN NÂNG CẤP V2) ---
def clean_text_for_tts(text):
    if not text: return ""
    text = str(text)
    
    # 1. Xóa các thẻ HTML & Link rác
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'http\S+', '', text)
    
    # 2. [NÂNG CẤP] THAY THẾ TỪ VIẾT TẮT (DICT)
    # Bạn có thể thêm các từ muốn sửa vào danh sách dưới đây:
    replacements = {
        "vn": "Việt Nam",
        "HT": "Hòa Thượng",
        "sp": "Sư phụ",
        "TT": "Thượng Tọa",
        "ko": "không",
        "k": "không",
        "hok": "không",
        "dc": "được",
        "đc": "được",
        "mn": "mọi người",
        "mng": "mọi người",
        "acc": "tài khoản",
        "fb": "Facebook",
        "zalo": "Za lô",
        "kg": "ki lô gam",
        "km": "ki lô mét",
        "sp": "sản phẩm",
        "shop": "cửa hàng",
        "ok": "ô kê"
    }
    
    # Vòng lặp thay thế thông minh (Dùng Regex)
    for k, v in replacements.items():
        # \b nghĩa là "ranh giới từ" -> Chỉ thay khi từ đứng một mình
        # re.IGNORECASE -> Không phân biệt hoa thường (VN hay vn đều thay hết)
        text = re.sub(r'\b' + re.escape(k) + r'\b', v, text, flags=re.IGNORECASE)

    # 3. Xóa ký tự điều khiển lạ & Chuẩn hóa khoảng trắng
    text = "".join(ch for ch in text if ch.isprintable())
    text = " ".join(text.split())
    
    return text.strip()



# --- [NEW] HÀM GỌI API TTS (CHẤT LƯỢNG CAO - GEMINI) ---

def _convert_to_wav(base64_raw_data):
    """Hàm phụ: Convert raw PCM từ Gemini sang WAV"""
    try:
        sample_rate, num_channels, bits_per_sample = 24000, 1, 16
        raw_buffer = base64.b64decode(base64_raw_data)
        
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        wav_header_size, data_size = 44, len(raw_buffer)
        file_size = wav_header_size + data_size - 8
        
        header = bytearray(wav_header_size)
        header[0:4] = b'RIFF'
        struct.pack_into('<I', header, 4, file_size)
        header[8:12] = b'WAVE'
        header[12:16] = b'fmt '
        struct.pack_into('<IHHIIHH', header, 16, 16, 1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample)
        header[36:40] = b'data'
        struct.pack_into('<I', header, 40, data_size)
        
        return bytes(header) + raw_buffer
    except Exception as e:
        print(f"Lỗi convert WAV: {e}")
        return None

# --- [NEW] CẤU HÌNH GIỌNG ĐỌC GEMINI (CHUẨN HÓA) ---
# Google chỉ có 5 giọng gốc (Puck, Charon, Kore, Fenrir, Aoede).
# Ta sẽ tạo 10 biến thể bằng cách kết hợp Giọng gốc + Phong cách (Prompt).
GEMINI_STYLES = {
    "Nam 1 - Trầm Ấm (Charon)":      {"id": "Charon", "style": "trầm ấm, dày, uy lực"},
    "Nam 2 - Kể Chuyện (Fenrir)":    {"id": "Fenrir", "style": "tự nhiên, như đang kể chuyện đời thường"},
    "Nam 3 - Nhẹ Nhàng (Puck)":      {"id": "Puck",   "style": "nhẹ nhàng, thư thái, chữa lành"},
    "Nam 4 - Sâu Sắc (Charon Deep)": {"id": "Charon", "style": "rất trầm, sâu sắc, chậm rãi, suy tư"},
    "Nam 5 - Năng Lượng (Fenrir)":   {"id": "Fenrir", "style": "nhanh nhẹn, vui vẻ, tràn đầy năng lượng"},
    "Nam 6 - Truyền Cảm (Puck)":     {"id": "Puck",   "style": "truyền cảm, nhấn nhá rõ ràng"},
    "Nữ 1 - Dịu Dàng (Aoede)":       {"id": "Aoede",  "style": "dịu dàng, ngọt ngào, như lời mẹ ru"},
    "Nữ 2 - Nghiêm Túc (Kore)":      {"id": "Kore",   "style": "nghiêm túc, bản tin, rõ ràng"},
    "Nữ 3 - Tự Nhiên (Aoede)":       {"id": "Aoede",  "style": "tự nhiên, như đang tâm sự"},
    "Nữ 4 - Nhẹ Nhàng (Kore)":       {"id": "Kore",   "style": "nhẹ nhàng, thủ thỉ"}
}

def tts_gemini(text, voice_style_key="Nam 1 - Trầm Ấm (Charon)", region="Miền Nam", is_test=False):
    """
    Google Gemini TTS - Updated (Sửa lỗi thiếu base64 & Config chuẩn)
    """
    if "gemini" in st.secrets and "key" in st.secrets["gemini"]:
        api_key = st.secrets["gemini"]["key"]
    else:
        st.error("⚠️ Chưa cấu hình Gemini API Key!")
        return None

    voice_config = GEMINI_STYLES.get(voice_style_key, GEMINI_STYLES["Nam 1 - Trầm Ấm (Charon)"])
    voice_id = voice_config["id"]
    
    if is_test:
        if not text or len(text.strip()) < 5:
            input_text = f"Chào bạn, tôi là giọng đọc {region}."
        else:
            sentences = re.split(r'(?<=[.!?])\s+', text.strip())
            input_text = " ".join(sentences[:2])
    else:
        input_text = text

    # [CẬP NHẬT] URL generateContent (Bỏ key khỏi URL để bảo mật hơn)
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
    
    # [CẬP NHẬT] Gửi Key qua Header & Chuyển 'audio' thành 'AUDIO' (Viết hoa)
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }
    
    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": f"{input_text}"}]
        }],
        "generationConfig": {
            "responseModalities": ["AUDIO"], # <--- SỬA THÀNH CHỮ HOA ĐỂ GOOGLE HIỂU RÕ
            "temperature": 1,
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": voice_id
                    }
                }
            }
        }
    }
    
    try:
        # Dùng requests.post với headers chuẩn
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            # Xử lý kết quả (Hỗ trợ cả dạng list và dict)
            candidates_data = result[0] if isinstance(result, list) and len(result) > 0 else result
            
            if candidates_data and 'candidates' in candidates_data:
                for candidate in candidates_data['candidates']:
                    if 'content' in candidate and 'parts' in candidate['content']:
                        for part in candidate['content']['parts']:
                            if 'inlineData' in part and 'data' in part['inlineData']:
                                # Convert Base64 sang WAV
                                wav_data = _convert_to_wav(part['inlineData']['data'])
                                if wav_data:
                                    if is_test: return wav_data 
                                    return upload_to_catbox(wav_data, "gemini_voice.wav")
            
            # Nếu chạy đến đây mà không return thì là lỗi dữ liệu rỗng
            print(f"DEBUG GEMINI: {result}") # In ra log server để kiểm tra
            st.error("Gemini không trả về dữ liệu âm thanh (Lỗi cấu trúc response).")
        else:
            st.error(f"Lỗi API ({response.status_code}): {response.text}")
    except Exception as e: 
        st.error(f"Lỗi kết nối: {e}")
    return None




# --- [NEW] HÀM ĐỒNG BỘ TỪ GOOGLE SHEET VỀ SUPABASE ---
def sync_sheet_to_supabase():
    try:
        # Kết nối Google Sheet
        gc = get_gspread_client()
        sh = gc.open_by_key(LIBRARY_SHEET_ID)
        target_sheets = ["duoi_60s", "duoi_90s", "duoi_180s", "tren_180s"]
        
        total_synced = 0
        status_text = st.empty()
        
        # Lấy Base URL từ secrets
        BASE_URL = st.secrets["huggingface"]["base_url"] if "huggingface" in st.secrets else ""

        for sheet_name in target_sheets:
            status_text.text(f"⏳ Đang đồng bộ sheet: {sheet_name}...")
            try:
                ws = sh.worksheet(sheet_name)
                data = ws.get_all_records()
            except: continue # Bỏ qua nếu không tìm thấy sheet
            
            # [LOGIC MỚI] 1. Lấy danh sách nội dung ĐÃ CÓ trong Supabase của sheet này
            # Mục đích: Để so sánh và loại bỏ những cái trùng lặp
            existing_response = supabase.table('library').select("content").eq('category', sheet_name).execute()
            
            # Tạo một tập hợp (set) chứa các nội dung đã tồn tại để tra cứu cho nhanh
            # Lưu ý: strip() để xóa khoảng trắng thừa đầu đuôi
            existing_contents = {str(item['content']).strip() for item in existing_response.data}
            
            batch_data = []
            for i, row in enumerate(data):
                # Tìm cột nội dung
                content = ""
                for k, v in row.items():
                    if "nội dung" in k.lower() or "content" in k.lower():
                        content = str(v).strip() # [Fix] Luôn làm sạch chuỗi
                        break
                
                # [LOGIC MỚI] 2. Chỉ thêm nếu có nội dung VÀ nội dung đó CHƯA CÓ trong DB
                if content and content not in existing_contents:
                    # [BẢO MẬT] Làm sạch nội dung kịch bản trước khi đưa vào DB
                    clean_content = sanitize_input(content)
                    
                    # [ĐÃ SỬA] Cộng thêm 1 để khớp với tên file (1.mp3, 2.mp3...)
                    audio_link = f"{BASE_URL}{sheet_name}/{i + 2}.mp3"
                    
                    # Chuẩn bị dữ liệu
                    batch_data.append({
                        "content": clean_content,
                        "audio_url": audio_link,
                        "category": sheet_name,
                        "source_index": i # Index thực tế
                    })
            
            # [LOGIC MỚI] 3. Dùng INSERT thay vì UPSERT
            # Vì ta đã lọc trùng rồi, nên chỉ cần Insert cái mới thôi
            if batch_data:
                chunk_size = 50
                for k in range(0, len(batch_data), chunk_size):
                    # Dùng insert để thêm mới (nếu lỡ vẫn còn trùng thì DB sẽ báo lỗi, nhưng ta đã lọc ở trên rồi)
                    supabase.table('library').insert(batch_data[k:k+chunk_size]).execute()
                total_synced += len(batch_data)

        if total_synced > 0:
            status_text.success(f"✅ Đã thêm mới {total_synced} kịch bản vào hệ thống!")
        else:
            status_text.info("✅ Hệ thống đã cập nhật. Không có kịch bản mới nào.")
            
        return True
    except Exception as e:
        st.error(f"Lỗi sync: {e}")
        return False
    
    
# --- [UPDATE] GIAO DIỆN ADMIN DASHBOARD ---
def admin_dashboard():
    # [FIX] CSS MÀU CHỮ TAB CHO ADMIN (Paste đoạn này vào đây hoặc vào get_app_style đều được)
    st.markdown("""
    <style>
        button[data-baseweb="tab"] div[data-testid="stMarkdownContainer"] p {
            color: #3E2723 !important; font-size: 18px !important; font-weight: bold !important;
        }
        div[data-baseweb="tab-highlight"] { background-color: #8B4513 !important; }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    st.title("🛠️ QUẢN TRỊ VIÊN (ADMIN)")
    
    # [CẬP NHẬT] Thêm Tab thứ 3 là Quản lý User
    tab1, tab2, tab3 = st.tabs(["👥 Thêm User Mới", "🔄 Đồng bộ Kịch bản", "✏️ Sửa/Tìm User"])
    
    # --- CẤU HÌNH CÁC GÓI CƯỚC (Đã cập nhật theo yêu cầu) ---
    PLAN_CONFIG = {
            "Free (Miễn phí)":    {"quota_per_month": 10,  "code": "free"},
            "Gói 30k (Cơ bản)":   {"quota_per_month": 30,  "code": "basic"},
            "Gói 60k (Nâng cao)": {"quota_per_month": 60,  "code": "pro"}, # Đã giảm từ 90 xuống 60
            "Gói huynh đệ":       {"quota_per_month": 60,  "code": ""}
    }

    with tab1:
        st.subheader("Tạo tài khoản & Gia hạn")
        
        # --- CẤU HÌNH CÁC GÓI CƯỚC (Đã cập nhật chuẩn) ---
        PLAN_CONFIG = {
            "Free (Miễn phí)":    {"quota_per_month": 10,  "code": "free"},
            "Gói 30k (Cơ bản)":   {"quota_per_month": 30,  "code": "basic"},
            "Gói 60k (Nâng cao)": {"quota_per_month": 90,  "code": "pro"},
            "Gói huynh đệ":       {"quota_per_month": 60,  "code": "dacbiet"}
        }
        
        DURATION_CONFIG = {
            "1 Tháng": 1,
            "3 Tháng": 3,
            "6 Tháng": 6,
            "12 Tháng (1 Năm)": 12
        }

        # [QUAN TRỌNG] Đã bỏ st.form để số liệu nhảy tự động
        st.info("👇 Nhập thông tin khách hàng mới")
        
        new_email = st.text_input("Email khách hàng", placeholder="vidu@gmail.com")
        new_pass = st.text_input("Mật khẩu", type="password")
        
        st.markdown("---")
        st.markdown("##### 📦 Chọn gói đăng ký")
        
        c1, c2 = st.columns(2)
        with c1:
            # Chọn gói - Tự động reload trang để cập nhật số video
            selected_plan_name = st.selectbox("Loại gói cước", list(PLAN_CONFIG.keys()), key="sb_new_user_plan")
        with c2:
            selected_duration_name = st.selectbox("Thời hạn đăng ký", list(DURATION_CONFIG.keys()), key="sb_new_user_duration")
        
        # --- LOGIC TÍNH TOÁN TỰ ĐỘNG ---
        plan_info = PLAN_CONFIG[selected_plan_name]
        months = DURATION_CONFIG[selected_duration_name]
        
        # Tính tổng quota = (Quota tháng) x (Số tháng)
        calculated_quota = plan_info["quota_per_month"] * months
        
        # Tính ngày hết hạn
        expiry_date = datetime.utcnow() + timedelta(days=30 * months)
        expiry_str = expiry_date.strftime("%d/%m/%Y")

        # Hiển thị thông tin review
        st.success(f"""
        📊 **Review Cấu hình:**
        - Gói: **{plan_info['code'].upper()}** ({plan_info['quota_per_month']} video/tháng)
        - Thời hạn: **{months} tháng**
        - Ngày hết hạn: **{expiry_str}**
        """)
        
        # [FIX] Tạo key động dựa trên tên gói và thời hạn
        # Khi user đổi gói, key thay đổi -> ô nhập liệu reset về giá trị mới
        dynamic_key_quota = f"quota_{selected_plan_name}_{selected_duration_name}"

        # Ô nhập số (Tự động cập nhật giá trị theo gói đã chọn)
        final_quota = st.number_input("Tổng số video (Quota Max) - Có thể sửa tay", 
                                    value=calculated_quota,
                                    min_value=0,
                                    step=1,
                                    key=dynamic_key_quota)
        
        # Nút Lưu (Dùng st.button thường)
        if st.button("💾 LƯU USER VÀO SUPABASE", type="primary"):
            if not new_email or not new_pass:
                st.warning("⚠️ Vui lòng điền Email và Mật khẩu!")
            else:
                try:
                    # [BẢO MẬT] Kiểm tra email trùng
                    check_exist = supabase.table('users').select("email").eq('email', new_email).execute()
                    if check_exist.data and len(check_exist.data) > 0:
                        st.error(f"❌ Email '{new_email}' đã tồn tại!")
                        st.stop()

                    # Mã hóa mật khẩu
                    hashed = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
                    
                    # Chuẩn bị dữ liệu insert
                    data = {
                        "email": new_email,
                        "password": hashed,
                        "plan": plan_info['code'],
                        "quota_max": final_quota,
                        "quota_used": 0,  # Khởi tạo đã dùng = 0
                        "role": "user",
                        "stock_level": 1000 # Mặc định stock level
                    }
                    
                    supabase.table('users').insert(data).execute()
                    st.success(f"✅ Đã tạo tài khoản thành công: {new_email}")
                    st.balloons()
                    
                except Exception as e:
                    st.error(f"Lỗi tạo user: {e}")

    with tab2:
        st.subheader("Cập nhật dữ liệu từ Google Sheet sang Supabase")
        st.info("Bấm nút dưới đây khi bạn vừa thêm kịch bản mới vào file Google Sheet.")
        if st.button("🚀 Bắt đầu Đồng bộ ngay"):
            sync_sheet_to_supabase()

    with tab3:
        st.subheader("🔎 Tìm và Cập nhật Gói User")
        
        # 1. Ô tìm kiếm
        c_search1, c_search2 = st.columns([3, 1])
        with c_search1:
            search_email = st.text_input("Nhập Email user cần tìm:", placeholder="user@gmail.com", label_visibility="collapsed")
        with c_search2:
            btn_find = st.button("🔍 Tìm kiếm", use_container_width=True)

        if btn_find:
            try:
                # Tìm user trong Supabase
                res = supabase.table('users').select("*").eq('email', search_email.strip()).execute()
                if res.data and len(res.data) > 0:
                    st.session_state['admin_edit_user'] = res.data[0]
                    st.success(f"✅ Đã tìm thấy: {search_email}")
                else:
                    st.warning("❌ Không tìm thấy user này!")
                    st.session_state['admin_edit_user'] = None
            except Exception as e:
                st.error(f"Lỗi tìm kiếm: {e}")

        # 2. KHU VỰC CHỈNH SỬA (ĐÃ BỎ ST.FORM ĐỂ CẬP NHẬT TỨC THÌ)
        if st.session_state.get('admin_edit_user'):
            user_edit = st.session_state['admin_edit_user']
            st.markdown("---")
            st.markdown(f"#### 👤 Đang sửa: {user_edit['email']}")
            
            # Hiển thị thông số hiện tại
            c1, c2, c3 = st.columns(3)
            c1.info(f"Gói hiện tại: **{user_edit.get('plan', 'N/A')}**")
            c2.info(f"Đã dùng: **{user_edit.get('quota_used', 0)}**")
            c3.info(f"Tổng Quota: **{user_edit.get('quota_max', 0)}**")

            st.markdown("##### 👇 Chọn gói mới để cập nhật")
            
            # [QUAN TRỌNG] Logic tự động cập nhật số liệu
            # 1. Chọn gói
            selected_plan_name = st.selectbox("Chọn gói muốn đổi:", list(PLAN_CONFIG.keys()), key="sb_admin_plan_select")
            
            # 2. Lấy số video mặc định của gói đó ngay lập tức
            suggested_quota = PLAN_CONFIG[selected_plan_name]["quota_per_month"]
            
            # 3. Ô nhập số (Sẽ tự đổi giá trị value theo suggested_quota)
            final_quota_edit = st.number_input("Tổng số video (Quota Max) - Có thể sửa tay", 
                                             value=suggested_quota, 
                                             min_value=0,
                                             step=1)
            
            st.caption(f"ℹ️ Gói **{selected_plan_name}** tương ứng **{suggested_quota}** video.")

            # Nút lưu (Dùng st.button thường thay vì form_submit_button)
            if st.button("💾 LƯU THAY ĐỔI NGAY", type="primary"):
                try:
                    plan_code = PLAN_CONFIG[selected_plan_name]["code"]
                    
                    # Cập nhật vào Supabase
                    supabase.table('users').update({
                        "plan": plan_code,
                        "quota_max": final_quota_edit
                    }).eq('email', user_edit['email']).execute()
                    
                    st.success(f"✅ Đã cập nhật thành công cho {user_edit['email']}!")
                    st.toast(f"Đã đổi sang gói {plan_code} ({final_quota_edit} video)", icon="🎉")
                    
                    # Cập nhật lại thông tin hiển thị ngay lập tức
                    st.session_state['admin_edit_user']['plan'] = plan_code
                    st.session_state['admin_edit_user']['quota_max'] = final_quota_edit
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi khi lưu: {e}")
# --- CSS GIAO DIỆN (FIXED FILE UPLOADER VISIBILITY) ---
st.markdown("""
    <style>
    /* 1. CẤU TRÚC CHUNG */
    .stApp { background-color: #FDF5E6; color: #3E2723; font-family: 'Georgia', serif; }
    
    /* 2. TIÊU ĐỀ (ĐÃ CHỈNH SỬA KÍCH THƯỚC) */
    h1 {
        color: #8B4513 !important; 
        font-size: 25px !important;  /* <-- [PC] Chỉnh số này để thay đổi cỡ chữ trên Máy Tính */
        text-align: center;
        border-bottom: 3px double #8B4513; padding-bottom: 15px; margin-bottom: 25px;
    }

    /* [MOBILE] Cài đặt riêng cho điện thoại */
    @media only screen and (max-width: 600px) {
        h1 {
            font-size: 20px !important; /* <-- [ĐIỆN THOẠI] Chỉnh số này để thay đổi cỡ chữ trên Điện Thoại */
            padding-bottom: 10px !important;
            margin-bottom: 15px !important;
        }
    }
    
    /* 3. STEP LABEL (ĐÃ TĂNG KHOẢNG CÁCH) */
    .step-label {
        font-size: 22px !important; font-weight: bold; color: #5D4037;
        background-color: #fcefe3; padding: 8px 15px; border-left: 6px solid #8B4513;
        
        /* [ĐÃ SỬA] Giảm khoảng cách xuống 20px cho gần hơn */
        margin-top: 20px !important; 
        
        margin-bottom: 20px !important; 
        border-radius: 0 5px 5px 0;
        display: inline-block; /* Giúp khung bao vừa vặn nội dung */
    }
    
    /* 4. INPUT & TEXTAREA */
    .stTextInput input, .stNumberInput input {
        background-color: #FFF8DC !important; color: #3E2723 !important;
        font-weight: 500 !important; border: 1px solid #D7CCC8; border-radius: 4px;
    }
    .stTextArea textarea {
        background-color: #FFF8DC !important; color: #3E2723 !important;
        border: 2px solid #8B4513 !important; 
        font-size: 19px !important; /* [ĐÃ TĂNG] Cỡ chữ to hơn (cũ là 16px) */
        line-height: 1.5 !important; /* Giãn dòng ra chút cho dễ đọc */
    }
    
    /* 5. FIX DROPDOWN & ICONS */
    div[data-baseweb="select"] > div:first-child {
        background-color: #FFF8DC !important; border: 1px solid #D7CCC8; color: #3E2723 !important;
    }
    div[data-baseweb="select"] svg { fill: #3E2723 !important; }
    
    /* 6. LABEL COLORS */
    .stRadio label p, .stCheckbox label p, .stSlider label p, .stNumberInput label p, .stSelectbox label p, .stColorPicker label p {
        color: #3E2723 !important; font-weight: 600 !important; font-size: 16px !important;
    }
    .stMarkdown p, .stCaption { color: #5D4037 !important; }
    
    /* 7. BUTTON (NÚT BẤM CHUNG) */
    .stButton button, div[data-testid="stFormSubmitButton"] button {
        background-color: #8B4513 !important; 
        color: #FFFFFF !important; 
        font-weight: bold !important;
        font-size: 20px !important; 
        border-radius: 8px !important; 
        margin-top: 10px;
        border: none !important;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.2) !important;
    }
    .stButton button:hover, .stButton button:active, .stButton button:focus,
    div[data-testid="stFormSubmitButton"] button:hover,
    div[data-testid="stFormSubmitButton"] button:active,
    div[data-testid="stFormSubmitButton"] button:focus { 
        background-color: #8B4513 !important; color: #FFFFFF !important;
        box-shadow: none !important; border: none !important;
    }
    
    /* 8. EXPANDER (THANH CÀI ĐẶT & TÀI KHOẢN) */
    div[data-testid="stExpander"] details > summary {
        background-color: #FFF8DC !important; color: #3E2723 !important; 
        border: 1px solid #D7CCC8 !important; border-radius: 5px;
        
        /* [MỚI] Ép chiều cao nhỏ lại */
        padding-top: 5px !important;
        padding-bottom: 5px !important;
        min-height: 40px !important; 
        height: auto !important;
    }
    /* Chỉnh mũi tên nhỏ lại cho cân đối */
    div[data-testid="stExpander"] details > summary svg { 
        fill: #3E2723 !important; 
        width: 18px !important;
        height: 18px !important;
    }
    
    /* 9. FILE UPLOADER (Đã sửa lỗi dấu X nằm quá xa) */
    /* Khung chứa file đã upload */
    div[data-testid="stFileUploaderUploadedFiles"] > div {
        background-color: #FFF8DC !important; /* Màu nền kem sáng */
        border: 1px solid #8B4513 !important; /* Viền nâu */
        color: #3E2723 !important;
        
        /* --- DÒNG QUAN TRỌNG MỚI THÊM --- */
        width: fit-content !important; /* Tự động co chiều rộng lại vừa đủ chữ */
        min-width: 150px !important; /* Đảm bảo không bị bé quá */
        padding-right: 10px !important; /* Căn lề phải một chút */
    }
    
    /* Tên file */
    div[data-testid="stFileUploaderUploadedFiles"] div[data-testid="stMarkdownContainer"] p {
        color: #3E2723 !important; 
        font-weight: bold !important;
    }
    /* Icon file (bên trái) */
    div[data-testid="stFileUploaderUploadedFiles"] svg {
        fill: #3E2723 !important; 
    }
    /* Nút Xóa (Dấu X bên phải) */
    div[data-testid="stFileUploaderDeleteBtn"] svg {
        fill: #D32F2F !important; /* Dấu X màu ĐỎ */
        stroke: #D32F2F !important;
    }
            

    /* --- [NEW] TÙY CHỈNH AUDIO PLAYER TO & ĐẸP HƠN --- */
    
    /* 1. Ép trình phát nhạc cao hơn và bo tròn */
    audio {
        height: 55px !important;    /* Tăng chiều cao lên 55px (Mặc định là 40px) */
        width: 100% !important;     /* Phủ kín chiều ngang */
        border-radius: 30px !important; /* Bo tròn mạnh 2 đầu cho mềm mại */
        box-shadow: 0 4px 6px rgba(0,0,0,0.1); /* Đổ bóng nhẹ cho nổi */
        background-color: #F1F8E9; /* Màu nền nhẹ (nếu trình duyệt hỗ trợ trong suốt) */
        margin-top: 10px;
        margin-bottom: 10px;
    }
    
    /* 2. Mẹo CSS dành riêng cho Chrome/Android để chỉnh màu */
    audio::-webkit-media-controls-panel {
        /* [FIX] Đổi từ #FFF8DC (Kem) sang #D7CCC8 (Nâu Cafe Sữa) 
           -> Mục đích: Tạo nền tối hơn để thanh Timeline màu trắng nổi bật lên */
        background-color: #D7CCC8 !important; 
        border: 2px solid #8B4513 !important;
    }
    
    audio::-webkit-media-controls-play-button,
    audio::-webkit-media-controls-mute-button {
        /* [FIX] Nút bấm chuyển sang màu Nâu đổ bóng nhẹ cho dễ bấm */
        background-color: #8B4513 !important;
        border-radius: 50%;
        box-shadow: 1px 1px 4px rgba(0,0,0,0.2) !important;
        /* Tăng kích thước nút lên một chút cho dễ bấm (nếu cần) */
        transform: scale(1.1);
    }
    
    /* [NEW] Chỉnh màu thanh trượt (Timeline) & Volume nếu trình duyệt hỗ trợ */
    audio::-webkit-media-controls-current-time-display,
    audio::-webkit-media-controls-time-remaining-display {
        color: #3E2723 !important; /* Chữ giờ màu nâu đậm cho dễ đọc */
        font-weight: bold;
    }
    
    /* --- ẨN TOÀN BỘ GIAO DIỆN HỆ THỐNG --- */
    
    /* 1. Ẩn menu 3 chấm và thanh header trên cùng */
    #MainMenu {visibility: hidden; display: none;}
    header {visibility: hidden; display: none;}
    
    /* Ẩn hoàn toàn footer mặc định */
    footer {visibility: hidden !important;}
    header {visibility: hidden !important;}

    
    
    
    /* 3. QUAN TRỌNG: Ẩn thanh 'Hosted with Streamlit' màu đỏ và Avatar */
    /* Lệnh này tìm mọi thành phần có tên chứa chữ 'viewerBadge' để ẩn đi */
    div[class*="viewerBadge"] {display: none !important;}
    
    /* 4. Ẩn luôn thanh trang trí 7 màu trên cùng (nếu có) */
    div[data-testid="stDecoration"] {display: none;}
    
    /* ============================================================
       [FIX] MÀU CHỮ TAB (ADMIN DASHBOARD)
       ============================================================ */
    
    /* 1. Đổi màu chữ trong Tab sang màu nâu đậm */
    button[data-baseweb="tab"] div[data-testid="stMarkdownContainer"] p {
        color: #3E2723 !important; 
        font-size: 20px !important;
        font-weight: bold !important;
    }

    /* 2. Đổi màu thanh gạch chân (highlight) khi chọn tab */
    div[data-baseweb="tab-highlight"] {
        background-color: #8B4513 !important;
        height: 4px !important; /* Làm dày thanh gạch chân */
    }

    /* 3. (Tùy chọn) Đổi màu nền tab khi di chuột vào */
    button[data-baseweb="tab"]:hover {
        background-color: #FFF8DC !important;
    }


    </style>
""", unsafe_allow_html=True)

# --- LOGIC MÀN HÌNH CHÍNH ---

if 'user_info' not in st.session_state:
    st.session_state['user_info'] = None

# [NEW] TỰ ĐỘNG ĐĂNG NHẬP BẰNG COOKIE
if not st.session_state['user_info']:
    # Thử đăng nhập bằng token trong cookie
    user_from_cookie = login_by_token()
    if user_from_cookie:
         st.session_state['user_info'] = user_from_cookie
         st.toast(f"Chào mừng trở lại, {user_from_cookie['email']}!", icon="👋")
         st.rerun()

# [LOGIC CŨ] Tự động điền email (Giữ lại làm phương án dự phòng)
if not st.session_state['user_info']:
    params = st.query_params
    if "u" in params:
        st.session_state['saved_email'] = params["u"]
        # Đã xóa đoạn "if user:" gây lỗi vì biến user chưa tồn tại ở đây

# --- GIAO DIỆN ĐĂNG NHẬP MỚI (CLEAN DESIGN) ---
if not st.session_state['user_info']:
    
    st.markdown("<br>", unsafe_allow_html=True) # Chỉ giữ lại 1 dòng khoảng trắng cho thoáng

    # 2. KHUNG ĐĂNG NHẬP CHIA 2 CỘT (PC)
    if st.session_state.get('is_mobile'):
        display_cols = st.columns([1])
        is_pc = False
    else:
        display_cols = st.columns([1, 1], gap="large")
        is_pc = True

    # --- CỘT 1: GIỚI THIỆU (Chỉ hiện trên PC hoặc hiện trên cùng mobile) ---
    with display_cols[0]:
        st.markdown(f"<h1>📻 hạt bụi nhỏ</h1>", unsafe_allow_html=True)
        st.markdown("""
        <div class="intro-column">
            <div class="intro-item">🍃 Biến kịch bản thành video trong 1 nốt nhạc</div>
            <div class="intro-item">🍃 Phụ đề chính xác 100%</div>
            <div class="intro-item">🍃 Chuyên nội dung đạo lý, chữa lành, Phật pháp..</div>
            <div class="intro-item">🍃 AI lựa chọn minh họa phù hợp nội dung</div>
        </div>
        """, unsafe_allow_html=True)

    # --- CỘT 2: FORM ĐĂNG NHẬP ---
    target_col = display_cols[1] if is_pc else display_cols[0]
    with target_col:
        with st.container(border=True):
            st.markdown("<h3 style='text-align: center; color: #5D4037; margin-bottom: 20px;'>🔐 Đăng Nhập</h3>", unsafe_allow_html=True)
            with st.form(key="login_form"):
                # Tự động điền email nếu đã lưu trước đó
                default_email = st.session_state.get('saved_email', "")
                login_email = st.text_input("Email", value=default_email, placeholder="vidu@gmail.com", key="login_email_unique")            
                login_pass = st.text_input("Mật khẩu", type="password", placeholder="••••••", key="login_pass_unique")
                
                # Checkbox và Link quên mật khẩu
                col_sub1, col_sub2 = st.columns(2)
                with col_sub1:
                    remember_me = st.checkbox("Ghi nhớ", value=True)
                with col_sub2:
                    # Cập nhật link dẫn đến nhóm Zalo hỗ trợ
                    st.markdown("<div style='text-align: right; font-size: 14px; padding-top: 5px;'><a href='https://zalo.me/g/ivgedj736' target='_blank' style='color: #8B4513; text-decoration: none;'>Quên mật khẩu?</a></div>", unsafe_allow_html=True)
                submitted = st.form_submit_button("ĐĂNG NHẬP NGAY", use_container_width=True)

            if submitted:
                user = check_login(login_email, login_pass)
                if user:
                    st.session_state['user_info'] = user
                    
                    # [FIX] Logic ghi nhớ đăng nhập (Token)
                    if remember_me:
                        new_token = str(uuid.uuid4())
                        # Lưu token vào database
                        update_session_token(user['id'], new_token)
                        # Lưu token vào cookie trình duyệt (30 ngày)
                        cookie_manager.set("user_session_token", new_token, expires_at=datetime.now() + timedelta(days=30))
                    
                    st.toast("Đăng nhập thành công!", icon="🎉")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error("Sai Email hoặc Mật khẩu, vui lòng thử lại.")

            st.markdown("---")
            # Tăng cỡ chữ lên 20px và làm nổi bật link Đăng ký
            st.markdown("""
                <div style='text-align: center; font-size: 17px; color: #3E2723; line-height: 1.6;'>
                    Chưa có tài khoản?<br>
                    <a href='https://zalo.me/g/ivgedj736' target='_blank' 
                       style='color: #8B4513; font-weight: 800; text-decoration: underline; 
                              display: block; margin-top: 10px; background-color: #FFF3E0; 
                              padding: 10px; border-radius: 8px; border: 1px dashed #8B4513;'>
                        👉 Đăng ký mới qua Zalo tại đây
                    </a>
                </div>
            """, unsafe_allow_html=True)
            



else:
    # ==========================================
    # KHI ĐÃ ĐĂNG NHẬP THÀNH CÔNG -> HIỆN UI CŨ
    # ==========================================
    user = st.session_state['user_info']

    # --- [NEW] NÚT HỖ TRỢ KỸ THUẬT (FLOATING BAR - GÓC DƯỚI TRÁI) ---
    st.markdown("""
        <a href="https://zalo.me/g/ivgedj736" target="_blank" rel="noopener noreferrer" style="text-decoration: none;">
            <div style="
                position: fixed;
                bottom: 1px;
                left: 1px;
                z-index: 99999;
                background-color: #00695C; 
                color: white; 
                padding: 8px 15px; 
                border-radius: 50px; 
                box-shadow: 2px 2px 10px rgba(0,0,0,0.3);
                font-weight: bold; 
                font-size: 16px;
                display: flex; align-items: center; gap: 10px;
                border: 2px solid #E0F2F1;
                transition: transform 0.2s ease-in-out;
            " onmouseover="this.style.transform='scale(1.05)'" onmouseout="this.style.transform='scale(1)'">
                Hỗ trợ
            </div>
        </a>
    """, unsafe_allow_html=True)
    
    # [MODIFIED] HEADER MỚI (Chỉ còn Tiêu đề)
    st.markdown(f"<h1 style='text-align: center; border: none; margin: 0; padding: 0;'>hạt bụi nhỏ - làm video giùm bạn</h1>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True) # Tạo khoảng cách nhỏ
    # Tính toán quota
    quota_left = user['quota_max'] - user['quota_used']
    is_out_of_quota = quota_left <= 0
    
    # Hiển thị thanh trạng thái Quota (Giao diện thẻ bài)
    st.markdown(f"""
    <div style="background-color: #FFF8DC; border: 2px dashed #8B4513; padding: 15px; border-radius: 10px; margin-bottom: 25px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <span style="font-size: 20px; font-weight: bold; color: #3E2723;">👤 {user['email']}</span><br>
                <span style="font-size: 15px; color: #6D4C41;">🏷️ Gói: <b>{user['plan']}</b></span>
            </div>
            <div style="text-align: right;">
                <span style="font-size: 18px; color: {'#D32F2F' if is_out_of_quota else '#2E7D32'}; font-weight: bold;">
                    {user['quota_used']}/{user['quota_max']} video
                </span><br>
                <small style="color: #888;">(Còn lại: {quota_left})</small>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if is_out_of_quota:
        st.error("⚠️ Bạn đã hết lượt tạo video trong tháng này. Vui lòng nâng cấp gói!")

    # === [NEW] KHU VỰC DÀNH RIÊNG CHO ADMIN ===
    # Kiểm tra xem user có phải role='admin' trong Supabase không
    if user.get('role') == 'admin':
        if st.button("🛠️ VÀO TRANG QUẢN TRỊ (ADMIN)", type="primary", use_container_width=True):
            st.session_state['show_admin'] = True
            st.rerun()
            
    # Nếu đang bật chế độ Admin thì hiện Dashboard và DỪNG APP CHÍNH
    if st.session_state.get('show_admin', False):
        if st.button("⬅️ Quay lại App chính"):
            st.session_state['show_admin'] = False
            st.rerun()
        admin_dashboard() # Gọi hàm hiển thị admin
        st.stop() # Dừng không chạy code bên dưới nữa
    # ==========================================

    # --- [NEW] HỘP QUẢN LÝ TÀI KHOẢN (SLIDER/EXPANDER) ---
    # Đặt nằm ngay dưới khung Quota
    with st.expander("👤 Đổi mật khẩu / Thoát", expanded=False):
        
        # 1. Phần Đổi mật khẩu
        st.markdown("##### 🔐 Đổi mật khẩu")
        
        # [NEW] Cảnh báo an toàn cho người dùng (Đã chỉnh màu chữ đậm hơn)
        st.markdown("""
        <div style="background-color: #FFEBEE; color: #D32F2F; padding: 15px; border-radius: 5px; border: 2px solid #D32F2F; margin-bottom: 10px; font-weight: bold;">
            ⛔ KHÔNG NÊN DÙNG CHUNG mật khẩu Facebook, Gmail ... hay Ngân hàng tại đây.<br>
        </div>
        """, unsafe_allow_html=True)

        with st.form("change_pass_form_inside"):
            cp_old = st.text_input("Mật khẩu cũ", type="password")
            cp_new = st.text_input("Mật khẩu mới", type="password")
            cp_conf = st.text_input("Nhập lại mật khẩu mới", type="password")
            
            # Nút xác nhận nhỏ gọn
            if st.form_submit_button("💾 Cập nhật mật khẩu"):
                if not cp_old or not cp_new:
                    st.error("Vui lòng nhập đầy đủ thông tin.")
                elif cp_new != cp_conf:
                    st.error("Mật khẩu mới không khớp nhau.")
                else:
                    success, msg = change_password_action(user['email'], cp_old, cp_new)
                    if success:
                        st.success(msg)
                    else:
                        st.error(msg)
        
        st.markdown("---") # Đường kẻ ngang ngăn cách
        
        # 2. Phần Đăng xuất
        st.markdown("##### 🚪 Đăng xuất khỏi tài khoản")
        if st.button("Đăng xuất ngay", key="btn_logout_inside", type="secondary", use_container_width=True):
            # Xóa session
            st.session_state['user_info'] = None
            st.query_params.clear()
            
            # Xóa Cookie & Token trong DB
            try:
                # Xóa token trong cookie trình duyệt
                cookie_manager.delete("user_session_token")
                # (Tùy chọn) Xóa token trong DB để bảo mật tuyệt đối
                if user: update_session_token(user['id'], None)
            except: pass
            
            st.rerun()

    # [ĐÃ SỬA] Đã xóa khoảng trắng <br> ở đây để Bước 1 đẩy lên cao hơn

    # --- (B1) EMAIL (ĐÃ ẨN GIAO DIỆN) ---
    # Chúng ta gán thẳng email từ session vào biến, không cần hiện input
    email = user['email']

    # --- (B1) NGUỒN KỊCH BẢN (GIAO DIỆN TÌM KIẾM MỚI) ---
    st.markdown("""
        <div class="step-card">
            <span class="step-label"> BƯỚC 1️⃣: CHUẨN BỊ KỊCH BẢN</span>
        </div>
    """, unsafe_allow_html=True)
    
    # [UX] index=None để ban đầu không chọn gì -> Ẩn các thao tác bên dưới
    source_opt = st.radio("Chọn nguồn kịch bản:", 
                          ["📂 Tìm trong Thư viện", "✍️ Tự viết mới"], 
                          index=None, 
                          horizontal=True,
                          key="radio_source_opt")

    final_script_content = ""
    selected_library_audio = None 

    # 1.1 LOGIC TÌM KIẾM TRONG THƯ VIỆN
    # 1.1 LOGIC TÌM KIẾM TRONG THƯ VIỆN (CHẠY TRỰC TIẾP TRÊN SUPABASE)
    if source_opt == "📂 Tìm trong Thư viện":
        st.info("💡Nhập tâm trạng hoặc từ khóa để tìm kịch bản phù hợp")
        
        with st.form(key="search_form"):
            c_search1, c_search2 = st.columns([3, 1], vertical_alignment="center")
            with c_search1:
                search_kw = st.text_input("", label_visibility="collapsed", placeholder="Nhập từ khóa (Ví dụ: Nhân quả, chữa lành...)")
            with c_search2:
                btn_search = st.form_submit_button("🔍 TÌM NGAY", use_container_width=True)

        if btn_search and search_kw:
            with st.spinner("Đang lục tìm trong kho dữ liệu..."):
                # Gửi lệnh cho Supabase tự tìm
                st.session_state['search_results'] = search_global_library(search_kw)
                st.session_state['has_searched'] = True
                if 'last_picked_idx' in st.session_state:
                    del st.session_state['last_picked_idx']

        if st.session_state.get('has_searched'):
            results = st.session_state.get('search_results', [])
            if results:
                preview_options = [f"[{item['source_sheet']}] {item['content'][:60]}..." for item in results]
                selected_idx = st.selectbox("Chọn kịch bản phù hợp:", range(len(results)), 
                                            format_func=lambda x: preview_options[x], key="sb_search_select")
                
                chosen_content = results[selected_idx]['content']
                selected_library_audio = results[selected_idx].get('audio')

                # Cập nhật vào vùng soạn thảo nếu có thay đổi
                if st.session_state.get('last_picked_idx') != selected_idx:
                    st.session_state['main_content_area'] = chosen_content
                    st.session_state['last_picked_idx'] = selected_idx
                    st.rerun()
                
                final_script_content = chosen_content
            else:
                st.warning("⚠️ Không tìm thấy kết quả nào. Hãy thử từ khóa khác!")

    elif source_opt == "✍️ Tự viết mới":
        st.caption("Nhập nội dung kịch bản của bạn vào bên dưới:")
    
    # --- KHUNG HIỂN THỊ NỘI DUNG & BỘ ĐẾM TỪ ---
    if source_opt:
        # [ĐÃ SỬA] Cố định chiều cao khung nhập liệu (Bạn có thể sửa số 450 thành số khác tùy ý)
        FIXED_HEIGHT = 450 
        
        # Text Area - [ĐÃ SỬA LỖI WARNING] Bỏ tham số 'value' để tránh xung đột với key
        noi_dung_gui = st.text_area("", height=FIXED_HEIGHT, 
                                    placeholder="Nội dung kịch bản sẽ hiện ở đây...", 
                                    key="main_content_area")
        
        # [CHỈNH SỬA] Chỉ hiện các nút Nháp khi đang ở chế độ "Tự viết mới"
        if source_opt == "✍️ Tự viết mới":
            # [SỬA LỖI UI] Tăng tỷ lệ cột đầu từ 1 lên 1.5 để nút rộng hơn, không bị rớt dòng
            c_draft1, c_draft2, c_draft3 = st.columns([1.5, 1.5, 4]) 
            
            # [SỬA LỖI API] Hàm xử lý riêng cho việc bấm nút (Callback)
            def load_draft_callback():
                saved_content = load_draft_from_sheet(user['email'])
                if saved_content:
                    st.session_state['main_content_area'] = saved_content
                    st.toast("Đã tải lại bản nháp cũ!", icon="📂")
                else:
                    st.toast("Bạn chưa có bản nháp nào!", icon="⚠️")

            with c_draft1:
                if st.button("💾 Lưu nháp", use_container_width=True, key="btn_save_draft"):
                    if noi_dung_gui:
                        if save_draft_to_sheet(user['email'], noi_dung_gui):
                            st.toast("Đã lưu nháp thành công!", icon="✅")
                        else:
                            st.error("Lỗi khi lưu nháp.")
                    else:
                        st.warning("Chưa có nội dung để lưu!")

            with c_draft2:
                # [FIX] Dùng on_click gọi hàm callback để nạp dữ liệu an toàn
                st.button("📂 Tải bản nháp", use_container_width=True, help="Tải lại nội dung cũ", key="btn_load_draft", on_click=load_draft_callback)
        
        # [NEW] LOGIC ĐẾM TỪ & THỜI GIAN (Tự động chạy khi nội dung thay đổi)
        if noi_dung_gui:
            # 1. Đếm số từ (tách theo khoảng trắng)
            word_count = len(noi_dung_gui.split())
            
            # 2. Tính thời gian (200 từ/phút => 1 từ = 0.3 giây)
            seconds = int((word_count / 200) * 60)
            
            # Quy đổi ra Phút:Giây cho dễ nhìn
            minutes = seconds // 60
            sec_rem = seconds % 60
            time_str = f"{minutes} phút {sec_rem} giây" if minutes > 0 else f"{seconds} giây"
            
            # Hiển thị thanh trạng thái
            st.markdown(f"""
            <div style="background-color: #EFEBE9; padding: 10px; border-radius: 5px; border-left: 5px solid #8D6E63; margin-top: 5px;">
                <span style="font-weight: bold; color: #3E2723;">📊</span> {word_count} từ 
                &nbsp;&nbsp;|&nbsp;&nbsp; 
                <span style="font-weight: bold; color: #3E2723;">⏱️ Thời lượng ước tính:</span> {time_str}
            </div>
            """, unsafe_allow_html=True)
        else:
            # Nếu chưa có nội dung
             st.markdown(f"""<div style="color: #999; font-style: italic; margin-top: 5px;">(Hãy nhập nội dung để xem ước lượng thời gian)</div>""", unsafe_allow_html=True)
            
    else:
        noi_dung_gui = ""

    # --- (B2) GIỌNG ĐỌC (GIAO DIỆN ẨN MẶC ĐỊNH) ---
    
    # [MỚI] Thêm 3 dòng <br> để đẩy Bước 2 xuống xa hơn (Bạn có thể thêm bớt <br> tùy ý)
    st.markdown("<br><br>", unsafe_allow_html=True) 

    st.markdown("""
        <div class="step-card">
            <span class="step-label"> BƯỚC 2️⃣: CHUẨN BỊ GIỌNG ĐỌC</span>
        </div>
    """, unsafe_allow_html=True)
    
    # --- [FIX] KIỂM TRA LINK TRƯỚC KHI HIỂN THỊ ---
    # Chỉ hiện tùy chọn "Giọng mẫu" nếu link đó thực sự tồn tại (Status 200)
    has_valid_audio = False
    if selected_library_audio and str(selected_library_audio).startswith("http"):
        # Gọi hàm kiểm tra (có thể hơi chậm xíu nếu mạng yếu, nhưng đảm bảo chính xác)
        has_valid_audio = check_link_exists(selected_library_audio)

    # Tạo danh sách lựa chọn
    # Tạo danh sách lựa chọn
    voice_options = ["🎙️ Thu âm trực tiếp", "📤 Tải file lên", "🤖 Giọng AI Gemini"]
    
    # Chỉ thêm lựa chọn này nếu file audio TỒN TẠI
    if has_valid_audio: 
        voice_options.insert(0, "🎵 Sử dụng giọng nói có sẵn")
    
    # [UX] Nếu có giọng mẫu xịn -> Chọn nó (index 0). 
    # Nếu không có -> Mặc định chọn cái đầu tiên còn lại (Thu âm) để không bị lỗi UI
    default_index = None

    voice_method = st.radio("Chọn cách nhập giọng đọc:", 
                            voice_options, 
                            index=default_index,  # <-- Sửa chỗ này
                            horizontal=True,
                            key="radio_voice_method")
    
    final_audio_link_to_send = None 
    
    # CHỈ HIỆN CÔNG CỤ KHI ĐÃ CHỌN RADIO
    if voice_method:
        st.markdown("---") # Đường kẻ ngăn cách nhẹ cho đẹp
        
        # CASE 1: DÙNG GIỌNG MẪU
        if voice_method == "🎵 Sử dụng giọng nói có sẵn":
            # [FIX] Đã kiểm tra link ở trên rồi, nên ở đây cứ thế mà hiện Player thôi
            st.info("✅ Đang sử dụng giọng đọc từ kho.")
            
            # Buộc hiển thị Audio Player
            st.audio(selected_library_audio, format="audio/mp3")
            
            # Gán link để gửi đi
            final_audio_link_to_send = selected_library_audio
                
        # CASE 2: UPLOAD FILE
        elif voice_method == "📤 Tải file lên":
            st.markdown("<b>Chọn file ghi âm từ máy của bạn (mp3, wav, m4a):</b>", unsafe_allow_html=True)
            uploaded_file = st.file_uploader("", type=['mp3', 'wav', 'm4a'], label_visibility="collapsed")
            
            # [MỚI] Thêm ô tick chọn giọng AI
            st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
            is_ai_checked = st.checkbox("NHỚ TÍCH CHỌN NẾU UPLOAD GIỌNG AI", 
                                      help="Tích vào đây nếu file này tạo từ AI (ElevenLabs, Vbee...) để hệ thống KHÔNG lọc ồn, tránh làm méo giọng.",
                                      key="chk_ai_upload_flag")

            if uploaded_file:
                # [BẢO MẬT] Cấu hình giới hạn
                MAX_FILE_SIZE = 10 * 1024 * 1024 # 10MB
                VALID_EXTS = ['mp3', 'wav', 'm4a', 'ogg', 'aac'] # Danh sách đuôi file cho phép
                
                # Lấy đuôi file (ví dụ: "nhac.mp3" -> "mp3")
                file_ext = uploaded_file.name.split('.')[-1].lower() if '.' in uploaded_file.name else ''

                # 1. Kiểm tra loại file trước (Quan trọng)
                if file_ext not in VALID_EXTS:
                    st.error(f"❌ Định dạng '{file_ext}' không hợp lệ! Chỉ chấp nhận: 'mp3', 'wav', 'm4a', 'ogg', 'aac'")
                    st.session_state['temp_upload_file'] = None # Xóa ngay lập tức
                
                # 2. Kiểm tra kích thước file
                elif uploaded_file.size > MAX_FILE_SIZE:
                    st.error("⚠️ File quá lớn! Vui lòng chọn file dưới 10MB.")
                    st.session_state['temp_upload_file'] = None
                
                # 3. Hợp lệ -> Lưu vào session
                else:
                    st.session_state['temp_upload_file'] = uploaded_file
                    st.session_state['temp_upload_name'] = uploaded_file.name
                    st.success(f"✅ Đã chọn: {uploaded_file.name}")

        # CASE 3: THU ÂM TRỰC TIẾP (GIAO DIỆN MÁY NHẮC CHỮ - ĐÃ SỬA KHOẢNG CÁCH)
        elif voice_method == "🎙️ Thu âm trực tiếp": 
            
            # Tạo một khung chứa riêng biệt
            with st.container(border=True):
                st.markdown("<h3 style='text-align: center; color: #D32F2F; margin-bottom: 15px;'>🎙️ PHÒNG THU ÂM</h3>", unsafe_allow_html=True)
                
                # 1. HIỆN KỊCH BẢN ĐỂ ĐỌC
                current_script = st.session_state.get('main_content_area', "")
                
                if not current_script:
                    st.warning("⚠️ Bạn chưa nhập nội dung ở Bước 1. Vui lòng quay lại nhập kịch bản trước khi thu!")
                else:
                    # [ĐÃ SỬA] margin-bottom giảm từ 20px xuống 5px để sát lại gần nút thu âm
                    st.markdown(f"""
                    <div style="
                        background-color: #fff; 
                        color: #000; 
                        padding: 20px; 
                        border-radius: 10px; 
                        border: 2px solid #5D4037; 
                        font-size: 22px; 
                        line-height: 1.6; 
                        max-height: 400px; 
                        overflow-y: auto; 
                        margin-bottom: 10px; 
                        box-shadow: inset 0 0 10px rgba(0,0,0,0.1);
                    ">
                        <b>📝 Kịch bản cần đọc:</b><br><br>
                        {current_script.replace(chr(10), '<br>')}
                    </div>
                    """, unsafe_allow_html=True)

                # [ĐÃ XÓA] Dòng st.markdown("---") ở đây để bỏ khoảng trống thừa

                # 2. BẢNG ĐIỀU KHIỂN THU ÂM
                has_recording = 'temp_record_file' in st.session_state and st.session_state['temp_record_file'] is not None

                if not has_recording:
                    c1, c2 = st.columns([1, 1], vertical_alignment="center") # [MỚI] Căn giữa theo chiều dọc
                    with c1:
                        # [ĐÃ SỬA] Thêm thẻ <br> để xuống dòng và sửa số 3 thành 5 giây
                        st.markdown("""
                        <div style="
                            background-color: #E3F2FD; 
                            padding: 15px; 
                            border-radius: 8px; 
                            color: #0D47A1; 
                            font-size: 20px; 
                            text-align: center;
                            border: 1px solid #90CAF9;
                            line-height: 1.4;
                        ">
                            💡 Giữ im lặng 5 giây đầu<br>để lọc ồn tốt hơn.
                        </div>
                        """, unsafe_allow_html=True)
                    
                    with c2:
                        # [CẬP NHẬT] Thêm hướng dẫn vào nút bấm
                        audio_data = mic_recorder(
                            start_prompt="🔴 BẮT ĐẦU THU ",
                            stop_prompt="⏹️ KẾT THÚC THU)",
                            just_once=True, 
                            use_container_width=True,
                            format="wav", 
                            key="new_mic_recorder_v3"
                        )
                        
                        if audio_data:
                            # [QUAN TRỌNG] Hiện vòng quay xử lý ngay lập tức để người dùng không bấm lung tung
                            with st.spinner("💾 Đang lưu file... Vui lòng KHÔNG bấm gì thêm!"):
                                raw_bytes = audio_data['bytes']
                                # Kiểm tra: Nếu file > 20MB (khoảng 20 phút) thì từ chối
                                if len(raw_bytes) > 20 * 1024 * 1024:
                                    st.error("⚠️ File ghi âm quá dài (>20MB). Vui lòng thu ngắn hơn!")
                                else:
                                    st.session_state['temp_record_file'] = raw_bytes
                                st.session_state['temp_record_name'] = f"record_{datetime.now().strftime('%H%M%S')}.wav"
                                
                                # Ngủ nhẹ 1 giây để đảm bảo session kịp cập nhật trước khi reload trang
                                time.sleep(1) 
                                st.rerun()
                else:
                    # Giao diện sau khi thu xong
                    st.success("✅ Đã thu xong! Hãy nghe lại bên dưới:")
                    st.audio(st.session_state['temp_record_file'], format="audio/wav")
                    
                    col_act1, col_act2 = st.columns(2)
                    with col_act1:
                         if st.button("🔄 Thu lại từ đầu", use_container_width=True, type="secondary"):
                            st.session_state['temp_record_file'] = None
                            st.rerun()
                    with col_act2:
                        st.markdown("""
                        <div style="
                            text-align: center; 
                            font-weight: bold; 
                            color: #2E7D32; 
                            padding: 8px; 
                            border: 1px dashed #2E7D32; 
                            border-radius: 5px;">
                            Nếu hài lòng, bấm GỬI TẠO VIDEO bên dưới!
                        </div>
                        """, unsafe_allow_html=True)
        

        # CASE 4: GIỌNG AI CHẤT LƯỢNG CAO
        elif voice_method == "🤖 Giọng AI Google":
            st.markdown("##### 🔊 Cấu hình giọng đọc Gemini")
            
            # 1. CHỌN VÙNG MIỀN (MỚI)
            c_region, c_voice = st.columns([1, 2])
            with c_region:
                selected_region = st.selectbox(
                    "🌍 Vùng miền:",
                    ["Miền Nam", "Miền Bắc", "Miền Trung"],
                    index=0 # Mặc định miền Nam
                )
            
            # 2. CHỌN CHẤT GIỌNG (10 giọng)
            with c_voice:
                selected_voice_key = st.selectbox(
                    "🗣️ Chất giọng:", 
                    list(GEMINI_STYLES.keys())
                )

            # 3. NGHE THỬ
            st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)
            if st.button("▶️ Nghe thử giọng này", use_container_width=True):
                # Lấy nội dung thực tế
                script_preview = st.session_state.get('main_content_area', "")
                
                with st.spinner(f"Đang tạo mẫu giọng {selected_region} (2 câu đầu)..."):
                    sample_audio = tts_gemini(
                        text=script_preview, 
                        voice_style_key=selected_voice_key, 
                        region=selected_region, 
                        is_test=True
                    )
                    
                    if sample_audio:
                        st.audio(sample_audio, format="audio/wav")
                    else:
                        st.warning("Hệ thống đang bận, vui lòng thử lại sau giây lát.")

            # 4. XÁC NHẬN
            st.markdown("---")
            if st.button("✨ CHỐT DÙNG GIỌNG NÀY", use_container_width=True, type="primary"):
                 # Lưu trọn gói thông tin vào session
                 st.session_state['selected_gemini_voice_key'] = selected_voice_key
                 st.session_state['selected_gemini_region'] = selected_region
                 
                 # Tạo sẵn link mẫu để giả lập quy trình (hoặc để trống chờ bước Gửi)
                 st.success(f"✅ Đã chọn: {selected_voice_key} ({selected_region})")
                 st.info("👇 Bấm nút 'GỬI YÊU CẦU' bên dưới để bắt đầu tạo video!")

            # Lưu ý cho người dùng
            st.info("💡 Mẹo: Gemini sẽ tự động điều chỉnh ngữ điệu miền Nam dựa trên yêu cầu ngầm định của hệ thống.")
              
              

            if 'temp_ai_audio' in st.session_state and st.session_state['temp_ai_audio']:
                st.audio(st.session_state['temp_ai_audio'])
                final_audio_link_to_send = st.session_state['temp_ai_audio']
                st.session_state['chk_ai_upload_flag'] = True



    # --- SETTINGS (CẬP NHẬT: TỰ ĐỘNG LOAD TỪ DATABASE) ---
    st.markdown("---")
    if 's_voice' not in st.session_state:
        # Lấy cài đặt cũ từ database (nếu có)
        # [FIX] Thêm 'or {}' để nếu dữ liệu là None thì đổi thành dict rỗng
        saved_settings = user.get('settings') or {}
        
        # Nếu chưa có cài đặt cũ thì dùng giá trị mặc định
        st.session_state.update({
            # Lúc này saved_settings chắc chắn là Dict, lệnh .get sẽ không lỗi nữa
            "s_clean": saved_settings.get("clean_audio", True),
            "s_voice": saved_settings.get("voice_vol", 1.5),
            "s_music": saved_settings.get("music_vol", 0.2), 
            "s_font": saved_settings.get("font_name", "Agbalumo"),
            "s_size": saved_settings.get("font_size", 110), 
            "s_color": saved_settings.get("text_color", "#FFFFFF"),
            "s_outline": saved_settings.get("outline_color", "#000000"),
            "s_border": saved_settings.get("border_width", 3),
            "s_margin": saved_settings.get("margin_v", 650),
            "s_offset": saved_settings.get("offset_x", 0)
        })
    with st.expander("⚙️ Cài đặt Âm thanh và Phụ đề", expanded=False):
        with st.form("settings_form"):
            c1, c2 = st.columns(2)
            with c1: 
                st.markdown("<b>🔊 Âm thanh</b>", unsafe_allow_html=True)
                st.checkbox("Khử tiếng ồn (Noise reduce)", key="s_clean")
                st.slider("Độ lớn giọng (Voice Vol)", 0.5, 5.0, key="s_voice")
                st.slider("Nhạc nền (Music Vol)", 0.0, 1.0, key="s_music")
            with c2:
                st.markdown("<b>🎨 Hiển thị chữ</b>", unsafe_allow_html=True)
                col_f1, col_f2 = st.columns(2)
                with col_f1: st.selectbox("Font chữ", ["Agbalumo", "Arial", "Times New Roman"], key="s_font")
                with col_f2: st.number_input("Cỡ chữ", 20, 200, key="s_size")
                col_c1, col_c2 = st.columns(2)
                with col_c1: st.color_picker("Màu chữ", key="s_color")
                with col_c2: st.color_picker("Màu viền", key="s_outline")
            st.slider("Độ dày viền", 0, 10, key="s_border")
            st.slider("Vị trí Dọc (Y)", 0, 1500, key="s_margin")
            st.slider("Vị trí Ngang (X)", -500, 500, key="s_offset")
        
            # [ĐÃ SỬA] Thụt vào trong để nút bấm nằm TRONG form
            if st.form_submit_button("💾 LƯU CÀI ĐẶT"):
                # Chuẩn bị dữ liệu để lưu
                current_settings = {
                    "clean_audio": st.session_state.s_clean, "voice_vol": st.session_state.s_voice,
                    "music_vol": st.session_state.s_music, "font_name": st.session_state.s_font,
                    "font_size": st.session_state.s_size, "text_color": st.session_state.s_color,
                    "outline_color": st.session_state.s_outline, "border_width": st.session_state.s_border,
                    "margin_v": st.session_state.s_margin, "offset_x": st.session_state.s_offset
                }
                # Gọi hàm lưu lên Supabase
                if save_user_settings_supabase(user['id'], current_settings):
                    st.toast("Đã lưu cài đặt vào tài khoản! ✅")
                    # Cập nhật lại session để không bị load đè dữ liệu cũ
                    st.session_state['user_info']['settings'] = current_settings
    
    settings = {
        "clean_audio": st.session_state.s_clean, "voice_vol": st.session_state.s_voice,
        "music_vol": st.session_state.s_music, "font_name": st.session_state.s_font,
        "font_size": st.session_state.s_size, "text_color": st.session_state.s_color,
        "outline_color": st.session_state.s_outline, "border_width": st.session_state.s_border,
        "margin_v": st.session_state.s_margin, "offset_x": st.session_state.s_offset
    }

    # --- NÚT GỬI (ĐÃ SỬA ĐỂ CHECK QUOTA) ---
    result_container = st.container()
    
    # Disable nút bấm nếu hết Quota
    if st.button("🚀 GỬI YÊU CẦU TẠO VIDEO", type="primary", use_container_width=True, disabled=is_out_of_quota):
        
        # [NEW] Kiểm tra spam (Chống bấm liên tục)
        # [BẢO MẬT] Kiểm tra Quota thực tế từ DB lần nữa trước khi gọi API tốn tiền
        # Tránh trường hợp Session lưu user['quota_used'] cũ chưa kịp cập nhật
        current_db_user = supabase.table('users').select("quota_used, quota_max").eq('id', user['id']).execute()
        if current_db_user.data:
            real_used = current_db_user.data[0]['quota_used']
            real_max = current_db_user.data[0]['quota_max']
            if real_used >= real_max:
                st.error("⚠️ Hệ thống phát hiện bạn đã hết Quota. Vui lòng nạp thêm!")
                st.stop()

        if not check_rate_limit(user['email']):
            st.error("⚠️ Thao tác quá nhanh! Vui lòng đợi 5 giây giữa mỗi lần gửi.")
            st.stop()
        
        ready_to_send = False
        
        # Logic upload file giữ nguyên
        if voice_method == "🎵 Sử dụng giọng nói có sẵn" and final_audio_link_to_send:
            ready_to_send = True
        elif voice_method == "📤 Tải file lên" and 'temp_upload_file' in st.session_state:
            with st.spinner("Đang tải file lên server..."):
                link = upload_to_catbox(st.session_state['temp_upload_file'], st.session_state['temp_upload_name'])
                if link: final_audio_link_to_send = link; ready_to_send = True
        elif voice_method == "🎙️ Thu âm trực tiếp" and 'temp_record_file' in st.session_state:
            with st.spinner("Đang xử lý bản thu..."):
                link = upload_to_catbox(st.session_state['temp_record_file'], st.session_state['temp_record_name'])
                if link: final_audio_link_to_send = link; ready_to_send = True

        # --- [CẬP NHẬT] GIỚI HẠN ĐỘ DÀI THEO PHƯƠNG THỨC GIỌNG NÓI & GÓI CƯỚC ---
        word_count = len(noi_dung_gui.split())
        
        if voice_method == "🤖 Giọng AI Google":
            # Nếu dùng Gemini: Gói Pro/Huynhde cho 1100 từ, các gói còn lại (Basic/Free) cho 800 từ
            if user.get('plan') in ['pro', 'huynhde']:
                MAX_WORDS = 1100
            else:
                MAX_WORDS = 800
        else:
            # Các phương thức khác (Tự thu âm, Tải file lên, Dùng giọng có sẵn) cho phép đến 2000 từ
            MAX_WORDS = 2000
            
        if not noi_dung_gui:
            st.toast("⚠️ Thiếu nội dung!", icon="⚠️")
        elif word_count > MAX_WORDS:
            st.error(f"⚠️ Nội dung quá dài ({word_count} từ). Gói hiện tại chỉ cho phép tối đa {MAX_WORDS} từ/video. Vui lòng cắt ngắn bớt!")
        elif not ready_to_send: 
            st.toast("⚠️ Thiếu file âm thanh!", icon="⚠️")
        else:
            try:
                gc = get_gspread_client()
                ws = gc.open(DB_SHEET_NAME).worksheet(DB_WORKSHEET)
                
                # 1. Lấy thời gian hiện tại
                now_vn = datetime.utcnow() + timedelta(hours=7)
                order_id = now_vn.strftime("%Y%m%d_%H%M%S")
                
                # --- [NEW] CƠ CHẾ CHỐNG TRÙNG ID (TIME SLIDING) ---
                try:
                    # Lấy toàn bộ cột ID hiện có để check (nhanh hơn dùng find nhiều lần)
                    existing_ids = ws.col_values(1) 
                    
                    # Nếu ID này đã có người xí chỗ, tự động lùi lại 1 giây cho đến khi hết trùng
                    while order_id in existing_ids:
                        now_vn += timedelta(seconds=1) # Cộng thêm 1 giây
                        order_id = now_vn.strftime("%Y%m%d_%H%M%S") # Tạo lại ID mới
                except:
                    # Trường hợp sheet mới tinh chưa có dòng nào thì bỏ qua lỗi
                    pass
                
                # Cập nhật lại timestamp theo cái ID chốt cuối cùng
                timestamp = now_vn.strftime("%Y-%m-%d %H:%M:%S")
                # ----------------------------------------------------
                # GHI VÀO SUPABASE
                safe_noidung = sanitize_input(noi_dung_gui)
                
                # [MỚI] Cập nhật settings nếu người dùng chọn giọng AI (Upload hoặc Thư viện)
                
                # CASE 1: Upload file và có tích chọn "Là giọng AI"
                if voice_method == "📤 Tải file lên" and st.session_state.get("chk_ai_upload_flag"):
                    settings['is_ai_voice'] = True
                    settings['clean_audio'] = False # Tắt lọc ồn để tránh méo tiếng
                
                # CASE 2: Dùng giọng thư viện (Mặc định luôn là AI) -> THÊM ĐOẠN NÀY
                elif voice_method == "🎵 Sử dụng giọng nói có sẵn":
                    settings['is_ai_voice'] = True
                    settings['clean_audio'] = False 
                    # [FIX] Đảm bảo volume đủ lớn
                    if float(settings.get('voice_vol', 1.0)) < 1.0:
                        settings['voice_vol'] = 1.5

                # [NEW] CASE 3: Dùng giọng Gemini (Tự tạo)
                elif voice_method == "🤖 Giọng AI Google":
                    # Lấy thông tin từ session (đã lưu ở bước Nghe thử/Chốt)
                    voice_key = st.session_state.get('selected_gemini_voice_key', "Nam 1 - Trầm Ấm (Charon)")
                    region_val = st.session_state.get('selected_gemini_region', "Miền Nam")
                    
                    with st.spinner(f"🤖 Đang tạo giọng đọc {region_val} dài {len(noi_dung_gui.split())} từ..."):
                        # Gọi hàm tạo giọng thật (is_test=False)
                        ai_link = tts_gemini(noi_dung_gui, voice_style_key=voice_key, region=region_val, is_test=False)
                        
                        if ai_link:
                            final_audio_link_to_send = ai_link
                            ready_to_send = True
                            
                            # Cài đặt cho giọng AI
                            settings['is_ai_voice'] = True
                            settings['clean_audio'] = False # Không lọc ồn
                            
                            # Lưu thông tin giọng vào settings để sau này xem lại
                            settings['voice_info'] = f"Gemini - {region_val} - {voice_key}"
                        else:
                            st.error("❌ Không tạo được giọng đọc. Vui lòng thử lại!")
                            st.stop()
                    
                order_data = {
                    "id": order_id,
                    "created_at": datetime.utcnow().isoformat(),
                    "email": user['email'],
                    "source": source_opt,
                    "content": safe_noidung,
                    "audio_link": final_audio_link_to_send,
                    "status": "Pending",
                    "result_link": "",
                    "settings": settings 
                }
                
                # Insert vào bảng orders
                supabase.table('orders').insert(order_data).execute()

                # --- GIẢI PHÓNG RAM NGAY LẬP TỨC ---
                # Xóa dữ liệu file nặng sau khi đã gửi lên Cloudinary và lưu DB thành công
                if 'temp_record_file' in st.session_state:
                    st.session_state['temp_record_file'] = None
                if 'temp_upload_file' in st.session_state:
                    st.session_state['temp_upload_file'] = None
                # ----------------------------------
                
                # [NEW] Trừ Quota (Đã chuyển sang Supabase)
                # update_user_usage_supabase đã được định nghĩa ở đầu file
                update_user_usage_supabase(user['id'], user['quota_used'])
                
                # Cập nhật session ngay lập tức
                st.session_state['user_info']['quota_used'] += 1
                st.session_state['submitted_order_id'] = order_id 
                
                # [MOI] Xóa cache lịch sử cũ & Bật thông báo chờ
                st.session_state['show_wait_message'] = True
                
                st.success(f"✅ ĐÃ GỬI THÀNH CÔNG! Mã đơn: {order_id}")
                st.balloons()
                st.rerun() # Refresh lại để cập nhật số quota trên giao diện
                
            except Exception as e: st.error(f"Lỗi hệ thống: {e}")

    # --- KIỂM TRA KẾT QUẢ (Giữ nguyên, chỉ thêm chút style nếu cần) ---
    

    # ==========================================
    # [NEW] LỊCH SỬ VIDEO (LẤY TỪ ORDERS) - [OPTIMIZED LAZY LOAD]
    # ==========================================
    st.markdown("---")
    
    # [FIX] Lấy dữ liệu lịch sử ngay lập tức để kiểm tra trạng thái thực tế
    history_df = get_user_history(user['email'])
    
    # Logic kiểm tra thông minh: Chỉ hiện thông báo nếu CÓ video đang Pending hoặc Processing
    is_processing_real = False
    if not history_df.empty and 'TrangThai' in history_df.columns:
        # Kiểm tra trong 5 đơn mới nhất xem có đơn nào chưa xong không
        check_pending = history_df.head(5)[history_df.head(5)['TrangThai'].isin(['Pending', 'Processing'])]
        if not check_pending.empty:
            is_processing_real = True

    # Chỉ hiển thị thông báo khi thực sự có video đang chạy
    if is_processing_real:
        st.markdown("""
        <div style="background-color: #FFF9C4; color: #5D4037; padding: 15px; border-radius: 10px; border: 1px solid #FBC02D; margin-bottom: 20px; font-weight: bold;">
            ⏳ Đang tạo video. Vui lòng quay lại sau 5 phút và bấm nút "Xem danh sách video" hoặc nút "Làm mới"!
        </div>
        """, unsafe_allow_html=True)

    # Khởi tạo trạng thái
    if 'show_history_section' not in st.session_state:
        st.session_state['show_history_section'] = False

    # --- TRƯỜNG HỢP 1: CHƯA BẤM XEM (ẨN) ---
    if not st.session_state['show_history_section']:
        if st.button("📂 Xem danh sách video", use_container_width=True):
            st.session_state['show_history_section'] = True
            st.rerun()
            
    # --- TRƯỜNG HỢP 2: ĐÃ BẤM XEM (HIỆN) ---
    else:
        # 1. Header & Nút Làm mới
        c_hist1, c_hist2 = st.columns([3, 1], vertical_alignment="center")
        with c_hist1:
            st.subheader("📜 Video của bạn")
        with c_hist2:
            if st.button("🔄 Làm mới", help="Cập nhật danh sách mới nhất"):
                # get_all_orders_cached.clear() <-- ĐÃ TẮT DÒNG NÀY
                st.rerun()
        
        # 2. Lấy dữ liệu
        history_df = get_user_history(user['email'])
        
        # 3. Hiển thị danh sách
        if not history_df.empty:
            status_map = {
                "Pending": "⏳ Đang chờ xử lý", "Processing": "⚙️ Đang tạo video...",
                "Done": "✅ Hoàn thành - Bấm xem", "Error": "❌ Gặp lỗi", "": "❓ Chưa xác định"
            }
            
            # Logic phân trang (Xem thêm / Thu gọn)
            MAX_ITEMS = 3
            if 'history_expanded' not in st.session_state: st.session_state['history_expanded'] = False
            
            # Cắt danh sách tùy theo trạng thái
            df_display = history_df if st.session_state['history_expanded'] else history_df.head(MAX_ITEMS)
            total_items = len(history_df)

            # Vòng lặp hiển thị từng video
            for index, row in df_display.iterrows():
                # Lấy thông tin an toàn
                date_str = row.get('NgayTao', '')
                result_link = row.get('LinkKetQua', '')
                raw_status = row.get('TrangThai', 'Pending')
                order_id = row.get('ID', f'id_{index}')
                old_audio_link = row.get('LinkGiongNoi', '')
                old_content_script = row.get('NoiDung', '')

                # Tạo trích dẫn ngắn
                try:
                    # Giải mã HTML trước khi hiển thị trích dẫn để người dùng đọc được ký tự gốc
                    decoded_content = html.unescape(str(old_content_script))
                    words = decoded_content.split()
                    script_preview = " ".join(words[:10]) + "..." if len(words) > 10 else decoded_content
                except: script_preview = ""

                # Format ngày & Trạng thái (Đã sửa lỗi lệch múi giờ Việt Nam)
                try:
                    # Chuyển chuỗi chữ thành định dạng thời gian
                    dt_obj = pd.to_datetime(date_str)
                    
                    # Nếu thời gian chưa có múi giờ, ta gán cho nó là UTC, sau đó chuyển sang giờ VN (+7)
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.tz_localize('UTC').tz_convert('Asia/Ho_Chi_Minh')
                    else:
                        dt_obj = dt_obj.tz_convert('Asia/Ho_Chi_Minh')
                        
                    display_date = dt_obj.strftime('%d/%m/%Y - %H:%M')
                except Exception as e:
                    display_date = str(date_str)
                vn_status = status_map.get(raw_status, raw_status)

                # HIỂN THỊ EXPANDER
                with st.expander(f"{display_date} | {vn_status} | 📝 {script_preview}"):
                    # A. Nếu có link kết quả -> Hiện nút Xem & Tải
                    # [FIX] Kiểm tra độ dài thay vì bắt buộc phải có http ngay từ đầu
                    if result_link and len(str(result_link)) > 5:
                        # Tự động thêm https:// nếu link trong database bị thiếu
                        if not str(result_link).startswith("http"):
                            result_link = f"https://{result_link}"

                        # Fix link tải cho iOS
                        dl_link = result_link.replace("/upload/", "/upload/fl_attachment/") if "cloudinary" in str(result_link) else result_link
                        
                        col_btn1, col_btn2 = st.columns([1, 1], gap="small")
                        btn_style = "width: 100%; padding: 10px; border-radius: 8px; text-align: center; font-weight: bold; text-decoration: none; display: block; box-shadow: 0 2px 3px rgba(0,0,0,0.1);"
                        
                        with col_btn1:
                            st.markdown(f'<a href="{result_link}" target="_blank" style="{btn_style} background-color: #8D6E63; color: white;">▶️ XEM VIDEO</a>', unsafe_allow_html=True)
                        with col_btn2:
                            # --- PHƯƠNG ÁN SIÊU NHẸ: DIRECT LINK (ZERO RAM) ---
                            
                            # 1. Lấy link gốc
                            direct_dl_link = dl_link
                            
                            # 2. [CLOUDINARY] Thêm 'fl_attachment' để ép tải về
                            # Cloudinary hỗ trợ cái này mặc định, rất ngon.
                            if "cloudinary" in str(direct_dl_link):
                                direct_dl_link = direct_dl_link.replace("/upload/", "/upload/fl_attachment/")
                            
                            # 3. [BUNNY CDN]
                            # [FIX] Đổi sang download=2 để né cache cũ trên điện thoại người dùng
                            elif "b-cdn.net" in str(direct_dl_link):
                                if "?" in direct_dl_link:
                                    direct_dl_link += "&download=2" # <-- Sửa số 1 thành 2
                                else:
                                    direct_dl_link += "?download=2" # <-- Sửa số 1 thành 2

                            # 4. HIỆN NÚT BẤM HTML THÔNG MINH (Tự đóng tab trên điện thoại)
                            # Sử dụng JavaScript để kích hoạt tải về mà không để lại tab thừa
                            download_script = f"""
                            <a href="{direct_dl_link}" 
                               onclick="setTimeout(function(){{ window.close(); }}, 500);" 
                               target="_blank" 
                               rel="noopener noreferrer" 
                               download
                               style="{btn_style} background-color: #2E7D32; color: white; border: 1px solid #1B5E20; text-decoration: none; display: block; text-align: center;">
                                📥 TẢI VIDEO
                            </a>
                            """
                            st.markdown(download_script, unsafe_allow_html=True)
                    
                    elif raw_status == "Error":
                        st.error("Video này bị lỗi xử lý.")
                    else:
                        st.info("Hệ thống đang xử lý...")

                    # B. Nút Tạo lại (Re-create)
                    st.markdown('<div style="margin-top: 5px;"></div>', unsafe_allow_html=True) 
                    if old_audio_link and str(old_audio_link).startswith("http"):
                        # [FIX] Thêm _{index} vào key để đảm bảo không bao giờ bị trùng
                        if st.button(f"♻️ Tạo lại bằng giọng nói này", key=f"recreate_{order_id}_{index}", disabled=is_out_of_quota, use_container_width=True):
                            if not is_out_of_quota:
                                try:
                                    with st.spinner("Đang gửi lệnh tạo lại..."):
                                        # 1. Tạo ID mới
                                        now_vn = datetime.utcnow() + timedelta(hours=7)
                                        new_id = now_vn.strftime("%Y%m%d_%H%M%S")
                                        
                                        # 2. Chuẩn bị dữ liệu cho Supabase
                                        order_data = {
                                            "id": new_id,
                                            "created_at": datetime.utcnow().isoformat(),
                                            "email": user['email'],
                                            "source": "Re-created",
                                            "content": old_content_script, # Dùng lại nội dung cũ
                                            "audio_link": old_audio_link,  # Dùng lại link audio cũ
                                            "status": "Pending",
                                            "result_link": "",
                                            "settings": settings 
                                        }
                                        
                                        # 3. Gửi vào Supabase
                                        supabase.table('orders').insert(order_data).execute()
                                        
                                        # 4. Cập nhật Quota (Trừ lượt dùng)
                                        update_user_usage_supabase(user['id'], user['quota_used'])
                                        
                                        # Log & Update Quota
                                        # [FIX] Chỉ log lịch sử, bỏ qua việc update row sheet cũ vì không còn biến row
                                        log_history(new_id, user['email'], "", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
                                        # update_user_usage(user['row'], user['quota_used']) <--- DÒNG NÀY GÂY LỖI NÊN ĐÃ BỊ XÓA/COMMENT
                                        
                                        st.session_state['user_info']['quota_used'] += 1
                                        # get_all_orders_cached.clear() <-- ĐÃ TẮT DÒNG NÀY
                                        st.session_state['show_wait_message'] = True
                                        st.success("✅ Đã gửi lệnh tạo lại!")
                                        st.rerun()
                                except Exception as e: st.error(f"Lỗi: {e}")

            # 4. Nút Xem thêm / Thu gọn
            if total_items > MAX_ITEMS:
                st.markdown("---")
                col_c = st.columns([1, 2, 1])[1]
                with col_c:
                    if not st.session_state['history_expanded']:
                        if st.button(f"🔽 Xem thêm ({total_items - MAX_ITEMS} video cũ)", use_container_width=True):
                            st.session_state['history_expanded'] = True
                            st.rerun()
                    else:
                        if st.button("🔼 Thu gọn danh sách", use_container_width=True):
                            st.session_state['history_expanded'] = False
                            st.rerun()
        else:
            st.info("Bạn chưa có video nào.")

        # 5. Nút Đóng danh sách
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("❌ Đóng lại", use_container_width=True):
            st.session_state['show_history_section'] = False
            st.rerun()
