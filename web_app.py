# FILE: cloud_bridge.py (VERSION 4.2 - FIXED AUDIO & DEBUG OFFSET)
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os
import sys
import json
import smtplib
import shutil
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# [MỚI] Thư viện xử lý âm thanh nâng cao
import numpy as np
import soundfile as sf
import noisereduce as nr
from scipy import signal
from scipy.ndimage import median_filter


# --- CẤU HÌNH ---
SHEET_ID = "1htiy__uXZsG9KXREcbmxO5JlfPLMnRECSCx2QKgnHAc"  
SENDER_EMAIL = "henrytruong.2016@gmail.com" 
APP_PASSWORD = "fvjl zzlw njpg ojkd"

# --- HỆ THỐNG ---
CREDENTIALS_FILE = 'credentials.json'
WORKSHEET_NAME = "orders"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_DOWNLOAD_DIR = os.path.join(BASE_DIR, "voice", "cloud_orders")
OUTPUT_VIDEO_DIR = os.path.join(BASE_DIR, "output_video_clips")

if not os.path.exists(VOICE_DOWNLOAD_DIR): os.makedirs(VOICE_DOWNLOAD_DIR)

sys.path.append(BASE_DIR)
try:
    from video_maker_remix import VideoRemixProcessor
    from subtitle_gen import ExcelSubtitleGenerator
except ImportError as e:
    print(f"❌ Thiếu file: {e}"); sys.exit()

DUMMY_EXCEL = os.path.join(BASE_DIR, "dummy_bridge.xlsx")
if not os.path.exists(DUMMY_EXCEL):
    import openpyxl; wb = openpyxl.Workbook(); wb.save(DUMMY_EXCEL)

sub_gen_engine = ExcelSubtitleGenerator(DUMMY_EXCEL, VOICE_DOWNLOAD_DIR)
sub_gen_engine.load_resources()

def download_file(url, save_path):
    headers = {'User-Agent': 'Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36'}
    for attempt in range(3):
        try:
            r = requests.get(url, stream=True, headers=headers, timeout=30)
            if r.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(1024): f.write(chunk)
                return True
        except: time.sleep(3)
    return False

def upload_to_catbox(file_path):
    # [NÂNG CẤP] Backend trả file qua Cloudinary
    # --- CẤU HÌNH GIỐNG BÊN WEB ---
    
    CLOUD_NAME = "dsaiot45b"  
    UPLOAD_PRESET = "aicunglamvideo"   

    # ------------------------------
    
    try:
        url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/video/upload"
        
        with open(file_path, 'rb') as f:
            data = {"upload_preset": UPLOAD_PRESET}
            files = {"file": (os.path.basename(file_path), f)}
            
            r = requests.post(url, data=data, files=files, timeout=120)
            
            if r.status_code == 200:
                return r.json()['secure_url']
            else:
                print(f"   ❌ Lỗi Cloudinary: {r.text}")
    except Exception as e:
        print(f"   ❌ Lỗi upload video: {e}")
    return None

