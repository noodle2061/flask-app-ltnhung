# app/udp_server.py
import socket
import logging # <<< Đã thêm ở lần sửa trước
import torch   # <<< THÊM DÒNG NÀY
import threading
import time
import io # Để xử lý byte stream trong bộ nhớ
from collections import defaultdict, deque
import numpy as np
# import torch # Đã import ở trên
import soundfile as sf # Để lưu tensor thành file WAV
import boto3 # Để tương tác với AWS S3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

from . import config
from . import ml_handler # Import module xử lý ML
from . import firebase_client # Import module Firebase để gửi thông báo VÀ ghi DB
# token_storage không cần import trực tiếp ở đây nữa

_stop_udp = threading.Event()

# --- Cấu trúc dữ liệu mới để lưu trữ lịch sử ---
_prediction_history = defaultdict(lambda: deque(maxlen=int(config.SCREAM_FREQUENCY_WINDOW_S / config.AUDIO_CHUNK_DURATION_S) * 2))
_audio_chunk_history = defaultdict(lambda: deque(maxlen=int(config.AUDIO_SAVE_DURATION_S / config.AUDIO_CHUNK_DURATION_S) + 5))
_last_alert_times = defaultdict(float)
_audio_buffers = defaultdict(lambda: torch.tensor([], dtype=torch.float32))
_buffer_lock = threading.Lock()

# --- Hàm trợ giúp S3 ---
_s3_client = None
if config.S3_CONFIGURED:
    try:
        _s3_client = boto3.client(
            's3',
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_S3_REGION
        )
        logging.info("S3 Client initialized successfully.")
    except (NoCredentialsError, PartialCredentialsError) as e:
        logging.error(f"S3 Initialization Error: AWS credentials not found or incomplete. {e}")
        _s3_client = None
    except Exception as e:
        logging.error(f"S3 Initialization Error: An unexpected error occurred. {e}", exc_info=True)
        _s3_client = None
else:
    logging.warning("S3 Client not initialized because S3 configuration is incomplete in config.")

def save_tensor_to_wav_bytes(audio_tensor: torch.Tensor, sample_rate: int) -> bytes | None:
    """Chuyển đổi tensor audio float [-1, 1] thành bytes WAV."""
    if audio_tensor is None or audio_tensor.nelement() == 0:
        return None
    try:
        if audio_tensor.ndim > 1:
            audio_tensor = audio_tensor.squeeze()
        # Chuyển đổi tensor float [-1, 1] sang int16
        # Đảm bảo clip giá trị để tránh lỗi tràn số khi nhân
        audio_np = torch.clamp(audio_tensor * 32767, -32768, 32767).cpu().numpy().astype(np.int16)
        buffer = io.BytesIO()
        sf.write(buffer, audio_np, sample_rate, format='WAV', subtype='PCM_16')
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        logging.error(f"Error converting tensor to WAV bytes: {e}", exc_info=True)
        return None

def upload_audio_to_s3(audio_bytes: bytes, client_ip: str, timestamp: float) -> tuple[str | None, str | None]:
    """Tải dữ liệu audio bytes lên S3 và trả về (s3_key, pre-signed_url)."""
    if not _s3_client or not config.S3_CONFIGURED:
        logging.error("S3 client not available or not configured. Cannot upload audio.")
        return None, None
    if not audio_bytes:
        logging.error("No audio bytes provided to upload.")
        return None, None
    try:
        # Sử dụng timestamp để đảm bảo tên file duy nhất
        s3_key = f"{config.AWS_S3_AUDIO_FOLDER}scream_{client_ip.replace('.', '-')}_{int(timestamp)}.wav"
        bucket_name = config.AWS_S3_BUCKET_NAME

        # Tải file lên S3
        _s3_client.put_object(Bucket=bucket_name, Key=s3_key, Body=audio_bytes, ContentType='audio/wav')
        logging.info(f"Successfully uploaded audio to s3://{bucket_name}/{s3_key}")

        # Tạo pre-signed URL để gửi trong thông báo FCM (có thể hết hạn)
        presigned_url = _s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=config.AWS_S3_URL_EXPIRATION_S # Thời gian hết hạn URL
        )
        # Trả về cả key (để lưu vào DB) và URL (để gửi đi)
        return s3_key, presigned_url
    except ClientError as e:
        logging.error(f"S3 ClientError during upload/presign URL generation: {e}", exc_info=True)
        return None, None
    except Exception as e:
        logging.error(f"Unexpected error during S3 upload: {e}", exc_info=True)
        return None, None

