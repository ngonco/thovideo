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


# --- DANH SÁCH GIỌNG VIENEU-TTS ---
# Lưu ý: Tên bên phải (Value) phải KHỚP CHÍNH XÁC với tên trong Dropdown của phần mềm trên máy bạn
VIENEU_VOICES = [
    "Ly (nữ miền Bắc)",
    "Bình (nam miền Bắc)",
    "Ngọc (nữ miền Bắc)",
    "Tuyên (nam miền Bắc)",
    "Vĩnh (nam miền Nam)",
    "Đoan (nữ miền Nam)"
]

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

# --- [NEW] RATE LIMIT (CHỐNG SPAM) BẰNG DATABASE ---
def check_rate_limit(user_email):
    try:
        # 1. BẢO VỆ LỚP 1: KIỂM TRA TRÊN DATABASE (Không thể lách luật)
        # Lấy thời gian của video gần nhất mà user này vừa bấm tạo
        res = supabase.table('orders').select('created_at').eq('email', user_email).order('created_at', desc=True).limit(1).execute()
        
        if res.data and len(res.data) > 0:
            last_created_str = res.data[0]['created_at']
            
            # Chuyển đổi thời gian từ hệ thống Supabase sang thời gian thực tế để làm toán
            last_time = pd.to_datetime(last_created_str).tz_localize(None)
            now_time = datetime.utcnow()
            
            # Tính khoảng cách bằng giây
            diff_seconds = (now_time - last_time).total_seconds()
            
            # Nếu chưa qua 5 giây -> Chặn ngay lập tức
            if diff_seconds < 5:
                return False
                
    except Exception as e:
        print(f"Lỗi hệ thống chống Spam DB: {e}")

    # 2. BẢO VỆ LỚP 2: KIỂM TRA TRÊN TRÌNH DUYỆT (Giữ nguyên như cũ để phòng hờ)
    last_req_key = f"last_req_{user_email}"
    current_time = time.time()
    
    if last_req_key in st.session_state:
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
    
    # 3. Mã hóa HTML (Chống XSS) - quote=False để GIỮ NGUYÊN dấu " cho kịch bản
    return html.escape(text, quote=False)

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


# --- [NEW] QUẢN LÝ GIỚI HẠN TTS GEMINI ---

def check_tts_quota(user_data, text_to_speak):
    """Kiểm tra xem user còn đủ hạn mức để đọc đoạn văn này không"""
    if not text_to_speak: return True, 0
    
    # Tính số ký tự của đoạn văn
    char_count = len(text_to_speak)
    
    # Lấy thông tin từ user (xử lý trường hợp chưa có cột trong DB cũ)
    current_usage = user_data.get('tts_usage') or 0
    max_limit = user_data.get('tts_limit') or 10000 # Mặc định 10k nếu lỗi
    
    if current_usage + char_count > max_limit:
        remaining_chars = max_limit - current_usage
        remaining_mins = round(remaining_chars / 1000, 1)
        return False, f"⚠️ Bạn đã hết thời lượng AI. Còn lại: {max(0, remaining_mins)} phút. Đoạn văn này cần {round(char_count/1000, 1)} phút."
    
    return True, char_count

def update_tts_usage_supabase(user_id, added_chars):
    """Cộng dồn số ký tự đã dùng vào Database"""
    try:
        # Lấy số liệu mới nhất từ DB để cộng cho chính xác (tránh race condition)
        res = supabase.table('users').select("tts_usage").eq('id', user_id).execute()
        if res.data:
            current_val = res.data[0]['tts_usage'] or 0
            new_val = current_val + added_chars
            supabase.table('users').update({"tts_usage": new_val}).eq('id', user_id).execute()
            return new_val
    except Exception as e:
        print(f"Lỗi update TTS: {e}")
    return None


def create_order_logic(user, status, audio_link, content, settings):
    import random
    try:
        # 1. Kiểm tra Quota (Nếu là tạo video)
        if status == "Pending":
            if user['quota_used'] >= user['quota_max']:
                st.error("⚠️ Bạn đã hết lượt tạo video!")
                return

        # 2. Tạo ID đơn hàng
        now_vn = datetime.utcnow() + timedelta(hours=7)
        random_suffix = random.randint(100, 999)
        order_id = now_vn.strftime("%Y%m%d_%H%M%S") + f"_{random_suffix}"
        
        # 3. Chuẩn bị dữ liệu
        # Nếu chỉ lưu giọng, ta dùng settings hiện tại nhưng đánh dấu
        final_settings = settings.copy()
        final_settings['is_voice_only'] = (status == "VoiceOnly")

        order_data = {
            "id": order_id,
            "created_at": datetime.utcnow().isoformat(),
            "email": user['email'],
            "source": "AI Gen",
            "content": sanitize_input(content),
            "audio_link": audio_link,
            "status": status, # Pending hoặc VoiceOnly
            "result_link": "",
            "settings": final_settings
        }

        # 4. Gửi lên Supabase
        supabase.table('orders').insert(order_data).execute()

        # 5. Xử lý sau khi lưu
        if status == "Pending":
            # Trừ quota video
            update_user_usage_supabase(user['id'], user['quota_used'])
            st.session_state['user_info']['quota_used'] += 1
            st.success(f"✅ Đã gửi yêu cầu tạo video! (Mã: {order_id})")
        else:
            st.toast("✅ Đã lưu bản thu vào lịch sử!", icon="💾")
            st.success("Đã lưu giọng nói. Bạn có thể xem lại trong phần 'Danh sách video'.")

        # Reload để cập nhật lịch sử
        time.sleep(1.5)
        st.rerun()

    except Exception as e:
        st.error(f"Lỗi lưu dữ liệu: {e}")

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
                if user_data.get('tts_usage') is None: user_data['tts_usage'] = 0
                if user_data.get('tts_limit') is None: user_data['tts_limit'] = 10000 # Mặc định Free 10 phút
                
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

# --- [NEW] HÀM QUẢN LÝ LỊCH SỬ TTS (ĐỂ KHÔNG MẤT KHI F5) ---
def save_tts_log(email, content, audio_link, voice_info):
    try:
        data = {
            "email": email,
            "content": content,
            "audio_link": audio_link,
            "voice_info": voice_info
        }
        supabase.table('tts_logs').insert(data).execute()
    except Exception as e:
        print(f"Lỗi lưu TTS log: {e}")

# --- [NEW] HÀM DỌN DẸP LOGS CŨ (TỰ ĐỘNG) ---
def cleanup_old_tts_logs(days=7):
    try:
        # 1. Tính mốc thời gian (Hiện tại trừ đi 7 ngày)
        threshold_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        # 2. Lệnh xóa các bản ghi có ngày tạo nhỏ hơn (cũ hơn) mốc trên
        supabase.table('tts_logs').delete().lt('created_at', threshold_date).execute()
        return True
    except Exception as e:
        print(f"Lỗi dọn dẹp logs: {e}")
        return False

def get_latest_tts_log(email):
    try:
        # Lấy file âm thanh mới nhất của user này
        response = supabase.table('tts_logs').select("*").eq('email', email).order('created_at', desc=True).limit(1).execute()
        if response.data:
            return response.data[0]
    except Exception as e:
        print(f"Lỗi tải TTS log: {e}")
    return None