def send_email(to_email, link, order_id):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = f"✅ Video hoàn tất (Đơn {order_id})"

        # --- NỘI DUNG HTML GIAO DIỆN VINTAGE (CÓ HƯỚNG DẪN TẢI) ---
        html_content = f"""
        <html>
          <body style="background-color: #FDF5E6; padding: 20px;">
            <div style="font-family: 'Georgia', serif; color: #3E2723; max-width: 600px; margin: auto; background-color: #FFF8DC; padding: 20px; border: 2px solid #8B4513; border-radius: 10px;">
                <h2 style="color: #8B4513; text-align: center; border-bottom: 3px double #8B4513; padding-bottom: 10px;">📻 Ai cũng làm video được</h2>
                
                <p style="font-size: 16px;">Xin chào,</p>
                <p style="font-size: 16px;">Đơn hàng <strong>{order_id}</strong> của bạn đã hoàn thành!</p>
                <p>Mời bạn tải video gốc về máy:</p>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{link}" download="Video_{order_id}.mp4" target="_blank" style="background-color: #8B4513; color: #FFFFFF; text-decoration: none; padding: 15px 30px; font-weight: bold; font-size: 18px; border-radius: 8px; box-shadow: 3px 3px 5px rgba(0,0,0,0.3); display: inline-block;">
                       📥 TẢI VIDEO NGAY
                    </a>
                </div>

                <div style="background-color: #EFEBE9; padding: 10px; border-left: 4px solid #8B4513; font-size: 14px; margin-bottom: 20px;">
                    <strong>💡 Mẹo nhỏ:</strong><br>
                    Do trình duyệt thường tự phát video thay vì tải, nếu bấm nút trên mà không thấy tải xuống:<br>
                    👉 Hãy <b>Bấm chuột phải</b> vào nút trên và chọn <b>"Lưu liên kết thành..." (Save link as)</b>.
                </div>

                <hr style="border: 1px dashed #8B4513; margin: 20px 0;">
                <p style="font-size: 13px; color: #5D4037;">Link dự phòng (Copy dán vào trình duyệt):</p>
                <p style="font-size: 12px; word-break: break-all;"><a href="{link}" style="color: #8B4513;">{link}</a></p>
                
                <p style="text-align: center; font-size: 12px; color: #888; margin-top: 30px;">
                    Cảm ơn bạn đã tin tưởng dịch vụ.<br>
                    (Email tự động từ hệ thống)
                </p>
            </div>
          </body>
        </html>
        """
        
        msg.attach(MIMEText(html_content, 'html'))

        s = smtplib.SMTP('smtp.gmail.com', 587)
        s.starttls()
        s.login(SENDER_EMAIL, APP_PASSWORD)
        s.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        s.quit()
        print(f"   📧 Đã gửi email tới: {to_email}")
        
    except Exception as e:
        print(f"   ❌ Lỗi gửi email: {e}")


# --- CÁC HÀM XỬ LÝ DSP (Được port từ Voice Recorder) ---
def dsp_enhance_voice(audio, sr):
    # --- GIAI ĐOẠN 1: PHÂN TÍCH GIỌNG (NAM HAY NỮ?) ---
    # Cắt 1 đoạn ở giữa file để phân tích cho chính xác
    try:
        check_chunk = audio[int(len(audio)/3) : int(len(audio)*2/3)]
        if len(check_chunk) > 0:
            # Dùng thuật toán Welch để tìm tần số trội nhất
            freqs, psd = signal.welch(check_chunk, sr, nperseg=2048)
            # Chỉ quét trong vùng giọng người (80Hz - 300Hz)
            valid_idx = np.where((freqs >= 80) & (freqs <= 300))[0]
            if len(valid_idx) > 0:
                peak_freq = freqs[valid_idx][np.argmax(psd[valid_idx])]
            else:
                peak_freq = 200 # Không tìm thấy thì mặc định trung tính
        else:
            peak_freq = 200
    except:
        peak_freq = 200

    print(f"   📊 AI Phân tích chất giọng: ~{int(peak_freq)}Hz ({'Giọng Trầm/Nam' if peak_freq < 165 else 'Giọng Cao/Nữ'})")

    # --- GIAI ĐOẠN 2: XỬ LÝ RIÊNG BIỆT ---
    
    if peak_freq < 165:
        # === KỊCH BẢN A: GIỌNG NAM (TRẦM) ===
        # Vấn đề thường gặp: Bị ồm, đục, thiếu độ sáng.
        
        # 1. Cắt mạnh dải "Hộp" (300Hz) để hết ồm
        try:
            b, a = signal.iirpeak(300, 2.0, fs=sr)
            audio = audio - (signal.lfilter(b, a, audio) * 0.7) 
        except: pass

        # 2. Tăng độ nảy (Presence) ở 4000Hz cho rõ lời
        try:
            b, a = signal.iirpeak(4000, 1.0, fs=sr)
            audio = audio + (signal.lfilter(b, a, audio) * 0.4)
        except: pass
        
        # 3. Boost nhẹ Sub-bass (85Hz) cho dày (nhưng ít thôi kẻo ù)
        try:
            b, a = signal.iirpeak(85, 1.0, fs=sr)
            audio = audio + (signal.lfilter(b, a, audio) * 0.8)
        except: pass

    else:
        # === KỊCH BẢN B: GIỌNG NỮ (CAO) ===
        # Vấn đề thường gặp: Mỏng, chói, thiếu lực.
        
        # 1. Bơm mạnh độ ấm (Warmth - 220Hz) -> Quan trọng nhất cho giọng nữ
        try:
            b, a = signal.iirpeak(220, 0.8, fs=sr)
            audio = audio + (signal.lfilter(b, a, audio) * 1.5) 
        except: pass

        # 2. Giảm gắt (De-ess) ở dải cao (6000Hz)
        try:
            b, a = signal.iirpeak(6000, 1.5, fs=sr)
            audio = audio - (signal.lfilter(b, a, audio) * 0.3)
        except: pass

    # --- GIAI ĐOẠN 3: NÉN ĐỒNG BỘ (COMPRESSION) ---
    # [ĐÃ SỬA] Hệ số 1.2: Giúp làm dày giọng nhẹ nhàng, KHÔNG gây rè
    return np.tanh(audio * 1.2)