def calculate_rms(audio_chunk_tensor: torch.Tensor) -> float:
    """Tính giá trị Root Mean Square (RMS) cho một chunk audio tensor."""
    if audio_chunk_tensor is None or audio_chunk_tensor.nelement() == 0:
        return 0.0
    try:
        # Công thức RMS: sqrt(mean(samples^2))
        # Thêm epsilon nhỏ để tránh log(0) hoặc sqrt(0) nếu chunk hoàn toàn im lặng
        rms_val = torch.sqrt(torch.mean(audio_chunk_tensor**2) + 1e-10)
        # Kết quả RMS cho tensor float [-1, 1] sẽ nằm trong khoảng [0, 1]
        return rms_val.item() # Trả về giá trị float
    except Exception as e:
        logging.error(f"Error calculating RMS: {e}", exc_info=True)
        return 0.0

def _process_audio_data(data_bytes, client_address):
    """
    Xử lý dữ liệu audio nhận được từ một client (ESP32).
    Tính RMS, gửi lên Firebase DB, áp dụng logic phát hiện phức tạp và gửi cảnh báo.
    """
    global _audio_buffers, _prediction_history, _audio_chunk_history, _last_alert_times, _buffer_lock

    client_ip = client_address[0]
    num_bytes_received = len(data_bytes)

    if num_bytes_received == 0: return
    if num_bytes_received % config.AUDIO_BYTES_PER_SAMPLE != 0:
        logging.warning(f"UDP Server: From {client_ip}, received {num_bytes_received} bytes, "
                        f"not a multiple of {config.AUDIO_BYTES_PER_SAMPLE} bytes/sample. Skipping packet.")
        return

    try:
        # Chuyển đổi bytes thành numpy array rồi thành tensor float
        samples_np = np.frombuffer(data_bytes, dtype=config.AUDIO_NUMPY_DTYPE)
        # Chuẩn hóa về khoảng [-1.0, 1.0] dựa trên kiểu dữ liệu int32 (2**31)
        audio_tensor_float = torch.from_numpy(samples_np.astype(np.float32) / (2**31)).float()

        with _buffer_lock:
            # Nối dữ liệu mới vào buffer của client tương ứng
            _audio_buffers[client_ip] = torch.cat((_audio_buffers[client_ip], audio_tensor_float))
            current_buffer = _audio_buffers[client_ip]

            # Xử lý từng chunk hoàn chỉnh trong buffer
            while len(current_buffer) >= config.AUDIO_CHUNK_SAMPLES:
                process_chunk = current_buffer[:config.AUDIO_CHUNK_SAMPLES]
                current_buffer = current_buffer[config.AUDIO_CHUNK_SAMPLES:]
                _audio_buffers[client_ip] = current_buffer # Cập nhật buffer còn lại

                # --- Tính RMS và gửi lên Firebase DB (ngoài lock) ---
                current_time_for_rms = time.time()
                rms_value = calculate_rms(process_chunk)
                # Gọi hàm ghi lên Firebase (có thể là RTDB hoặc Firestore tùy cấu hình)
                firebase_client.write_audio_level(client_ip, rms_value, current_time_for_rms)
                # --- Kết thúc tính RMS và gửi DB ---

                # --- Thực hiện dự đoán (ngoài lock) ---
                _buffer_lock.release() # Tạm thời nhả lock khi chạy ML
                try:
                    prediction, confidence = ml_handler.predict_scream(process_chunk)
                finally:
                    _buffer_lock.acquire() # Lấy lại lock sau khi dự đoán xong
                # --- Kết thúc dự đoán ---

                current_time = time.time() # Lấy lại thời gian sau khi dự đoán

                # --- Cập nhật lịch sử và kiểm tra điều kiện (trong lock) ---
                # Lưu chunk audio (trên CPU để tiết kiệm bộ nhớ GPU nếu có) và kết quả dự đoán
                _audio_chunk_history[client_ip].append((current_time, process_chunk.cpu()))
                _prediction_history[client_ip].append((current_time, prediction))

                # Xóa dữ liệu cũ trong history để giới hạn bộ nhớ
                prediction_window_start_time = current_time - config.SCREAM_FREQUENCY_WINDOW_S
                while _prediction_history[client_ip] and _prediction_history[client_ip][0][0] < prediction_window_start_time:
                     _prediction_history[client_ip].popleft()
                audio_save_window_start_time = current_time - config.AUDIO_SAVE_DURATION_S - 5 # Giữ thêm buffer
                while _audio_chunk_history[client_ip] and _audio_chunk_history[client_ip][0][0] < audio_save_window_start_time:
                     _audio_chunk_history[client_ip].popleft()

                # Kiểm tra điều kiện cảnh báo phức tạp
                recent_predictions_in_window = list(_prediction_history[client_ip])
                max_consecutive_in_window = 0
                current_consecutive = 0
                for _, pred_label in recent_predictions_in_window:
                    if pred_label == 'Hét': current_consecutive += 1
                    else:
                        max_consecutive_in_window = max(max_consecutive_in_window, current_consecutive)
                        current_consecutive = 0
                max_consecutive_in_window = max(max_consecutive_in_window, current_consecutive)
                condition1_met = max_consecutive_in_window >= config.SCREAM_MIN_CONSECUTIVE_CHUNKS

                total_screams_in_window = sum(1 for _, pred_label in recent_predictions_in_window if pred_label == 'Hét')
                condition2_met = total_screams_in_window >= config.SCREAM_FREQUENCY_COUNT

                # Log chi tiết trạng thái (hữu ích cho debug)
                log_message = (
                    f"UDP Server: Chunk from {client_ip} - RMS: {rms_value:.3f}, Prediction: {prediction} ({confidence*100:.1f}%). "
                    f"Status in {config.SCREAM_FREQUENCY_WINDOW_S}s window: "
                    f"Consecutive: {max_consecutive_in_window}/{config.SCREAM_MIN_CONSECUTIVE_CHUNKS}, "
                    f"Total: {total_screams_in_window}/{config.SCREAM_FREQUENCY_COUNT}."
                )
                # Chỉ log INFO nếu là hét hoặc lỗi, còn lại là DEBUG để tránh spam log
                if prediction == 'Hét' or prediction is None: logging.info(log_message)
                else: logging.debug(log_message)

                # --- Logic Xử lý Cảnh báo ---
                last_alert_time = _last_alert_times.get(client_ip, 0.0)
                # Kiểm tra cả 2 điều kiện và thời gian cooldown
                if condition1_met and condition2_met and (current_time - last_alert_time > config.SCREAM_ALERT_COOLDOWN_S):
                    logging.warning(f"--- !!! Complex Scream Pattern Detected from {client_ip} !!! ---")

                    # Lấy audio chunks cần lưu TRONG LOCK
                    save_window_start_time = current_time - config.AUDIO_SAVE_DURATION_S
                    audio_to_save_list = [chunk for ts, chunk in list(_audio_chunk_history[client_ip]) if ts >= save_window_start_time]

                    # Gán last_alert_time NGAY LẬP TỨC trong lock để tránh gửi nhiều lần khi xử lý S3/FCM chậm
                    _last_alert_times[client_ip] = current_time

                    # Nhả lock SAU KHI lấy dữ liệu cần thiết và cập nhật last_alert_time
                    _buffer_lock.release()

                    # --- Xử lý S3 và Gửi Thông báo (ngoài lock) ---
                    audio_s3_key = None
                    audio_presigned_url = None
                    try:
                        if audio_to_save_list:
                            # Nối các chunk lại thành một tensor lớn
                            full_audio_tensor = torch.cat(audio_to_save_list)
                            # Chuyển tensor thành bytes WAV
                            audio_bytes = save_tensor_to_wav_bytes(full_audio_tensor, config.AUDIO_SAMPLE_RATE)
                            if audio_bytes:
                                 # Tải lên S3 và lấy về key + URL tạm thời
                                 audio_s3_key, audio_presigned_url = upload_audio_to_s3(audio_bytes, client_ip, current_time)
                                 if audio_s3_key: logging.info(f"Uploaded audio segment to S3 key: {audio_s3_key}")
                                 else: logging.error("Failed to upload audio segment to S3.")
                            else: logging.error("Failed to convert audio tensor to WAV bytes.")
                        else: logging.warning("No audio data found in the save window to upload.")

                        # Chuẩn bị payload và gửi FCM
                        alert_title = config.HIGH_FREQUENCY_ALERT_TITLE
                        alert_body = config.HIGH_FREQUENCY_ALERT_BODY_TEMPLATE.format(total_screams_in_window, config.SCREAM_FREQUENCY_WINDOW_S, client_ip)
                        payload = {"type": "complex_scream", "ip": client_ip}
                        if audio_presigned_url: # Gửi URL tạm thời trong FCM data
                            payload["audio_url"] = audio_presigned_url
                        if audio_s3_key: # Thêm s3_key vào payload nếu muốn client biết (tùy chọn)
                            payload["s3_key"] = audio_s3_key

                        # Gửi thông báo đến tất cả client đã đăng ký
                        success = firebase_client.send_alert_to_all(alert_title, alert_body, data=payload)

                        # Ghi log lịch sử vào Firestore (ngay cả khi gửi FCM thất bại)
                        firebase_client.log_alert_to_firestore(client_ip, audio_s3_key) # Truyền s3_key (có thể là None)

                        if success:
                            logging.info(f"Sent complex scream alert for {client_ip} to devices.")
                        else:
                            logging.error(f"Failed to send complex scream alert for {client_ip}.")

                    except Exception as alert_err:
                        logging.error(f"Error during S3 upload or sending alert for {client_ip}: {alert_err}", exc_info=True)
                    finally:
                        _buffer_lock.acquire() # Lấy lại lock sau khi xử lý xong

                # Trường hợp đủ điều kiện nhưng đang trong thời gian cooldown
                elif condition1_met and condition2_met:
                     logging.info(f"Complex scream pattern conditions met for {client_ip}, but within cooldown period. Alert not sent.")
                # --- Kết thúc cập nhật lịch sử và kiểm tra ---

    except ValueError as e:
         # Lỗi khi chuyển đổi bytes sang numpy (ví dụ: sai dtype)
         logging.error(f"UDP Server: ValueError processing data from {client_ip}. Corrupted data or wrong dtype? {e}", exc_info=True)
    except Exception as e:
        # Các lỗi nghiêm trọng khác trong quá trình xử lý
        logging.error(f"UDP Server: Critical error processing data from {client_ip}: {e}", exc_info=True)
        # Xóa buffer và lịch sử của client này để tránh lỗi lặp lại
        with _buffer_lock:
            if client_ip in _audio_buffers: del _audio_buffers[client_ip]
            if client_ip in _prediction_history: del _prediction_history[client_ip]
            if client_ip in _audio_chunk_history: del _audio_chunk_history[client_ip]
            if client_ip in _last_alert_times: del _last_alert_times[client_ip]

