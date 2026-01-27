# --- THÊM ĐOẠN NÀY VÀO SAU CÁC DÒNG IMPORT ---
from supabase import create_client, Client

# Hàm này giúp kết nối Supabase và giữ kết nối không bị ngắt
# Dùng cache_resource cho KẾT NỐI (Database, ML models...)
@st.cache_resource
def init_supabase():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

# Khởi tạo kết nối ngay lập tức
supabase = init_supabase()

# FILE: web_app.py (VERSION 7.2 - FULL SETTINGS RESTORED)
# --- [NEW] HÀM MẬT KHẨU AN TOÀN ---
def hash_password(plain_text_password):
    # Mã hóa mật khẩu
    return bcrypt.hashpw(plain_text_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_text_password, hashed_password):
    # Kiểm tra mật khẩu
    return bcrypt.checkpw(plain_text_password.encode('utf-8'), hashed_password.encode('utf-8'))

# --- [NEW] LOGIC ĐĂNG NHẬP VỚI SUPABASE ---
def check_login(email, password):
    try:
        # 1. Tìm user trong Supabase
        response = supabase.table('users').select("*").eq('email', email).execute()
        
        if response.data and len(response.data) > 0:
            user_data = response.data[0]
            stored_hash = user_data['password']
            
            # 2. Kiểm tra mật khẩu (So sánh pass nhập vào với mã hash)
            if verify_password(password, stored_hash):
                # Reset quota logic (nếu cần) có thể đặt ở đây hoặc xử lý sau
                return user_data
                
    except Exception as e:
        st.error(f"Lỗi đăng nhập: {e}")
    return None

# --- [NEW] HÀM ĐỔI MẬT KHẨU ---
def change_password_action(email, old_pass_input, new_pass_input):
    try:
        # 1. Lấy thông tin user
        response = supabase.table('users').select("password").eq('email', email).execute()
        if response.data:
            stored_hash = response.data[0]['password']
            # 2. Check pass cũ
            if verify_password(old_pass_input, stored_hash):
                # 3. Hash pass mới và cập nhật
                new_hash = hash_password(new_pass_input)
                supabase.table('users').update({"password": new_hash}).eq('email', email).execute()
                return True, "✅ Đổi mật khẩu thành công!"
            else:
                return False, "❌ Mật khẩu cũ không đúng!"
    except Exception as e:
        return False, f"Lỗi hệ thống: {e}"
    return False, "❌ Lỗi không xác định"

# --- [NEW] CẬP NHẬT QUOTA ---
def update_user_usage_supabase(user_id, current_used):
    try:
        supabase.table('users').update({"quota_used": current_used + 1}).eq('id', user_id).execute()
    except Exception as e:
        print(f"Lỗi update quota: {e}")