def dsp_smart_trim(audio, sr):
    # CHẾ ĐỘ CẮT AN TOÀN (SAFE TRIM)
    # [ĐÃ SỬA] Tăng ngưỡng lên 0.02 để loại bỏ tiếng xì nền (Noise floor)
    threshold = 0.02
    
    try:
        # Tìm tất cả những điểm có tiếng nói
        non_silent_indices = np.where(np.abs(audio) > threshold)[0]
        
        if non_silent_indices.size > 0:
            # Lấy điểm đầu tiên có tiếng, lùi lại 0.5 giây để giữ hơi thở đầu
            start_index = max(0, non_silent_indices[0] - int(0.5 * sr))
            
            # Lấy điểm cuối cùng có tiếng, cộng thêm 0.5 giây để giữ đuôi
            end_index = min(len(audio), non_silent_indices[-1] + int(0.5 * sr))
            
            return audio[start_index : end_index]
        
        return audio # Nếu không tìm thấy gì thì giữ nguyên
    except:
        return audio

def dsp_shorten_silence(audio, sr):
    # Cắt bớt khoảng lặng GIỮA câu
    frame_len = int(0.02 * sr)
    thresh_lin = 10 ** (-45 / 20)
    
    n_frames = len(audio) // frame_len
    energies = np.array([np.max(np.abs(audio[i*frame_len:(i+1)*frame_len])) for i in range(n_frames)])
    smoothed = median_filter(energies, size=5)
    is_speech = smoothed > thresh_lin
    
    output = []
    silence_count = 0
    min_silence = int(1.0 / 0.02) # >1s là khoảng lặng dài
    keep_silence = int(0.5 / 0.02) # Giữ lại 0.5s thôi
    
    buf = []
    for i, speech in enumerate(is_speech):
        chunk = audio[i*frame_len : (i+1)*frame_len]
        if speech:
            if silence_count > 0:
                add = keep_silence if silence_count > min_silence else silence_count
                for f in buf[-add:]: output.append(f)
                silence_count = 0
                buf = []
            output.append(chunk)
        else:
            silence_count += 1
            buf.append(chunk)
            
    if output: return np.concatenate(output)
    return audio