def get_pending_local_ai_request(email, content):
    """Hàm tự động tìm lại yêu cầu TTS đang chạy ngầm nếu user bấm F5"""
    try:
        # Lấy yêu cầu mới nhất của user
        res = supabase.table('tts_requests').select("id, status, content").eq('email', email).order('created_at', desc=True).limit(1).execute()
        if res.data:
            req = res.data[0]
            # Nếu đang chờ/đang xử lý VÀ nội dung trùng khớp với trên màn hình
            if req['status'] in ['pending', 'processing'] and req['content'] == sanitize_input(content):
                return req['id']
    except Exception as e:
        print(f"Lỗi check request: {e}")
    return None

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
        background-color: transparent !important; /* Nền trong suốt */
    }}
    
    /* [MỚI] XÓA LỀ NỘI DUNG BÊN TRONG EXPANDER */
    div[data-testid="stExpander"] div[role="group"] {{
        padding: 0px !important; /* Xóa khoảng trắng bao quanh nội dung */
        gap: 5px !important;    /* Giảm khoảng cách giữa các phần tử con */
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
        import uuid
        # Luôn tạo tên mới ngẫu nhiên để tránh lỗi bảo mật tên file
        ext = "wav"
        if hasattr(file_obj, "name") and "." in file_obj.name:
            ext = file_obj.name.split(".")[-1]
        
        filename = f"{uuid.uuid4()}.{ext}"
            
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

# --- [NEW] CẤU HÌNH GIỌNG ĐỌC GEMINI (ĐÃ RÚT GỌN: HÀ NỘI TRẦM ẤM) ---
GEMINI_STYLES = {
    "Nam Hà Nội - Trầm Ấm": {
        "id": "Charon", 
        "style": "hãy đọc bằng giọng nam Miền Bắc (Hà Nội), tông giọng trầm, dày, ấm áp, chậm rãi và truyền cảm"
    },
    "Nữ Hà Nội - Dịu Dàng": {
        "id": "Aoede",  
        "style": "hãy đọc bằng giọng nữ Miền Bắc (Hà Nội), tông giọng trầm ấm, nhẹ nhàng, như đang tâm sự thủ thỉ"
    }
}






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
    
    # --- CẤU HÌNH CÁC GÓI CƯỚC CHUẨN (Dùng chung cho cả Tab 1 và Tab 3) ---
    # Tại đây quy định số video và mã code cho từng gói
    # --- CẤU HÌNH GÓI CƯỚC & GIỚI HẠN TTS ---
    # Quy ước: 1 phút giọng đọc ≈ 1000 ký tự (đã bao gồm khoảng nghỉ)
    # [ĐÃ SỬA] Thêm trường "code" và đổi tên "video_quota" thành "quota_per_month" để khớp logic tính toán
    PLAN_CONFIG = {
        "free":     {"name": "Free",     "code": "free",    "quota_per_month": 10, "tts_chars": 10000}, 
        "basic":    {"name": "Cơ bản",   "code": "basic",   "quota_per_month": 30, "tts_chars": 50000}, 
        "pro":      {"name": "Nâng cao", "code": "pro",     "quota_per_month": 60, "tts_chars": 150000}, 
        "huynhde":  {"name": "Huynh Đệ", "code": "huynhde", "quota_per_month": 60, "tts_chars": 150000}, 
    }
    # Mapping tên hiển thị cũ sang code mới để tương thích ngược
    PLAN_NAME_MAP = {
        "Free (Miễn phí)": "free", "Gói 30k (Cơ bản)": "basic", 
        "Gói 60k (Nâng cao)": "pro", "Gói huynh đệ": "huynhde"
    }

    with tab1:
        st.subheader("Tạo tài khoản & Gia hạn")
        
        # (Đã xóa khai báo trùng lặp ở đây để tránh lỗi logic)
        
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
        # [MỚI] Tính tổng TTS = (TTS tháng) x (Số tháng)
        calculated_tts = plan_info["tts_chars"] * months
        
        # Tính ngày hết hạn
        expiry_date = datetime.utcnow() + timedelta(days=30 * months)
        expiry_str = expiry_date.strftime("%d/%m/%Y")

        # Hiển thị thông tin review
        st.success(f"""
        📊 **Review Cấu hình:**
        - Gói: **{plan_info['code'].upper()}**
        - Thời hạn: **{months} tháng** (Hết hạn: {expiry_str})
        """)
        
        # [FIX] Tạo key động dựa trên tên gói và thời hạn để auto-reload giá trị
        dynamic_key = f"{selected_plan_name}_{selected_duration_name}"

        # CHIA 2 CỘT ĐỂ NHẬP LIỆU
        col_inp1, col_inp2 = st.columns(2)
        with col_inp1:
            final_quota = st.number_input("Tổng Video (Quota Max)", 
                                        value=calculated_quota, min_value=0, step=1,
                                        key=f"quota_{dynamic_key}")
        with col_inp2:
            final_tts = st.number_input("Tổng TTS (Ký tự)", 
                                        value=calculated_tts, min_value=0, step=5000,
                                        key=f"tts_{dynamic_key}",
                                        help="1 phút đọc ≈ 1000 ký tự")
        
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
                        "plan": plan_info['code'], # Lưu mã code (free, basic...) thay vì tên hiển thị
                        "quota_max": final_quota,
                        "quota_used": 0,
                        # [FIX] Lưu giá trị TTS từ ô nhập liệu (đã nhân theo tháng)
                        "tts_limit": final_tts,
                        "tts_usage": 0, 
                        "role": "user",
                        "stock_level": 1000
                    }
                    
                    supabase.table('users').insert(data).execute()
                    st.success(f"✅ Đã tạo tài khoản thành công: {new_email} | Video: {final_quota} | TTS: {final_tts}")
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
            
            # 2. Lấy số liệu mặc định của gói
            suggested_quota = PLAN_CONFIG[selected_plan_name]["quota_per_month"]
            suggested_tts = PLAN_CONFIG[selected_plan_name]["tts_chars"]
            
            # 3. Ô nhập số (Sẽ tự đổi giá trị value theo gói)
            c_edit1, c_edit2 = st.columns(2)
            with c_edit1:
                final_quota_edit = st.number_input("Tổng Video (Quota Max)", value=suggested_quota, step=1)
            with c_edit2:
                final_tts_edit = st.number_input("Tổng TTS (Ký tự)", value=suggested_tts, step=1000)
            
            st.caption(f"ℹ️ Gói **{selected_plan_name}** tương ứng **{suggested_quota}** video.")

            # Nút lưu
            if st.button("💾 LƯU THAY ĐỔI NGAY", type="primary"):
                try:
                    plan_code = PLAN_CONFIG[selected_plan_name]["code"]
                    
                    # Cập nhật vào Supabase
                    supabase.table('users').update({
                        "plan": plan_code,
                        "quota_max": final_quota_edit,
                        "tts_limit": final_tts_edit # [MỚI] Cập nhật TTS limit
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
        background-color: #FFF8DC !important; 
        color: #3E2723 !important; 
        caret-color: #8B4513 !important; 
        font-weight: normal !important; /* Đã sửa: Trả chữ về nét mỏng bình thường */
        border: 2px solid #8B4513 !important; 
        font-size: 19px !important; 
        line-height: 1.5 !important; 
    }
    /* MỚI: Làm mờ chữ gợi ý (placeholder) để phân biệt rõ với chữ thật do user gõ */
    .stTextArea textarea::placeholder {
        color: #A1887F !important; 
        font-weight: normal !important;
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
            <div class="intro-item">🍃 Chuyên nội dung đạo lý</div>
            <div class="intro-item">🍃 AI lựa chọn video phù hợp nội dung</div>
            <div class="intro-item">🍃 Nhiều lựa chọn về giọng nói</div>
            <div class="intro-item">🍃 Và nhiều tính năng đang phát triển</div>
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

    # --- [FIX LỖI F5] TỰ ĐỘNG KHÔI PHỤC TTS GẦN NHẤT ---
    if 'gemini_full_audio_link' not in st.session_state:
        with st.spinner("Đang kiểm tra dữ liệu cũ..."):
            last_tts = get_latest_tts_log(user['email'])
            if last_tts:
                # Lấy bản nháp mới nhất từ DB để so sánh
                latest_draft = load_draft_from_supabase(user['email'])
                
                # So sánh (xóa khoảng trắng và xuống dòng để chính xác tuyệt đối)
                content_in_db = str(last_tts.get('content', '')).strip()
                current_draft = str(latest_draft).strip()
                
                if content_in_db == current_draft and current_draft != "":
                    st.session_state['gemini_full_audio_link'] = last_tts['audio_link']
                    st.session_state['gemini_voice_info'] = last_tts['voice_info']
                    st.session_state['main_content_area'] = latest_draft # Đảm bảo kịch bản cũng được load
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
    st.markdown(f"<h1 style='text-align: center; border: none; margin: 0; padding: 0;'>hạt bụi nhỏ làm video siêu dễ</h1>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True) # Tạo khoảng cách nhỏ
    
    
    # Tính toán quota
    quota_left = user['quota_max'] - user['quota_used']
    is_out_of_quota = quota_left <= 0

    # [LOGIC TÍNH NGÀY] (Giữ nguyên logic tính toán)
    try:
        created_at_raw = user.get('created_at')
        if created_at_raw:
            created_date = pd.to_datetime(created_at_raw)
            expiry_date = created_date + timedelta(days=30) # Giả định gói 30 ngày
            now_date = pd.Timestamp.now(tz=created_date.tz)
            
            days_left = (expiry_date - now_date).days
            days_display = max(0, days_left)
        else:
            days_display = "?"
    except Exception as e:
        days_display = "?"

    # Hiển thị thanh trạng thái Quota (Giao diện thẻ bài)
    st.markdown(f"""
    <div style="background-color: #FFF8DC; border: 2px dashed #8B4513; padding: 15px; border-radius: 10px; margin-bottom: 25px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <span style="font-size: 18px; font-weight: bold; color: #3E2723;">👤 {user['email']}</span><br>
                <span style="font-size: 14px; color: #6D4C41;">🏷️ Gói: <b>{user['plan']}</b></span>
            </div>
            <div style="text-align: right;">
                <span style="font-size: 22px; color: {'#D32F2F' if is_out_of_quota else '#2E7D32'}; font-weight: bold;">
                    {user['quota_used']}/{user['quota_max']} video
                </span><br>
                <span style="color: #6D4C41; font-size: 16px; font-weight: regular;">(Còn lại: {days_display} ngày)</span>
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

        # --- (B1) NGUỒN KỊCH BẢN (GIAO DIỆN EXPANDER) ---
        # [ĐÃ SỬA] Đổi expanded=False để mặc định đóng lại
    with st.expander("1️⃣ BƯỚC 1: CHUẨN BỊ KỊCH BẢN", expanded=False):
            
            # PHIÊN BẢN AN TOÀN: Dùng .get() để tránh lỗi AttributeError khi chưa khởi tạo xong
            settings = {
                "clean_audio": bool(st.session_state.get("s_clean", True)), 
                "voice_vol": float(st.session_state.get("s_voice", 1.5)),
                "music_vol": float(st.session_state.get("s_music", 0.2)), 
                "font_name": str(st.session_state.get("s_font", "Agbalumo")),
                "font_size": int(st.session_state.get("s_size", 110)), 
                "text_color": str(st.session_state.get("s_color", "#FFFFFF")),
                "outline_color": str(st.session_state.get("s_outline", "#000000")), 
                "border_width": int(st.session_state.get("s_border", 3)),
                "margin_v": int(st.session_state.get("s_margin", 650)), 
                "offset_x": int(st.session_state.get("s_offset", 0))
            }
            
            # [ĐÃ SỬA] Thêm label_visibility="collapsed" để ẩn dòng chữ tiêu đề
            source_opt = st.radio("Chọn nguồn kịch bản:",
                            ["📂 Tìm trong Thư viện", "✍️ Tự viết mới"], 
                            index=None, 
                            horizontal=True,
                            label_visibility="collapsed", 
                            key="radio_source_opt")

            # --- [QUAN TRỌNG] ĐƯA CÁC BIẾN VÀ LOGIC VÀO TRONG EXPANDER ---
            # Bạn hãy BÔI ĐEN từ dòng này cho đến hết phần logic Bước 1 (trước khi đến Bước 2) 
            # và nhấn phím TAB để thụt vào trong thẳng hàng với chữ 'source_opt' ở trên
            
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
                            
                            # --- [MỚI] RESET FILE ÂM THANH KHI ĐỔI KỊCH BẢN ---
                            if 'gemini_full_audio_link' in st.session_state: 
                                st.session_state['gemini_full_audio_link'] = None
                            if 'local_ai_audio_link' in st.session_state:
                                st.session_state['local_ai_audio_link'] = None
                            # --------------------------------------------------
                            
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
                
                # --- [MỚI] Hàm xóa âm thanh khi nội dung thay đổi ---
                def clear_audio_cache():
                    if 'gemini_full_audio_link' in st.session_state: 
                        st.session_state['gemini_full_audio_link'] = None
                    if 'local_ai_audio_link' in st.session_state:
                        st.session_state['local_ai_audio_link'] = None

                # Text Area - [ĐÃ SỬA] Thêm on_change=clear_audio_cache
                noi_dung_gui = st.text_area("", height=FIXED_HEIGHT, 
                                            placeholder="Nội dung kịch bản sẽ hiện ở đây...", 
                                            key="main_content_area",
                                            on_change=clear_audio_cache) # <--- Thêm dòng này
                
                # [CHỈNH SỬA] Chỉ hiện các nút Nháp khi đang ở chế độ "Tự viết mới"
                if source_opt == "✍️ Tự viết mới":
                    # [SỬA LỖI UI] Tăng tỷ lệ cột đầu từ 1 lên 1.5 để nút rộng hơn, không bị rớt dòng
                    c_draft1, c_draft2, c_draft3 = st.columns([1.5, 1.5, 4]) 
                    
                    # [SỬA LỖI API] Hàm xử lý riêng cho việc bấm nút (Callback)
                    def load_draft_callback():
                        saved_content = load_draft_from_supabase(user['email'])
                        if saved_content:
                            st.session_state['main_content_area'] = saved_content
                            st.toast("Đã tải lại bản nháp cũ!", icon="📂")
                        else:
                            st.toast("Bạn chưa có bản nháp nào!", icon="⚠️")

                    with c_draft1:
                        if st.button("💾 Lưu nháp", use_container_width=True, key="btn_save_draft"):
                            if noi_dung_gui:
                                if save_draft_to_supabase(user['email'], noi_dung_gui):
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
    
    st.markdown("<br><br>", unsafe_allow_html=True) 

    # [CẬP NHẬT] Gom Bước 2 vào Expander và MẶC ĐỊNH ĐÓNG (expanded=False)
    with st.expander("2️⃣ BƯỚC 2: CHUẨN BỊ GIỌNG ĐỌC", expanded=False):
        
            # Kiểm tra nhanh nếu chưa có nội dung ở Bước 1 thì hiện cảnh báo nhẹ (Màu nâu đậm)
            if not st.session_state.get('main_content_area'):
                st.markdown("""
                    <div style="color: #3E2723; font-weight: bold; padding: 10px; background-color: #FFF3E0; border-radius: 5px; border-left: 5px solid #8B4513;">
                        ⚠️ Bạn cần nhập kịch bản ở Bước 1 trước khi chuẩn bị giọng đọc.
                    </div>
                """, unsafe_allow_html=True)
            # --- [FIX] KIỂM TRA LINK TRƯỚC KHI HIỂN THỊ ---
            # Chỉ hiện tùy chọn "Giọng mẫu" nếu link đó thực sự tồn tại (Status 200)
            has_valid_audio = False
            if selected_library_audio and str(selected_library_audio).startswith("http"):
                # Gọi hàm kiểm tra (có thể hơi chậm xíu nếu mạng yếu, nhưng đảm bảo chính xác)
                has_valid_audio = check_link_exists(selected_library_audio)

            # --- [SỬA LẠI GIAO DIỆN 2 CỘT] ---
            # 1. Tạo danh sách lựa chọn đầy đủ
            all_options = {
                "library": "🎵 Sử dụng giọng nói có sẵn",
                "mic": "🎙️ Thu âm trực tiếp",
                "upload": "📤 Tải file lên",
                "local_ai": "🖥️ Giọng AI tiêu chuẩn" 
            }
            
            # Lọc bỏ giọng thư viện nếu link không tồn tại
            if not has_valid_audio:
                all_options.pop("library")

            # 2. Chia thành 2 cột và tạo style khoảng cách
            st.markdown("""
                <style>
                    /* Ép các lựa chọn radio thành lưới 2 cột */
                    div[data-testid="stRadio"] > div {
                        display: grid !important;
                        grid-template-columns: 1fr 1fr !important; /* Chia 2 cột đều nhau */
                        gap: 15px 20px !important; /* Khoảng cách: 15px dọc, 20px ngang */
                    }
                    /* Làm đẹp từng ô lựa chọn */
                    div[data-testid="stRadio"] label {
                        background-color: #FFF8DC !important;
                        border: 1px solid #D7CCC8 !important;
                        padding: 15px !important;
                        border-radius: 10px !important;
                        width: 100% !important;
                        margin: 0 !important;
                    }
                </style>
            """, unsafe_allow_html=True)

            # [ĐÃ SỬA] Thêm label_visibility="collapsed" để ẩn dòng chữ tiêu đề
            voice_method = st.radio(
                "**Chọn cách nhập giọng đọc:**",
                options=list(all_options.values()),
                index=None,
                horizontal=True,
                label_visibility="collapsed",
                key="radio_voice_method"
            )
            
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
                    # [QUAN TRỌNG] Khởi tạo biến trước để tránh lỗi NameError nếu không hiện nút upload
                    uploaded_file = None 
                    
                    # Kiểm tra xem đã có nội dung kịch bản chưa
                    current_script_upload = st.session_state.get('main_content_area', "")
                    
                    # Nếu chưa có nội dung hoặc quá ngắn -> Hiện cảnh báo
                    if not current_script_upload or len(current_script_upload.strip()) < 5:
                        st.markdown("""
                            <div style="color: #3E2723; font-weight: bold; padding: 10px; background-color: #FFF3E0; border-radius: 5px; border-left: 5px solid #8B4513;">
                                ⚠️ Bạn chưa nhập kịch bản! Vui lòng quay lại Bước 1 viết nội dung trước khi tải file âm thanh.
                            </div>
                        """, unsafe_allow_html=True)
                    else:
                        # Chỉ hiện công cụ upload khi đã có kịch bản
                        st.markdown("<b>Chọn file ghi âm từ máy của bạn (mp3, wav, m4a):</b>", unsafe_allow_html=True)
                        st.caption("⚠️ Lưu ý: Dung lượng tối đa 10MB/file")
                        
                        # Lúc này mới gán giá trị thực cho biến
                        uploaded_file = st.file_uploader("", type=['mp3', 'wav', 'm4a'], label_visibility="collapsed")
                        
                        st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
                        is_ai_checked = st.checkbox("NHỚ TÍCH CHỌN NẾU UPLOAD GIỌNG AI", 
                                                help="Tích vào đây nếu file này tạo từ AI (ElevenLabs, Vbee...) để hệ thống KHÔNG lọc ồn, tránh làm méo giọng.",
                                                key="chk_ai_upload_flag")

                    # [SỬA LỖI] Đoạn này nằm ngoài else, nên biến uploaded_file phải luôn tồn tại (dù là None)
                    if uploaded_file:
                        # ... (Giữ nguyên phần xử lý file bên dưới của bạn) ...
                        # ... Logic kiểm tra dung lượng, đuôi file ...
                        # (Bạn không cần thay đổi code bên trong này, chỉ cần đảm bảo dòng 'if uploaded_file:' chạy được)
                        
                        # [MẸO] Nếu bạn lỡ xóa đoạn xử lý file cũ, hãy copy lại từ đoạn code gốc của file web_app.py
                        # Bắt đầu từ dòng: MAX_MB = 10 ...
                        pass # <-- Dòng này chỉ để giữ chỗ, bạn hãy giữ nguyên logic xử lý file cũ của bạn ở đây

                    if uploaded_file:
                        # [BẢO MẬT] Cấu hình giới hạn
                        MAX_MB = 10
                        MAX_FILE_SIZE = MAX_MB * 1024 * 1024 # 10MB đổi ra bytes
                        VALID_EXTS = ['mp3', 'wav', 'm4a', 'ogg', 'aac'] 
                        
                        # [QUAN TRỌNG] Kiểm tra kích thước NGAY LẬP TỨC
                        if uploaded_file.size > MAX_FILE_SIZE:
                            current_mb = uploaded_file.size / (1024 * 1024)
                            st.error(f"❌ File quá lớn ({current_mb:.2f} MB). Hệ thống chỉ nhận file dưới {MAX_MB} MB.")
                            st.session_state['temp_upload_file'] = None
                            # [LỆNH MỚI] Dừng code tại đây, không cho chạy tiếp các đoạn xử lý phía sau
                            st.stop()

                        # Lấy đuôi file
                        file_ext = uploaded_file.name.split('.')[-1].lower() if '.' in uploaded_file.name else ''

                        # 1. Kiểm tra loại file
                        if file_ext not in VALID_EXTS:
                            st.error(f"❌ Định dạng '{file_ext}' không hợp lệ! Chỉ chấp nhận: mp3, wav, m4a")
                            st.session_state['temp_upload_file'] = None
                            st.stop()
                        
                        # 2. Hợp lệ -> Lưu vào session
                        st.session_state['temp_upload_file'] = uploaded_file
                        st.session_state['temp_upload_name'] = uploaded_file.name
                        st.success(f"✅ Đã nhận file: {uploaded_file.name} ({uploaded_file.size / (1024*1024):.2f} MB)")

                # CASE 3: THU ÂM TRỰC TIẾP (GIAO DIỆN MÁY NHẮC CHỮ - ĐÃ SỬA KHOẢNG CÁCH)
                elif voice_method == "🎙️ Thu âm trực tiếp": 
                    
                    # Tạo một khung chứa riêng biệt
                    with st.container(border=True):
                        st.markdown("<h3 style='text-align: center; color: #D32F2F; margin-bottom: 15px;'>🎙️ PHÒNG THU ÂM</h3>", unsafe_allow_html=True)
                        
                        # 1. HIỆN KỊCH BẢN ĐỂ ĐỌC
                        current_script = st.session_state.get('main_content_area', "")
                        
                        # [LOGIC MỚI] Nếu chưa có kịch bản -> CHỈ HIỆN CẢNH BÁO VÀ DỪNG
                        if not current_script or len(current_script.strip()) < 5:
                            # Hiện cảnh báo đúng như yêu cầu (Màu Nâu)
                            st.markdown("""
                                <div style="color: #3E2723; font-weight: bold; padding: 10px; background-color: #FFF3E0; border-radius: 5px; border-left: 5px solid #8B4513;">
                                    ⚠️ Bạn chưa nhập kịch bản! Vui lòng quay lại Bước 1 viết nội dung trước khi tải file âm thanh.
                                </div>
                            """, unsafe_allow_html=True)
                        
                        else:
                            # NẾU ĐÃ CÓ KỊCH BẢN -> Mới hiện công cụ thu âm
                            
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
                

                

                # CASE 5: GIỌNG AI VIENEU (LOCAL PC)
                elif voice_method == "🖥️ Giọng AI tiêu chuẩn": 
                    
                    # --- THANH QUOTA (GIỮ NGUYÊN) ---
                    u_usage = user.get('tts_usage', 0) or 0
                    u_limit = user.get('tts_limit', 10000) or 10000 
                    min_used = round(u_usage / 1000, 1)
                    min_total = round(u_limit / 1000, 1)
                    min_left = max(0, min_total - min_used)
                    progress = min(u_usage / u_limit, 1.0) if u_limit > 0 else 1.0
                    bar_color = "red" if progress > 0.9 else ("orange" if progress > 0.7 else "green")

                    st.markdown(f"""
                    <div style="margin-bottom: 15px; padding: 10px; border: 1px solid #D7CCC8; border-radius: 8px; background: #FFF8E1;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 5px; font-weight: bold; color: #5D4037;">
                            <span>⏱️ Thời lượng giọng AI</span>
                            <span>còn lại: {min_left} phút</span>
                        </div>
                        <div style="width: 100%; background-color: #E0E0E0; border-radius: 5px; height: 10px;">
                            <div style="width: {progress*100}%; background-color: {bar_color}; height: 10px; border-radius: 5px;"></div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown("##### 🖥️ Chọn giọng đọc ")
                    
                    # Kiểm tra kịch bản
                    current_script_local = st.session_state.get('main_content_area', "")
                    if not current_script_local or len(current_script_local.strip()) < 2:
                        st.warning("⚠️ Vui lòng nhập nội dung kịch bản ở Bước 1 trước!")
                    else:
                        c_loc1, c_loc2 = st.columns([2, 1])
                        with c_loc1:
                            # Hiển thị danh sách giọng đọc từ ảnh đính kèm
                            selected_voice_name = st.selectbox("Chọn giọng đọc:", VIENEU_VOICES, index=1) # Mặc định chọn Ly
                        with c_loc2:
                            # Sửa tốc độ mặc định thành 0.6 theo yêu cầu
                            speed_input = st.slider("Tốc độ đọc", 0.5, 2.0, 0.8, 0.1)

                        # --- CƠ CHẾ TỰ ĐỘNG PHỤC HỒI NẾU BỊ F5 MẤT SESSION ---
                        if 'pending_tts_id' not in st.session_state:
                            recovered_id = get_pending_local_ai_request(user['email'], current_script_local)
                            if recovered_id:
                                st.session_state['pending_tts_id'] = recovered_id
                        
                        # --- GIAO DIỆN KHI ĐANG CÓ YÊU CẦU CHẠY NGẦM ---
                        if 'pending_tts_id' in st.session_state:
                            req_id = st.session_state['pending_tts_id']
                            
                            # Kiểm tra tiến độ ngay lập tức
                            check = supabase.table('tts_requests').select("status, audio_link, output_path, voice_id").eq('id', req_id).execute()
                            
                            if check.data:
                                status = check.data[0]['status']
                                
                                if status == 'done':
                                    st.success("✅ Đã tạo giọng thành công!")
                                    st.session_state['local_ai_audio_link'] = check.data[0]['audio_link']
                                    st.session_state['local_ai_info'] = f"Voice: {check.data[0]['voice_id']}"
                                    del st.session_state['pending_tts_id'] # Xóa trạng thái chờ
                                    st.rerun()
                                    
                                elif status == 'error':
                                    st.error(f"❌ Lỗi xử lý âm thanh từ máy chủ AI: {check.data[0].get('output_path', 'Không rõ nguyên nhân')}")
                                    del st.session_state['pending_tts_id']
                                    if st.button("🔄 Thử lại"): st.rerun()
                                    
                                else:
                                    # Trạng thái Pending/Processing
                                    st.info("⏳ AI đang xử lý giọng nói ngầm. Quá trình này có thể mất 1-3 phút tùy độ dài kịch bản.")
                                    st.caption("💡 Mẹo: Bạn có thể ẩn mục này đi, làm việc khác hoặc tải lại trang (F5). Dữ liệu đang được máy chủ giữ an toàn.")
                                    if st.button("🔄 Bấm vào đây để kiểm tra trạng thái", use_container_width=True):
                                        st.rerun()

                        # --- GIAO DIỆN KHI CHƯA GỬI YÊU CẦU ---
                        else:
                            if st.button("🎙️ GỬI YÊU CẦU TẠO GIỌNG", type="primary", use_container_width=True):
                                # 1. Kiểm tra hạn mức
                                is_enough, msg_or_count = check_tts_quota(user, current_script_local)
                                
                                if not is_enough:
                                    st.error(msg_or_count)
                                else:
                                    try:
                                        # Insert vào database trạng thái chờ
                                        res = supabase.table('tts_requests').insert({
                                                "email": user['email'],
                                                "content": sanitize_input(current_script_local),
                                                "voice_id": selected_voice_name,
                                                "speed": speed_input,
                                                "status": "pending"
                                            }).execute()
                                        
                                        if res.data:
                                            req_id = res.data[0]['id']
                                            # Trừ hạn mức
                                            new_val = update_tts_usage_supabase(user['id'], msg_or_count)
                                            if new_val: user['tts_usage'] = new_val

                                            # Ước tính thời gian tạo (Giả định máy chủ đọc 15 ký tự/giây)
                                            estimated_time_seconds = len(current_script_local) / 15
                                            
                                            if estimated_time_seconds > 30:
                                                # Kịch bản dài -> Chạy ngầm và lưu thẳng vào Lịch sử
                                                st.toast("🚀 Giọng nói sẽ được lưu vào Danh sách video!", icon="✅")
                                                temp_audio_link = f"pending_tts_{req_id}" # Tạo mã liên kết tạm thời
                                                
                                                # Hàm create_order_logic sẽ tự động lưu và load lại trang
                                                create_order_logic(user, "VoiceOnly", temp_audio_link, current_script_local, settings)
                                            else:
                                                # Kịch bản ngắn -> Đợi trực tiếp trên màn hình
                                                st.session_state['pending_tts_id'] = req_id
                                                st.toast("🚀 Đã đẩy yêu cầu lên máy chủ thành công!", icon="✅")
                                                st.rerun()
                                            
                                    except Exception as e:
                                        st.error(f"Lỗi kết nối máy chủ dữ liệu: {e}")

                    # Hiển thị kết quả & Các tùy chọn
                    if st.session_state.get('local_ai_audio_link'):
                        # 1. Phát âm thanh
                        st.audio(st.session_state['local_ai_audio_link'], format="audio/wav")
                        st.caption(f"ℹ️ {st.session_state.get('local_ai_info')}")
                        
                        # 2. Gán link để sẵn sàng sử dụng
                        final_audio_link_to_send = st.session_state['local_ai_audio_link']
                        st.session_state['chk_ai_upload_flag'] = True

                        # 3. HIỂN THỊ 3 NÚT CHỨC NĂNG
                        st.markdown("---")
                        st.write("👉 **Bạn muốn làm gì tiếp theo?**")
                        
                        col_opt1, col_opt2, col_opt3 = st.columns(3)
                        
                        # NÚT 1: [ĐÃ SỬA] CHUYỂN HƯỚNG NGƯỜI DÙNG
                        with col_opt1:
                            if st.button("🎬 Dùng giọng này", type="primary", use_container_width=True):
                                # Thay vì tạo đơn ngay, ta hiện thông báo và hướng dẫn xuống Bước 3
                                
                                st.markdown("""
                                <div style="background-color: #E8F5E9; border: 1px solid #4CAF50; padding: 10px; border-radius: 5px; margin-top: 10px; color: #1B5E20;">
                                    <b>✅ Đã chọn giọng đọc!</b><br>
                                    👇 Kéo xuống <b>BƯỚC 3</b> để chọn kiểu video minh họa, sau đó bấm nút <b>GỬI YÊU CẦU TẠO VIDEO</b>.
                                </div>
                                """, unsafe_allow_html=True)
                        
                        # NÚT 2: CHỈ LƯU GIỌNG
                        with col_opt2:
                            if st.button("💾 Chỉ lưu giọng", use_container_width=True):
                                # Tạo đơn hàng nhưng set Status='VoiceOnly'
                                create_order_logic(user, "VoiceOnly", final_audio_link_to_send, current_script_local, settings)

                        # NÚT 3: TẠO LẠI (RESET)
                        with col_opt3:
                            if st.button("🔄 Tạo lại giọng", use_container_width=True):
                                st.session_state['local_ai_audio_link'] = None
                                st.rerun()


    # --- (B3) CHỌN PHONG CÁCH VIDEO (MỚI) ---
    st.markdown("<br><br>", unsafe_allow_html=True)
    
    # [LƯU Ý] Dòng with này phải sát lề trái, thẳng hàng với các dòng if/else lớn
    with st.expander("3️⃣ BƯỚC 3: CÁCH CHỌN VIDEO MINH HỌA", expanded=False):
        
        # Radio chọn chế độ
        video_style = st.radio(
            "Chế độ video:",
            ["AI tự động chọn video", "Chọn chủ đề video cụ thể", "Video kết hợp ảnh AI (Mới)"],
            key="rb_video_style",
            label_visibility="collapsed"
        )
        
        selected_topic_name = ""
        
        if "Chọn chủ đề video cụ thể" in video_style:
            # Danh sách chủ đề (Hardcode theo folder trên máy bạn)
            TOPIC_LIST = [
                "0 Đức Phật 2026", "0 Đức Phật và Cờ VN", "0 Đọc sách bên hoa sen", "1 Người tí hon bên sen", "2 Đầm sen chill chill", "3 Ruộng bậc thang dưới ánh trăng", "AI bầu trời", "AI chùa", "AI sinh vật cute", "Anime", 
                "Âu Mỹ", "Âu Mỹ home garden", "Bác Hồ", "Biển đại dương", 
                "Chiến tranh người que", "Cô đơn giữa mây trời", "Cô gái và linh thú", 
                "Con Đường", "Cyperpunk", "Động vật cute", 
                "Gọt trái cây", "Mặt trời lặn", "Mặt trời mọc", "Mùa hạ", "Mùa thu", 
                "Mùa xuân", "Thiên nhiên", 
                "Thực vật phát sáng", "Võ thuật", "Vũ Trụ"
            ]
            
            selected_topic_name = st.selectbox(
                "Chọn chủ đề mong muốn:",
                TOPIC_LIST,
                key="sb_topic_select"
            )
            st.caption(f"👉 Chỉ lấy video từ chủ đề: **{selected_topic_name}**")
            
            # Cập nhật vào settings
            settings['video_mode'] = 'topic'
            settings['topic_name'] = selected_topic_name
        elif "ảnh AI" in video_style:
            settings['video_mode'] = 'ai_image'
            settings['topic_name'] = ""
        else:
            settings['video_mode'] = 'auto'
            settings['topic_name'] = ""

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
        
        # [FIX] Đặt giới hạn từ mặc định ban đầu là 2000 để tránh lỗi NameError
        MAX_WORDS = 2000

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
        # [MỚI] CASE Local AI (SỬA LỖI TÊN GỌI)
        elif voice_method == "🖥️ Giọng AI tiêu chuẩn":
            if st.session_state.get('local_ai_audio_link'):
                ready_to_send = True
                final_audio_link_to_send = st.session_state['local_ai_audio_link']
                # Cài đặt
                settings['is_ai_voice'] = True
                settings['clean_audio'] = False
                settings['voice_info'] = st.session_state.get('local_ai_info', "Local AI")
                
                # Giới hạn từ cho gói Pro: 3 phút 10 giây (~633 từ)
                if user.get('plan') in ['pro', 'huynhde', 'dacbiet']:
                    MAX_WORDS = 633
                # Giới hạn từ cho gói Free/Cơ bản: 2 phút (~400 từ)
                else:
                    MAX_WORDS = 400
            else:
                st.error("⚠️ Bạn chưa bấm nút tạo giọng ở Bước 2!")
            if st.session_state.get('local_ai_audio_link'):
                ready_to_send = True
                final_audio_link_to_send = st.session_state['local_ai_audio_link']
                # Cài đặt
                settings['is_ai_voice'] = True
                settings['clean_audio'] = False
                settings['voice_info'] = st.session_state.get('local_ai_info', "Local AI")
            else:
                st.error("⚠️ Bạn chưa bấm nút tạo giọng ở Bước 2!")


        # [NEW] CASE 3: Các trường hợp khác (Giọng Google cũ, Tự thu, Upload...)
        else:
            # Các phương thức khác cho phép đến 2000 từ
            MAX_WORDS = 2000
            
        if not noi_dung_gui:
            st.toast("⚠️ Thiếu nội dung!", icon="⚠️")
        elif word_count > MAX_WORDS:
            st.error(f"⚠️ Nội dung quá dài ({word_count} từ). Gói hiện tại chỉ cho phép tối đa {MAX_WORDS} từ/video. Vui lòng cắt ngắn bớt!")
        elif not ready_to_send: 
            st.toast("⚠️ Thiếu file âm thanh!", icon="⚠️")
        else:
            try:
                # [FIX 503] Bỏ kết nối Google Sheet ở đây vì hay gây lỗi quá tải.
                # Thay bằng cách tạo ID theo Thời gian + Số ngẫu nhiên (Nhanh & Không bao giờ trùng)
                
                import random
                
                # 1. Lấy thời gian hiện tại
                now_vn = datetime.utcnow() + timedelta(hours=7)
                
                # 2. Tạo đuôi ngẫu nhiên 3 số (ví dụ: _123, _999)
                random_suffix = random.randint(100, 999)
                
                # 3. Ghép thành ID hoàn chỉnh (Ví dụ: 20231025_103000_567)
                order_id = now_vn.strftime("%Y%m%d_%H%M%S") + f"_{random_suffix}"
                
                # Cập nhật timestamp
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

                # [NEW] CASE 3: Dùng giọng Gemini (Đã tạo sẵn ở bước trên)
                # SỬA LỖI: Đổi tên so sánh thành "🤖 Giọng AI Gemini" cho khớp với menu
                elif voice_method == "🤖 Giọng AI Gemini":
                    # Kiểm tra xem người dùng đã bấm nút tạo file ở Bước 2 chưa
                    if st.session_state.get('gemini_full_audio_link'):
                        final_audio_link_to_send = st.session_state['gemini_full_audio_link']
                        ready_to_send = True
                        
                        # Cài đặt cho giọng AI
                        settings['is_ai_voice'] = True
                        settings['clean_audio'] = False 
                        settings['voice_info'] = st.session_state.get('gemini_voice_info', "Gemini AI")
                    else:
                        st.error("⚠️ Bạn chưa bấm nút 'TẠO GIỌNG ĐỌC ĐẦY ĐỦ' ở Bước 2!")
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
                
                # Insert vào bảng orders (Có bắt lỗi 500)
                try:
                    supabase.table('orders').insert(order_data).execute()
                    
                    # --- [MỚI] TÍNH TOÁN HÀNG CHỜ ---
                    # Đếm số lượng đơn đang chờ hoặc đang chạy
                    # count='exact' giúp Supabase chỉ trả về số lượng (rất nhanh), không tải dữ liệu nặng
                    queue_res = supabase.table('orders').select('*', count='exact').in_('status', ['Pending', 'Processing']).execute()
                    current_queue = queue_res.count if queue_res.count else 1
                    est_wait_time = current_queue * 3 # Giả sử trung bình 3 phút/video
                    
                    st.session_state['queue_info'] = {
                        "position": current_queue,
                        "wait_time": est_wait_time
                    }
                    # --------------------------------
                    
                except Exception as e:
                    # Nếu lỗi 500, chờ 1 giây rồi thử lại 1 lần nữa (Cơ chế Retry)
                    if "500" in str(e):
                        time.sleep(1)
                        supabase.table('orders').insert(order_data).execute()
                    else:
                        raise e # Nếu lỗi khác thì báo ra ngoài

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
                
                # --- [NEW LOGIC] KIỂM TRA GIỜ ĐỂ HIỆN THÔNG BÁO ---
                # Biến now_vn đã được tạo ở trên (dòng 1405): now_vn = datetime.utcnow() + timedelta(hours=7)
                cur_hour = now_vn.hour
                cur_minute = now_vn.minute
                
                # Logic: Giờ làm việc mới từ 7:00 đến 23:00
                is_working_time = False
                if 7 <= cur_hour < 23:
                    is_working_time = True

                if is_working_time:
                    # Lấy thông tin hàng chờ
                    q_info = st.session_state.get('queue_info', {'position': 1, 'wait_time': 5})
                    
                    # --- [LOGIC MỚI] ẨN SỐ LƯỢNG NẾU QUÁ ĐÔNG ---
                    real_pos = q_info['position']
                    
                    # Tính số người thực sự đứng trước (Tổng trừ đi chính mình)
                    people_ahead = max(0, real_pos - 1)

                    if real_pos > 10:
                        pos_display = "Hơn 10 người"
                        sub_text = "Hệ thống đang xử lý nhiều đơn hàng trước bạn"
                    else:
                        pos_display = f"Thứ {real_pos}"
                        
                        # Logic hiển thị thông minh hơn
                        if people_ahead == 0:
                            sub_text = "✨ Hệ thống đang xử lý ngay."
                        else:
                            sub_text = f"Hệ thống đang xử lý {people_ahead} đơn hàng trước bạn"
                    # ---------------------------------------------

                    st.success(f"✅ ĐÃ GỬI THÀNH CÔNG! Mã đơn: {order_id}")
                    
                    
                    
                    st.balloons()
                    time.sleep(3) 
                    st.rerun()
                else:
                    # [ĐÃ SỬA] Dùng st.success và st.rerun giống hệt bên trên, chỉ khác nội dung
                    st.success(f"🌙 ĐÃ NHẬN ĐƠN NGOÀI GIỜ! Mã đơn: {order_id}. Hệ thống sẽ xử lý sau 7:00 sáng.")
                    st.balloons()
                    st.rerun()
                
            except Exception as e: st.error(f"Lỗi hệ thống: {e}")

    # --- KIỂM TRA KẾT QUẢ (Giữ nguyên, chỉ thêm chút style nếu cần) ---
    pass

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

    # [FIX] Chỉ hiển thị thông báo khi thực sự có video đang chạy
    if is_processing_real:
        # Lấy giờ hiện tại
        now_check = datetime.utcnow() + timedelta(hours=7)
        
        # Nếu đang trong giờ làm việc (7h - 23h)
        if 7 <= now_check.hour < 23:
            # --- [LOGIC MỚI] TÍNH TOÁN HÀNG CHỜ THỜI GIAN THỰC ---
            try:
                # Đếm lại số lượng để cập nhật mỗi khi f5
                q_res = supabase.table('orders').select('*', count='exact').in_('status', ['Pending', 'Processing']).execute()
                q_count = q_res.count if q_res.count else 1
                q_wait = q_count * 3 # 3 phút/video
                
                # [FIX] Trừ đi 1 (chính là đơn hàng của bạn)
                real_ahead = max(0, q_count - 1)

                # Logic hiển thị thông minh hơn
                if real_ahead > 10:
                    q_text = "Hơn 10 người"
                elif real_ahead == 0:
                    q_text = "0 người"
                else:
                    q_text = f"{real_ahead} người"
                
                st.markdown(f"""
                <div style="background-color: #E3F2FD; color: #0D47A1; padding: 15px; border-radius: 10px; border: 1px solid #2196F3; margin-bottom: 20px;">
                    <span style="font-size: 18px; font-weight: bold;">⚙️ Đang tạo video </span><br>
                    <span style="font-size: 16px;">
                        🔢 Đang có <b>{q_text}</b> trước bạn.<br>
                        ⏳ Vui lòng quay lại sau <b>{q_wait} phút và bấm nút xem danh sách hoặc làm mới. </b>.
                    </span>
                </div>
                """, unsafe_allow_html=True)
            except:
                # Fallback nếu lỗi kết nối đếm
                st.info("⏳ Đang tạo video. Vui lòng đợi trong giây lát...")

        # Nếu là ban đêm -> Báo đang chờ đến sáng (KHÔNG báo đang tạo)
        else:
            st.markdown("""
            <div style="background-color: #E3F2FD; color: #0D47A1; padding: 15px; border-radius: 10px; border: 1px solid #90CAF9; margin-bottom: 20px; font-weight: bold;">
                🌙 Đã nhận nội dung của bạn vào thời gian nghỉ. Video sẽ được tạo sau 7:00 sáng.
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
                "Pending": "⏳ Đang chờ", 
                "Processing": "⚙️ Đang tạo...",
                "Done": "✅ Hoàn thành", 
                "VoiceOnly": "💾 Đã có giọng AI",
                "Error": "❌ Lỗi", 
                "": "❓ Không rõ"
            }
            
            MAX_ITEMS = 3
            if 'history_expanded' not in st.session_state: st.session_state['history_expanded'] = False
            
            df_display = history_df if st.session_state['history_expanded'] else history_df.head(MAX_ITEMS)
            total_items = len(history_df)

            for index, row in df_display.iterrows():
                date_str = row.get('NgayTao', '')
                result_link = row.get('LinkKetQua', '')
                raw_status = row.get('TrangThai', 'Pending')
                order_id = row.get('ID', f'id_{index}')
                old_audio_link = row.get('LinkGiongNoi', '')
                old_content_script = row.get('NoiDung', '')

                # [QUAN TRỌNG] Tạo biến vn_status để không bị lỗi
                vn_status = status_map.get(raw_status, "❓ Chờ xử lý")

                try:
                    decoded_content = html.unescape(str(old_content_script))
                    words = decoded_content.split()
                    script_preview = " ".join(words[:10]) + "..." if len(words) > 10 else decoded_content
                except: script_preview = "Kịch bản..."

                try:
                    dt_obj = pd.to_datetime(date_str)
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.tz_localize('UTC').tz_convert('Asia/Ho_Chi_Minh')
                    else:
                        dt_obj = dt_obj.tz_convert('Asia/Ho_Chi_Minh')
                    display_date = dt_obj.strftime('%d/%m/%Y - %H:%M')
                except:
                    display_date = str(date_str)

                # --- HIỂN THỊ CHI TIẾT VIDEO ---
                with st.expander(f"{display_date} | {vn_status} | 📝 {script_preview}"):
                    
                    # CASE A: NẾU LÀ "CHỈ LƯU GIỌNG" (VoiceOnly)
                    if raw_status == "VoiceOnly":
                        st.info("💾 Đây là bản lưu giọng nói (Chưa tạo video).")
                        
                        # --- [MỚI] KIỂM TRA TRẠNG THÁI TTS CHẠY NGẦM ---
                        if old_audio_link and str(old_audio_link).startswith("pending_tts_"):
                            # Trích xuất ID yêu cầu chạy ngầm
                            req_id = str(old_audio_link).replace("pending_tts_", "")
                            
                            try:
                                # Hỏi Supabase xem file đã tạo xong chưa
                                check_tts = supabase.table('tts_requests').select('status, audio_link').eq('id', req_id).execute()
                                if check_tts.data:
                                    tts_status = check_tts.data[0]['status']
                                    if tts_status == 'done':
                                        real_link = check_tts.data[0]['audio_link']
                                        # Đã xong -> Cập nhật link thật vào bảng orders
                                        supabase.table('orders').update({"audio_link": real_link}).eq('id', order_id).execute()
                                        st.success("✅ Hệ thống đã tạo xong giọng AI ngầm!")
                                        st.audio(real_link, format="audio/wav")
                                        old_audio_link = real_link # Cập nhật biến để hiển thị nút bên dưới
                                    elif tts_status == 'error':
                                        st.error("❌ Quá trình tạo giọng AI bị lỗi.")
                                    else:
                                        st.warning("⏳ Trí tuệ nhân tạo vẫn đang tạo giọng ngầm. Bạn hãy nhấn 'Làm mới' sau ít phút nhé...")
                            except Exception as e:
                                st.error("Lỗi kiểm tra dữ liệu ngầm.")
                        else:
                            # 1. Hiện Audio Player để nghe lại bình thường (nếu đã có link thật)
                            if old_audio_link and str(old_audio_link).startswith("http"):
                                st.audio(old_audio_link, format="audio/wav")
                        
                        # 2. Nút chuyển đổi thành Video (Chỉ hiện khi đã có link audio thật)
                        if old_audio_link and str(old_audio_link).startswith("http"):
                            if st.button("🎬 Chuyển thành Video ngay", key=f"btn_convert_{order_id}"):
                                # Update trạng thái từ VoiceOnly -> Pending
                                try:
                                    supabase.table('orders').update({"status": "Pending"}).eq('id', order_id).execute()
                                    # Trừ quota
                                    update_user_usage_supabase(user['id'], user['quota_used'])
                                    st.session_state['user_info']['quota_used'] += 1
                                    st.success("✅ Đã chuyển sang chờ xử lý video!")
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Lỗi: {e}")

                    # CASE B: VIDEO ĐÃ HOÀN THÀNH (Done)
                    elif result_link and len(str(result_link)) > 5:
                        # ... (Giữ nguyên code hiển thị nút Xem Video/Tải Video cũ ở đây) ...
                        # ...
                        pass # Xóa dòng pass này khi paste code cũ vào

                    # CASE C: ĐANG XỬ LÝ / LỖI
                    elif raw_status == "Error":
                        st.error("Video này bị lỗi xử lý.")
                    else:
                        st.info("Hệ thống đang xử lý video này...")
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

                            # 4. HIỆN NÚT BẤM HTML (Đã sửa lỗi trên Xiaomi/HyperOS)
                            download_script = f"""
                            <a href="{direct_dl_link}" 
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

                    # B. Nút Tạo lại (Re-create) - [ĐÃ CẬP NHẬT: THÊM XÁC NHẬN BƯỚC 3]
                    st.markdown('<div style="margin-top: 5px;"></div>', unsafe_allow_html=True) 
                    if old_audio_link and str(old_audio_link).startswith("http"):
                        
                        # [LOGIC MỚI] 1. Kiểm tra: Nếu CHƯA bấm nút (hoặc đang bấm nút khác) -> Thì mới hiện nút "Tạo lại"
                        if st.session_state.get('confirm_recreate_id') != order_id:
                            # Nút kích hoạt
                            if st.button(f"♻️ Tạo lại bằng giọng nói này", key=f"pre_recreate_{order_id}_{index}", disabled=is_out_of_quota, use_container_width=True):
                                # Lưu ID của đơn hàng đang muốn tạo lại vào session
                                st.session_state['confirm_recreate_id'] = order_id
                                st.rerun()

                        # 2. Nếu ĐÃ BẤM (ID khớp với session) -> Thì hiện khung xác nhận (ẩn nút trên đi)
                        if st.session_state.get('confirm_recreate_id') == order_id:
                            st.markdown("""
                            <div style="background-color: #FFF3E0; border: 2px solid #FF9800; padding: 15px; border-radius: 10px; margin-bottom: 10px; margin-top: 5px;">
                                <h4 style="color: #E65100; margin: 0; font-size: 18px;">⚠️ LƯU Ý</h4>
                                <p style="color: #5D4037; font-size: 16px; margin-top: 5px; line-height: 1.5;">
                                    <b>Cài đặt hiện tại ở BƯỚC 3</b> sẽ được dùng để tạo video mới này.<br>
                                    👉 Nếu bạn muốn thay đổi, hãy chỉnh lại ở <b>Bước 3</b> trước khi bấm nút Tạo Ngay.
                                </p>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            col_conf1, col_conf2 = st.columns(2)
                            with col_conf1:
                                # Nút XÁC NHẬN THẬT (Code xử lý cũ nằm ở đây)
                                if st.button("✅ ĐÃ HIỂU, TẠO NGAY", key=f"real_recreate_{order_id}_{index}", type="primary", use_container_width=True):
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
                                                    "settings": settings # <--- QUAN TRỌNG: Dùng settings hiện tại của UI
                                                }
                                                
                                                # 3. Gửi vào Supabase
                                                supabase.table('orders').insert(order_data).execute()
                                                
                                                # 4. Cập nhật Quota (Trừ lượt dùng)
                                                update_user_usage_supabase(user['id'], user['quota_used'])
                                                
                                                # 5. Log & Dọn dẹp
                                                log_history(new_id, user['email'], "", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
                                                
                                                st.session_state['user_info']['quota_used'] += 1
                                                st.session_state['show_wait_message'] = True
                                                
                                                # Xóa trạng thái xác nhận để đóng form
                                                del st.session_state['confirm_recreate_id']
                                                
                                                st.success("✅ Đã gửi lệnh tạo lại!")
                                                st.rerun()
                                        except Exception as e: st.error(f"Lỗi: {e}")

                            with col_conf2:
                                # Nút HỦY
                                if st.button("❌ Hủy bỏ (Chỉnh lại)", key=f"cancel_recreate_{order_id}_{index}", use_container_width=True):
                                    del st.session_state['confirm_recreate_id']
                                    st.rerun()

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