# --- [NEW] CÁC HÀM QUẢN LÝ USER & QUOTA ---
# --- [UPDATE] LOGIC ĐĂNG NHẬP & RESET QUOTA THEO NGÀY ĐĂNG KÝ ---
def check_login(email, password):
    try:
        gc = get_gspread_client()
        ws = gc.open(DB_SHEET_NAME).worksheet("users")
        
        # [OPTIMIZED] Lấy toàn bộ dữ liệu 1 lần thay vì dùng .find() + .row_values()
        # Giúp tiết kiệm 50% số lần gọi API Google
        all_users = ws.get_all_values()
        
        # Loop qua từng dòng trong RAM của Python (Siêu nhanh)
        for i, row_data in enumerate(all_users):
            # i=0 là tiêu đề, bỏ qua
            if i == 0: continue
            
            # Cột 1 là Email (index 0). So sánh không phân biệt hoa thường
            if len(row_data) > 0 and str(row_data[0]).strip().lower() == str(email).strip().lower():
                
                # [FIX] Tự động điền thêm phần tử rỗng nếu hàng thiếu dữ liệu
                while len(row_data) < 7:
                    row_data.append("")

                # Cấu trúc: A=Email, B=Pass, C=Plan, D=Max, E=Used, F=NextResetDate, G=Stock
                db_pass = row_data[1]
                
                if str(password) == str(db_pass):
                    def safe_int(val):
                        try: return int(val)
                        except: return 0

                    # Vì Sheet tính dòng từ 1, mà list Python tính từ 0, nên dòng thực tế là i + 1
                    current_row = i + 1 

                    user_info = {
                        "row": current_row,
                        "email": row_data[0],
                        "plan": row_data[2],
                        "quota_max": safe_int(row_data[3]),   
                        "quota_used": safe_int(row_data[4]),  
                        "next_reset": row_data[5], 
                        "stock_level": safe_int(row_data[6])  
                    }
                    
                    # [NEW LOGIC] Reset theo chu kỳ 30 ngày từ ngày đăng ký
                    try:
                        today = datetime.now().date()
                        if user_info["next_reset"]:
                            next_reset_date = datetime.strptime(user_info["next_reset"], "%Y-%m-%d").date()
                            
                            # Nếu hôm nay đã vượt qua ngày reset
                            if today >= next_reset_date:
                                # 1. Reset Quota Used = 0
                                ws.update_cell(current_row, 5, 0) 
                                user_info["quota_used"] = 0
                                
                                # 2. Tính ngày reset tiếp theo
                                new_next_reset = next_reset_date + timedelta(days=30)
                                new_reset_str = new_next_reset.strftime("%Y-%m-%d")
                                
                                # 3. Cập nhật ngày reset mới vào Sheet
                                ws.update_cell(current_row, 6, new_reset_str)
                                user_info["next_reset"] = new_reset_str
                    except Exception as e:
                        print(f"Lỗi format ngày tháng: {e}") 
                    
                    return user_info
                    
    except Exception as e:
        st.error(f"Lỗi đăng nhập: {e}")
    return None

# --- [NEW] HÀM ĐỔI MẬT KHẨU ---
def change_password_action(email, old_pass_input, new_pass_input):
    try:
        gc = get_gspread_client()
        ws = gc.open(DB_SHEET_NAME).worksheet("users")
        cell = ws.find(email, in_column=1)
        
        if cell:
            # Lấy mật khẩu hiện tại trong DB để kiểm tra (Cột 2)
            current_db_pass = ws.cell(cell.row, 2).value
            
            # Kiểm tra mật khẩu cũ người dùng nhập có đúng không
            if str(current_db_pass) == str(old_pass_input):
                # Nếu đúng thì cập nhật mật khẩu mới
                ws.update_cell(cell.row, 2, new_pass_input)
                return True, "✅ Đổi mật khẩu thành công!"
            else:
                return False, "❌ Mật khẩu cũ không đúng!"
    except Exception as e:
        return False, f"Lỗi hệ thống: {e}"
    return False, "❌ Không tìm thấy tài khoản!"


# --- [NEW] HÀM LƯU VÀ TẢI BẢN NHÁP ---
def save_draft_to_sheet(email, content):
    try:
        gc = get_gspread_client()
        # Mở sheet drafts (Bạn nhớ tạo sheet này trong file Google Sheet nhé)
        try:
            ws = gc.open(DB_SHEET_NAME).worksheet("drafts")
        except:
            # Nếu chưa có thì tự tạo (phòng hờ)
            ws = gc.open(DB_SHEET_NAME).add_worksheet(title="drafts", rows=100, cols=5)
            ws.append_row(["Email", "Content"])
            
        # Tìm xem user đã có bản nháp chưa
        cell = ws.find(email, in_column=1)
        # [BẢO MẬT] Làm sạch nội dung trước khi lưu
        safe_content = sanitize_input(content)

        if cell:
            # Nếu có rồi -> Cập nhật nội dung (Cột 2)
            ws.update_cell(cell.row, 2, safe_content)
        else:
            # Nếu chưa -> Thêm dòng mới
            ws.append_row([email, safe_content])
        return True
    except Exception as e:
        print(f"Lỗi save draft: {e}")
        return False

def load_draft_from_sheet(email):
    try:
        gc = get_gspread_client()
        ws = gc.open(DB_SHEET_NAME).worksheet("drafts")
        
        # [OPTIMIZED] Lấy hết về 1 lần thay vì tìm và gọi cell lẻ tẻ
        all_drafts = ws.get_all_values()
        
        for row in all_drafts:
            # Nếu tìm thấy email ở cột đầu tiên (index 0)
            if len(row) >= 2 and str(row[0]).strip().lower() == str(email).strip().lower():
                return row[1] # Trả về cột Content (index 1)
    except: pass
    return ""