def process_audio_studio(input_path):
    # Logic mới: Dùng Python libraries để xử lý Studio xịn hơn FFmpeg thuần
    temp_wav = input_path.replace(".mp3", "_temp.wav")
    
    try:
        print("   🎙️ Đang convert sang WAV để xử lý...")
        # 1. Convert MP3 -> WAV (để đọc bằng SoundFile dễ hơn)
        os.system(f'ffmpeg -y -i "{input_path}" -ar 48000 -ac 1 "{temp_wav}" -loglevel error')
        
        if not os.path.exists(temp_wav): return False

        # 2. Đọc file
        audio, sr = sf.read(temp_wav)
        if audio.dtype == np.int16: audio = audio.astype(np.float32) / 32768.0

        # [LOGIC THÔNG MINH MỚI] Kiểm tra file đã xử lý chưa?
        # Nếu trong 0.8 giây đầu mà âm lượng lớn (có tiếng nói ngay) -> BỎ QUA XỬ LÝ
        try:
            check_chunk = audio[:int(0.8 * sr)] # Lấy mẫu 0.8 giây đầu
            if len(check_chunk) > 0:
                max_vol = np.max(np.abs(check_chunk))
                # Ngưỡng 0.05 là đủ lớn để xác định là tiếng người nói (không phải noise nền)
                if max_vol > 0.05:
                    print(f"   ✨ Phát hiện file chuẩn (Nói ngay đầu) -> BỎ QUA XỬ LÝ (Giữ nguyên gốc).")
                    if os.path.exists(temp_wav): os.remove(temp_wav)
                    return True # Trả về True ngay, không lọc, không cắt nữa
        except Exception as e:
            print(f"   ⚠️ Lỗi kiểm tra nhanh: {e}")

        # 3. KHỬ ỒN NHẸ (Noise Reduction)
        print("   🧹 Đang khử ồn cực nhẹ (giữ chất giọng)...")
        try:
            # Lấy 0.5s đầu làm mẫu
            noise_part = audio[:int(0.5*sr)] 
            audio = nr.reduce_noise(y=audio, sr=sr, y_noise=noise_part, prop_decrease=0.15, n_jobs=1)
        except: pass

        # [QUAN TRỌNG] 4. CẮT GỌT NGAY LẬP TỨC (Chuyển lên trên)
        # Phải cắt khoảng lặng/tiếng xì TRƯỚC khi tăng âm lượng, nếu không tiếng xì sẽ bị to lên
        print("   ✂️ Đang cắt khoảng lặng đầu/cuối...")
        audio = dsp_smart_trim(audio, sr)

        # 5. NORMALIZE (Kéo to âm lượng chuẩn)
        print("   🔊 Đang cân bằng âm lượng...")
        peak = np.max(np.abs(audio))
        if peak > 0:
            target_amp = 10 ** (-3.0 / 20) 
            audio = audio * (target_amp / peak)

        # 6. TỐI ƯU GIỌNG (EQ & Saturation)
        print("   🎚️ Đang làm ấm giọng & EQ...")
        audio = dsp_enhance_voice(audio, sr)
        


        # 7. NORMALIZE LẦN CUỐI (Chốt hạ output chuẩn -1.5dB)
        # Cần làm lại lần nữa vì quá trình EQ có thể làm thay đổi Gain
        peak_final = np.max(np.abs(audio))
        if peak_final > 0:
            target_amp_final = 10 ** (-1.5 / 20)
            audio = audio * (target_amp_final / peak_final)

        # 7. Xuất ra file WAV đã xử lý
        processed_wav = input_path.replace(".mp3", "_processed.wav")
        sf.write(processed_wav, audio, sr)

        # 8. Convert ngược lại MP3 đè lên file gốc
        cmd = f'ffmpeg -y -i "{processed_wav}" -acodec libmp3lame -b:a 192k "{input_path}" -loglevel error'
        os.system(cmd)

        # Dọn dẹp
        if os.path.exists(temp_wav): os.remove(temp_wav)
        if os.path.exists(processed_wav): os.remove(processed_wav)
        
        print("   ✅ Xử lý Studio hoàn tất!")
        return True

    except Exception as e:
        print(f"   ❌ Lỗi xử lý Audio Python: {e}")
        # Nếu lỗi thì giữ nguyên file gốc, không crash
        return False