def udp_listener():
    """Lắng nghe dữ liệu UDP từ các ESP32 và xử lý."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Cho phép tái sử dụng địa chỉ nhanh chóng sau khi đóng
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((config.UDP_HOST, config.UDP_PORT))
        sock.settimeout(1.0) # Chờ tối đa 1 giây để nhận dữ liệu
        logging.info(f"UDP Server: Listening on {config.UDP_HOST}:{config.UDP_PORT}...")

        while not _stop_udp.is_set():
            try:
                # Nhận dữ liệu và địa chỉ client
                data, addr = sock.recvfrom(config.UDP_BUFFER_SIZE)
                if data:
                    # Gọi hàm xử lý trong cùng luồng (đơn giản nhất)
                    _process_audio_data(data, addr)
            except socket.timeout:
                # Không nhận được gì trong 1 giây, tiếp tục vòng lặp để kiểm tra _stop_udp
                continue
            except OSError as e:
                 # Lỗi mạng hoặc socket
                 logging.error(f"UDP Server: Socket OSError receiving data: {e}", exc_info=True)
                 time.sleep(1) # Chờ 1 giây trước khi thử lại
            except Exception as e:
                # Các lỗi không mong muốn khác trong lúc nhận/xử lý
                logging.error(f"UDP Server: Unknown error receiving/processing data: {e}", exc_info=True)

    except OSError as e:
        # Lỗi khi bind port (ví dụ: port đã được sử dụng)
        logging.error(f"UDP Server: Error binding UDP port {config.UDP_PORT}: {e}. Port might be in use or require privileges.")
    except Exception as e:
        # Các lỗi không mong muốn khác trong vòng lặp chính
        logging.error(f"UDP Server: Unknown error in main listener loop: {e}", exc_info=True)
    finally:
        # Đảm bảo socket được đóng khi luồng kết thúc
        if sock:
            sock.close()
        logging.info("UDP Server: Listener thread stopped and socket closed.")

def start_udp_thread() -> threading.Thread:
    """Khởi tạo và bắt đầu luồng chạy UDP listener."""
    if not ml_handler._is_model_loaded:
         logging.warning("UDP Server: ML Model not loaded. UDP listener starting but predictions will fail.")

    _stop_udp.clear() # Đảm bảo cờ stop được reset
    # Xóa các buffer và lịch sử cũ trước khi bắt đầu luồng mới
    with _buffer_lock:
        _audio_buffers.clear()
        _prediction_history.clear()
        _audio_chunk_history.clear()
        _last_alert_times.clear()

    # Tạo và bắt đầu luồng listener
    udp_thread = threading.Thread(target=udp_listener, name="UDPListenerThread", daemon=True) # daemon=True để luồng tự thoát khi chương trình chính thoát
    udp_thread.start()
    logging.info("UDP Server: Listener thread initialized and started.")
    return udp_thread

def stop_udp_listener():
    """Dừng UDP listener một cách an toàn."""
    logging.info("UDP Server: Requesting listener thread stop...")
    _stop_udp.set() # Đặt cờ yêu cầu dừng
    # Không cần join() ở đây vì luồng là daemon và sẽ tự thoát,
    # hoặc join() trong hàm shutdown của run.py nếu cần đợi