# --- [NEW] HÀM CALLBACK ĐỂ AUTO-SAVE ---
def auto_save_callback():
    # Kiểm tra xem đã đăng nhập chưa
    if 'user_info' in st.session_state and st.session_state['user_info']:
        user_email = st.session_state['user_info']['email']
        # Lấy nội dung mới nhất từ ô nhập liệu (thông qua key)
        current_content = st.session_state['main_content_area']
        
        # Gọi hàm lưu vào Sheet
        save_draft_to_sheet(user_email, current_content)
        
        # Hiện thông báo nhỏ góc dưới (Toast) để người dùng yên tâm
        st.toast("Đã tự động lưu nháp! ✅")

# --- [UPDATE] HÀM LẤY LỊCH SỬ TỪ SHEET ORDERS ---
# [ĐÃ SỬA] Thêm Cache để không gọi API liên tục (ttl=300 nghĩa là lưu cache 300 giây/5 phút)
# Sửa st.cache_data thành st.cache (để chạy được trên server cũ)
@st.cache_data(ttl=300)
def get_all_orders_cached():
    try:
        gc = get_gspread_client()
        ws = gc.open(DB_SHEET_NAME).worksheet(DB_WORKSHEET)
        return pd.DataFrame(ws.get_all_records())
    except:
        return pd.DataFrame()