def process_order(ws, row_idx, row):
    order_id = row['ID']
    link_voice = row['LinkGiongNoi']
    raw_script = row['NoiDung']
    
    print(f"\n⚡ XỬ LÝ ĐƠN: {order_id}")
    ws.update_cell(row_idx, 7, "Processing")
    
    s = {}
    json_str = ""
    if 'CauHinh' in row and row['CauHinh']: json_str = row['CauHinh']
    else:
        for key, val in row.items():
            if isinstance(val, str) and val.startswith('{') and '"font_name"' in val:
                json_str = val; break
    
    if json_str:
        try: s = json.loads(json_str)
        except: print("   ❌ Lỗi JSON")

    # Map cấu hình
    render_config = {
        "output_path": OUTPUT_VIDEO_DIR,
        "source_path": os.path.join(BASE_DIR, "video background"),
        "voice_dir": os.path.join(BASE_DIR, "voice"),
        "music_path": "", 
        "render_subs": True,
        "sheet_name": "cloud_orders",
        "voice_vol": float(s.get('voice_vol', 1.5)),
        "music_vol": float(s.get('music_vol', 0.2)),
        "fontname": s.get('font_name', 'Agbalumo'),
        "fontsize": int(s.get('font_size', 90)),
        "max_chars": 20, 
        "primary_color": s.get('text_color', '#FFFFFF'),
        "text_color": s.get('text_color', '#FFFFFF'),
        "outline_color": s.get('outline_color', '#000000'),
        "border_width": int(s.get('border_width', 3)),
        "margin_v": int(s.get('margin_v', 650)),
        
        # [QUAN TRỌNG] Nhận giá trị offset_x từ Web
        "offset_x": int(s.get('offset_x', 0)),
    }

    # [DEBUG] In ra để kiểm tra xem Web có gửi offset_x không
    print(f"   ⚙️ Config: Font={render_config['fontname']} | Size={render_config['fontsize']} | V_Pos={render_config['margin_v']} | H_Pos (Lệch Ngang)={render_config['offset_x']}")
    
    local_voice_path = os.path.join(VOICE_DOWNLOAD_DIR, f"{order_id}.mp3")
    
    if download_file(link_voice, local_voice_path):
        if s.get('clean_audio', False):
            print("   🎙️ Studio Mode: ON")
            process_audio_studio(local_voice_path)
        
        print("   📝 Creating Subtitles...")
        sub_ok, srt_path = sub_gen_engine.generate_srt(
            local_voice_path, raw_script, max_chars_per_line=20 
        )
        
        print("   🎬 Rendering Video...")
        music_dir = os.path.join(BASE_DIR, "music")
        if os.path.exists(music_dir):
            import random
            songs = [f for f in os.listdir(music_dir) if f.endswith('mp3')]
            if songs: render_config["music_path"] = os.path.join(music_dir, random.choice(songs))

        proc = VideoRemixProcessor(BASE_DIR, render_config)
        
        if proc.create_video_from_audio(local_voice_path, "cloud_orders"):
            local_video = os.path.join(OUTPUT_VIDEO_DIR, "cloud_orders", f"{order_id}.mp4")
            link_kq = upload_to_catbox(local_video)
            if link_kq:
                ws.update_cell(row_idx, 7, "Done")
                ws.update_cell(row_idx, 8, link_kq)
                send_email(row['Email'], link_kq, order_id)
                print(f"   🎉 XONG! Link: {link_kq}")
                return

    print("   ❌ Thất bại.")

if __name__ == "__main__":
    print("🤖 BRIDGE V4.2 ĐANG CHẠY...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    while True:
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
            client = gspread.authorize(creds)
            ws = client.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
            records = ws.get_all_records()
            for i, r in enumerate(records):
                if r['TrangThai'] == "Pending": process_order(ws, i+2, r)
        except Exception as e: print(f"⚠️ Chờ kết nối: {e}")
        time.sleep(10)