def get_user_history(email):
    try:
        # Gọi hàm đã được cache thay vì gọi trực tiếp sheet
        df = get_all_orders_cached()
        
        if df.empty: return pd.DataFrame()

        # 1. Lọc theo Email (Code cũ)
        if 'Email' in df.columns:
            df_user = df[df['Email'] == email].copy()
        else:
            return pd.DataFrame()
        
        # 2. Sắp xếp (Code cũ)
        if 'NgayTao' in df.columns:
            df_user['NgayTao'] = pd.to_datetime(df_user['NgayTao'], errors='coerce')
            df_user = df_user.sort_values(by='NgayTao', ascending=False)
        
        return df_user
    except Exception as e:
        return pd.DataFrame()
        
        if df.empty: return pd.DataFrame()

        # 1. Lọc theo Email người dùng hiện tại
        # Lưu ý: Tên cột phải khớp chính xác với tiêu đề trong file Sheet (theo ảnh bạn gửi)
        if 'Email' in df.columns:
            df_user = df[df['Email'] == email].copy()
        else:
            return pd.DataFrame() # Tránh lỗi nếu không tìm thấy cột Email
        
        # 2. Sắp xếp mới nhất lên đầu (Dựa vào cột NgayTao)
        if 'NgayTao' in df.columns:
            df_user['NgayTao'] = pd.to_datetime(df_user['NgayTao'], errors='coerce')
            df_user = df_user.sort_values(by='NgayTao', ascending=False)
        
        return df_user
    except Exception as e:
        # st.error(f"Lỗi tải lịch sử: {e}") # Bật lên nếu muốn debug
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
st.set_page_config(page_title="Thợ video", page_icon="📻", layout="centered")



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
        border-bottom: 2px solid #8B4513; padding-bottom: 10px; margin-bottom: 20px;
        font-weight: bold; text-transform: uppercase;
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
    
    /* 6. BUTTON (Nút bấm) */
    .stButton button {{
        background-color: #8B4513 !important; color: #FFFFFF !important; 
        font-weight: bold !important; font-size: 20px !important; 
        border-radius: 8px !important; margin-top: 10px; border: none !important;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.2) !important;
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

        /* 3. Canh lề lại cho gọn */
        .main .block-container {{
            padding-top: 2rem !important; 
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }}

        /* 4. [FIX] PHÓNG TO AUDIO PLAYER CHO ĐIỆN THOẠI */
        audio {{
            height: 65px !important;    /* Tăng chiều cao lên 65px */
            width: 104% !important;     /* Rộng hơn khung màn hình */
            margin-left: -2% !important;
            margin-top: 15px !important;
            margin-bottom: 15px !important;
            border-radius: 15px !important;
        }}
        
        /* Phóng to nút bấm Play/Pause bên trong */
        audio::-webkit-media-controls-play-button {{
            transform: scale(1.8) !important;
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

DB_SHEET_NAME = "VideoAutomation_DB"
DB_WORKSHEET = "orders"
LIBRARY_SHEET_ID = "1oTnl19oMQ1TLpaD5Tuu7seJ76JlNB9tEgnuiKwa66Uw" 

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
@st.cache_data(ttl=3600, show_spinner="Đang tải dữ liệu từ thư viện...")
def get_scripts_with_audio(sheet_name, stock_limit=1000):
    # [BẢO MẬT] Lấy link Hugging Face từ secrets
    if "huggingface" in st.secrets:
        BASE_URL = st.secrets["huggingface"]["base_url"]
    else:
        # Fallback nếu quên cấu hình secrets (giữ link cũ làm dự phòng hoặc để trống)
        BASE_URL = "nothing"    
    try:
        gc = get_gspread_client()
        sh = gc.open_by_key(LIBRARY_SHEET_ID)
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        
        # [ĐÃ SỬA] Logic mới: Duyệt trực tiếp danh sách gốc để đảm bảo thứ tự file (1.mp3, 2.mp3...) chuẩn xác
        results = []
        
        if data:
            # 1. Xác định tên cột nội dung từ dòng đầu tiên
            first_row = data[0]
            # Tìm key nào có chứa chữ "nội dung" hoặc "content"
            content_col = next((k for k in first_row.keys() if "nội dung" in k.lower() or "content" in k.lower()), None)
            
            # Nếu không tìm thấy thì lấy cột đầu tiên làm mặc định
            if not content_col: 
                content_col = list(first_row.keys())[0]

            # 2. Duyệt qua danh sách gốc và đếm số thứ tự (i)
            for i, row in enumerate(data):
                # Nếu đã lấy đủ số lượng giới hạn (stock_limit) thì dừng lại
                if i >= stock_limit:
                    break
                
                content_text = row.get(content_col, "")
                if content_text:
                    item = {"content": content_text}
                    
                    # [ĐÃ SỬA] Đổi thành i+2 để khớp với số dòng hiển thị trong Google Sheet
                    # Giải thích: Dữ liệu bắt đầu từ dòng 2. i chạy từ 0.
                    # Dòng 2 -> i=0 -> 0+2 = 2.mp3
                    # Dòng 6 -> i=4 -> 4+2 = 6.mp3 (Đúng ý bạn)
                    auto_link = f"{BASE_URL}{sheet_name}/{i+2}.mp3"
                    item["audio"] = auto_link
                    
                    results.append(item)
                    
        return results
    except Exception as e: 
        print(f"Lỗi load script: {e}")
        return []

# [NEW] TÌM KIẾM TRONG DATABASE (Nhanh hơn Sheet rất nhiều)
def search_global_library(keyword, user_stock_limit_ignored):
    try:
        keyword = keyword.lower().strip()
        if not keyword: return []
        
        # Tìm trong bảng library, cột content chứa keyword (ilike là case-insensitive)
        response = supabase.table('library').select("*").ilike('content', f'%{keyword}%').limit(20).execute()
        
        results = []
        for item in response.data:
            results.append({
                "content": item['content'],
                "audio": item['audio_url'],
                "source_sheet": item['category']
            })
        return results
    except Exception as e:
        print(f"Lỗi tìm kiếm Supabase: {e}")
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
            
            batch_data = []
            for i, row in enumerate(data):
                # Tìm cột nội dung
                content = ""
                for k, v in row.items():
                    if "nội dung" in k.lower() or "content" in k.lower():
                        content = v
                        break
                
                if content:
                    # Tạo link audio giả định theo quy tắc cũ
                    audio_link = f"{BASE_URL}{sheet_name}/{i+2}.mp3"
                    
                    # Chuẩn bị dữ liệu (cần khớp với cột trong Supabase)
                    batch_data.append({
                        "content": content,
                        "audio_url": audio_link,
                        "category": sheet_name,
                        "source_index": i+2
                    })
            
            # Đẩy lên Supabase (Upsert)
            if batch_data:
                # Chia nhỏ mỗi lần gửi 50 dòng để tránh lỗi
                chunk_size = 50
                for k in range(0, len(batch_data), chunk_size):
                    supabase.table('library').upsert(batch_data[k:k+chunk_size]).execute()
                total_synced += len(batch_data)

        status_text.success(f"✅ Đã đồng bộ xong {total_synced} kịch bản vào Supabase!")
        return True
    except Exception as e:
        st.error(f"Lỗi sync: {e}")
        return False

# --- [NEW] GIAO DIỆN ADMIN DASHBOARD ---
def admin_dashboard():
    st.markdown("---")
    st.title("🛠️ QUẢN TRỊ VIÊN (ADMIN)")
    
    tab1, tab2 = st.tabs(["👥 Thêm User Mới", "🔄 Đồng bộ Kịch bản"])
    
    with tab1:
        st.subheader("Tạo tài khoản khách hàng")
        with st.form("add_user_form"):
            new_email = st.text_input("Email khách")
            new_pass = st.text_input("Mật khẩu", type="password")
            col_u1, col_u2 = st.columns(2)
            with col_u1: new_plan = st.selectbox("Gói cước", ["free", "basic", "pro", "vip"])
            with col_u2: new_quota = st.number_input("Số video (Quota)", value=10)
            
            submitted = st.form_submit_button("Lưu User vào Supabase")
            
            if submitted:
                if not new_email or not new_pass:
                    st.warning("Điền thiếu thông tin!")
                else:
                    try:
                        # Mã hóa mật khẩu trước khi lưu
                        hashed = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
                        
                        data = {
                            "email": new_email,
                            "password": hashed,
                            "plan": new_plan,
                            "quota_max": new_quota,
                            "role": "user"
                        }
                        supabase.table('users').insert(data).execute()
                        st.success(f"✅ Đã tạo tài khoản: {new_email}")
                    except Exception as e:
                        st.error(f"Lỗi (có thể trùng email): {e}")

    with tab2:
        st.subheader("Cập nhật dữ liệu từ Google Sheet sang Supabase")
        st.info("Bấm nút dưới đây khi bạn vừa thêm kịch bản mới vào file Google Sheet.")
        if st.button("🚀 Bắt đầu Đồng bộ ngay"):
            sync_sheet_to_supabase()

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
    
    /* 2. Ẩn footer mặc định */
    footer {visibility: hidden; display: none;}
    
    /* 3. QUAN TRỌNG: Ẩn thanh 'Hosted with Streamlit' màu đỏ và Avatar */
    /* Lệnh này tìm mọi thành phần có tên chứa chữ 'viewerBadge' để ẩn đi */
    div[class*="viewerBadge"] {display: none !important;}
    
    /* 4. Ẩn luôn thanh trang trí 7 màu trên cùng (nếu có) */
    div[data-testid="stDecoration"] {display: none;}
    
    </style>
""", unsafe_allow_html=True)

# --- LOGIC MÀN HÌNH CHÍNH ---

if 'user_info' not in st.session_state:
    st.session_state['user_info'] = None

# [FIX] LOGIC TỰ ĐỘNG ĐĂNG NHẬP KHI F5 (Load lại trang)
if not st.session_state['user_info']:
    # Kiểm tra ngay lập tức xem trên URL có user/pass không
    params = st.query_params
    if "u" in params and "p" in params:
        # Tự động login lại
        user = check_login(params["u"], params["p"])
        if user:
            st.session_state['user_info'] = user
            # [NEW] Sau khi login lại thành công, tự động tải bản nháp về
            draft_content = load_draft_from_sheet(user['email'])
            if draft_content:
                 st.session_state['main_content_area'] = draft_content
            st.rerun()

# --- GIAO DIỆN ĐĂNG NHẬP ---
if not st.session_state['user_info']:
    # --- GIAO DIỆN ĐĂNG NHẬP (CARD STYLE) ---
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Bỏ hoàn toàn Toggle
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Luôn sử dụng tỷ lệ cột rộng cho người lớn tuổi
    c1, c2, c3 = st.columns([1, 10, 1])

    with c2:
        # Tạo khung card bao quanh form
        with st.container():
            st.markdown(f"<h2 style='text-align: center; color: #8B4513; margin-bottom: 20px;'>🔐 ĐĂNG NHẬP</h2>", unsafe_allow_html=True)
            
            # Form nhập liệu
            st.markdown("<br>", unsafe_allow_html=True) # Thêm khoảng trắng
            login_email = st.text_input("📧 Nhập Email", placeholder="vidu@gmail.com", key="login_email_unique")
            
            st.markdown("<br>", unsafe_allow_html=True) # Thêm khoảng trắng giữa email và pass
            login_pass = st.text_input("🔑 Mật khẩu", type="password", key="login_pass_unique")
            
            # Checkbox Ghi nhớ & Nút
            col_rem, col_btn = st.columns([1, 1])
            with col_rem:
                st.markdown("<br>", unsafe_allow_html=True)
                # [FIX] Mặc định luôn tích chọn để không bị đăng xuất
                remember_me = st.checkbox("Ghi nhớ đăng nhập", value=True)
            
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("ĐĂNG NHẬP NGAY", use_container_width=True):
                user = check_login(login_email, login_pass)
                if user:
                    st.session_state['user_info'] = user
                    if remember_me:
                        st.query_params["u"] = login_email
                        st.query_params["p"] = login_pass 
                    else:
                        st.query_params.clear()
                    st.toast("Đăng nhập thành công!", icon="🎉")
                    st.rerun()
                else:
                    st.error("Sai Email hoặc Mật khẩu, vui lòng thử lại.")
            



else:
    # ==========================================
    # KHI ĐÃ ĐĂNG NHẬP THÀNH CÔNG -> HIỆN UI CŨ
    # ==========================================
    user = st.session_state['user_info']
    
    # [MODIFIED] HEADER MỚI (Chỉ còn Tiêu đề)
    st.markdown(f"<h1 style='text-align: center; border: none; margin: 0; padding: 0;'>📻 Thợ video</h1>", unsafe_allow_html=True)
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
            ⛔ CẢNH BÁO QUAN TRỌNG:<br>
            Vì mật khẩu ở đây không quan trọng nên KHÔNG ĐƯỢC BẢO MẬT.<br>
            TUYỆT ĐỐI KHÔNG dùng mật khẩu Facebook, Gmail ... hay Ngân hàng tại đây.<br>
            Hãy dùng mật khẩu rác (Ví dụ: 123456, abcxyz).
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
            st.query_params.clear() 
            st.session_state['user_info'] = None
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
    if source_opt == "📂 Tìm trong Thư viện":
        st.info("💡 Nhập từ khóa để tìm kịch bản phù hợp.")
        
        # [FIX] Dùng st.form để hỗ trợ nhấn Enter là tự tìm kiếm
        with st.form(key="search_form"):
            c_search1, c_search2 = st.columns([3, 1], vertical_alignment="center")
            
            with c_search1:
                search_kw = st.text_input("", label_visibility="collapsed", placeholder="Ví dụ: Đức Phật, từ bi...")
            with c_search2:
                # Đổi button thường thành form_submit_button
                btn_search = st.form_submit_button("🔍 Tìm kiếm", use_container_width=True)

        # Logic cũ vẫn giữ nguyên, nhưng giờ nhấn Enter btn_search cũng sẽ là True
        if btn_search and search_kw:
            st.session_state['search_results'] = search_global_library(search_kw, user['stock_level'])
            st.session_state['has_searched'] = True
            
            # [FIX] QUAN TRỌNG: Xóa ký ức về lần chọn trước
            # Giúp máy nhận diện được kết quả mới dù chỉ có 1 bài (index 0)
            if 'last_picked_idx' in st.session_state:
                del st.session_state['last_picked_idx']
            
        # ... (Giữ nguyên logic hiển thị Selectbox) ...
        if st.session_state.get('has_searched'):
            results = st.session_state.get('search_results', [])
            if results:
                # ... (Code selectbox cũ giữ nguyên) ...
                preview_options = [f"({item['source_sheet']}) {str(item['content'])[:60]}..." for item in results]
                selected_idx = st.selectbox("Chọn kịch bản:", range(len(results)), format_func=lambda x: preview_options[x], key="sb_search_select")
                
                chosen_content = results[selected_idx]['content']
                selected_library_audio = results[selected_idx].get('audio')

                # Kiểm tra nếu người dùng chọn kịch bản KHÁC với lần trước
                if 'last_picked_idx' not in st.session_state or st.session_state['last_picked_idx'] != selected_idx:
                    st.session_state['main_content_area'] = chosen_content
                    st.session_state['last_picked_idx'] = selected_idx
                    
                    # [FIX] Xóa trạng thái của nút chọn giọng đọc để nó tự reset lại theo kịch bản mới
                    if "radio_voice_method" in st.session_state:
                        del st.session_state["radio_voice_method"]
                    
                    st.rerun()
                final_script_content = chosen_content
                
                # [ĐÃ XÓA] Đã bỏ phần nghe thử ở Bước 1 theo yêu cầu.
                # Biến selected_library_audio vẫn được giữ để dùng cho Bước 2.

            else:
                st.warning("⚠️ Không tìm thấy kịch bản nào.")

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
    voice_options = ["🎙️ Thu âm trực tiếp", "📤 Tải file lên"]
    
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
            if uploaded_file:
                st.session_state['temp_upload_file'] = uploaded_file
                st.session_state['temp_upload_name'] = uploaded_file.name
                st.success(f"Đã chọn: {uploaded_file.name}")

        # CASE 3: THU ÂM TRỰC TIẾP
        elif voice_method == "🎙️ Thu âm trực tiếp": 
            st.markdown("##### 🎙️ Bảng điều khiển thu âm")
            
            # Kiểm tra xem đã có file thu âm trong bộ nhớ chưa
            has_recording = 'temp_record_file' in st.session_state and st.session_state['temp_record_file'] is not None

            # KHU VỰC THU ÂM (Luôn hiện để có thể thu lại đè lên)
            if not has_recording:
                c_mic1, c_mic2 = st.columns([3, 1])
                with c_mic1:
                    st.info("💡GIỮ IM LẶNG 5 GIÂY ĐẦU")
                
                # Gọi thư viện mic_recorder mới
                audio_data = mic_recorder(
                    start_prompt="🔴 BẤM ĐỂ BẮT ĐẦU THU",
                    stop_prompt="⏹️ BẤM ĐỂ DỪNG THU",
                    just_once=True, 
                    use_container_width=True,
                    format="wav", 
                    key="new_mic_recorder"
                )
                
                if audio_data:
                    st.session_state['temp_record_file'] = audio_data['bytes']
                    st.session_state['temp_record_name'] = f"record_{datetime.now().strftime('%H%M%S')}.wav"
                    st.rerun()

            # KHU VỰC NGHE LẠI & XÁC NHẬN
            else:
                st.success("✅ Đã thu âm thành công!")
                st.audio(st.session_state['temp_record_file'], format="audio/wav")
                
                if st.button("🔄 Xóa và Thu lại", use_container_width=True, type="secondary"):
                    st.session_state['temp_record_file'] = None
                    st.rerun()
                    
                st.info("👇 Nếu đã ưng ý, hãy bấm nút **'🚀 GỬI YÊU CẦU TẠO VIDEO'** bên dưới.")
        
    # --- SETTINGS (Giữ nguyên code cũ) ---
    st.markdown("---")
    if 's_voice' not in st.session_state:
        st.session_state.update({
            "s_clean": True, "s_voice": 1.5, "s_music": 0.2, 
            "s_font": "Agbalumo", "s_size": 110, 
            "s_color": "#FFFFFF", "s_outline": "#000000", "s_border": 3,
            "s_margin": 650, "s_offset": 0
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
            st.form_submit_button("💾 LƯU CÀI ĐẶT")
    
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

        if not noi_dung_gui: st.toast("⚠️ Thiếu nội dung!", icon="⚠️")
        elif not ready_to_send: st.toast("⚠️ Thiếu file âm thanh!", icon="⚠️")
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
                # GHI ĐƠN HÀNG VÀO SHEET ORDERS
                # [BẢO MẬT] Làm sạch nội dung do người dùng nhập
                safe_noidung = sanitize_input(noi_dung_gui)
                
                # Cấu trúc: ID | Date | Email | Nguồn | Nội dung | Audio | Trạng thái | Link KQ | Cài đặt
                ws.append_row([
                    order_id, 
                    timestamp, 
                    user['email'], 
                    source_opt, 
                    safe_noidung, # <-- Đã thay bằng biến an toàn
                    final_audio_link_to_send, 
                    "Pending", 
                    "", 
                    json.dumps(settings)
                ])
                # [NEW] Ghi vào History
                log_history(order_id, user['email'], "", timestamp)
                
                # [NEW] Trừ Quota
                update_user_usage(user['row'], user['quota_used'])
                
                # Cập nhật session ngay lập tức
                st.session_state['user_info']['quota_used'] += 1
                st.session_state['submitted_order_id'] = order_id 
                
                # [MOI] Xóa cache lịch sử cũ & Bật thông báo chờ
                get_all_orders_cached.clear()
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
                get_all_orders_cached.clear() 
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
                    words = str(old_content_script).split()
                    script_preview = " ".join(words[:10]) + "..." if len(words) > 10 else str(old_content_script)
                except: script_preview = ""

                # Format ngày & Trạng thái
                try: display_date = pd.to_datetime(date_str).strftime('%d/%m/%Y - %H:%M')
                except: display_date = str(date_str)
                vn_status = status_map.get(raw_status, raw_status)

                # HIỂN THỊ EXPANDER
                with st.expander(f"{display_date} | {vn_status} | 📝 {script_preview}"):
                    # A. Nếu có link kết quả -> Hiện nút Xem & Tải
                    if result_link and str(result_link).startswith("http"):
                        # Fix link tải cho iOS
                        dl_link = result_link.replace("/upload/", "/upload/fl_attachment/") if "cloudinary" in str(result_link) else result_link
                        
                        col_btn1, col_btn2 = st.columns([1, 1], gap="small")
                        btn_style = "width: 100%; padding: 10px; border-radius: 8px; text-align: center; font-weight: bold; text-decoration: none; display: block; box-shadow: 0 2px 3px rgba(0,0,0,0.1);"
                        
                        with col_btn1:
                            st.markdown(f'<a href="{result_link}" target="_blank" style="{btn_style} background-color: #8D6E63; color: white;">▶️ XEM VIDEO</a>', unsafe_allow_html=True)
                        with col_btn2:
                            st.markdown(f'<a href="{dl_link}" target="_self" style="{btn_style} background-color: #5D4037; color: white;">📥 TẢI VỀ MÁY</a>', unsafe_allow_html=True)
                    
                    elif raw_status == "Error":
                        st.error("Video này bị lỗi xử lý.")
                    else:
                        st.info("Hệ thống đang xử lý...")

                    # B. Nút Tạo lại (Re-create)
                    st.markdown('<div style="margin-top: 5px;"></div>', unsafe_allow_html=True) 
                    if old_audio_link and str(old_audio_link).startswith("http"):
                        # [FIX] Thêm _{index} vào key để đảm bảo không bao giờ bị trùng
                        if st.button(f"♻️ Tạo lại bằng Audio này", key=f"recreate_{order_id}_{index}", disabled=is_out_of_quota, use_container_width=True):
                            if not is_out_of_quota:
                                try:
                                    with st.spinner("Đang gửi lệnh tạo lại..."):
                                        gc = get_gspread_client()
                                        ws = gc.open(DB_SHEET_NAME).worksheet(DB_WORKSHEET)
                                        # Tạo ID mới
                                        now_vn = datetime.utcnow() + timedelta(hours=7)
                                        new_id = now_vn.strftime("%Y%m%d_%H%M%S")
                                        ws.append_row([new_id, now_vn.strftime("%Y-%m-%d %H:%M:%S"), user['email'], "Re-created", old_content_script, old_audio_link, "Pending", "", json.dumps(settings)])
                                        
                                        # Log & Update Quota
                                        log_history(new_id, user['email'], "", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
                                        update_user_usage(user['row'], user['quota_used'])
                                        st.session_state['user_info']['quota_used'] += 1
                                        get_all_orders_cached.clear()
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
    
    
