# app/udp_server.py
import socket
import logging
import threading
import time
import io # Để xử lý byte stream trong bộ nhớ
from collections import defaultdict, deque
import numpy as np
import torch
import soundfile as sf # Để lưu tensor thành file WAV
import boto3 # Để tương tác với AWS S3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError

from . import config
from . import ml_handler # Import module xử lý ML
from . import firebase_client # Import module Firebase để gửi thông báo
# token_storage không cần import trực tiếp ở đây nữa

_stop_udp = threading.Event()

# --- Cấu trúc dữ liệu mới để lưu trữ lịch sử ---
# {ip: deque([(timestamp, prediction_label), ...])} - Lưu lịch sử dự đoán trong khoảng thời gian window
_prediction_history = defaultdict(lambda: deque(maxlen=int(config.SCREAM_FREQUENCY_WINDOW_S / config.AUDIO_CHUNK_DURATION_S) * 2)) # Lưu nhiều hơn một chút phòng trường hợp chunk đến không đều

# {ip: deque([(timestamp, audio_chunk_tensor), ...])} - Lưu lịch sử chunk âm thanh để ghép nối sau này
_audio_chunk_history = defaultdict(lambda: deque(maxlen=int(config.AUDIO_SAVE_DURATION_S / config.AUDIO_CHUNK_DURATION_S) + 5)) # Lưu ~10-15 giây

# {ip: float} - Lưu thời gian cảnh báo cuối cùng (kể cả cảnh báo phức tạp)
_last_alert_times = defaultdict(float)

# {ip: torch.Tensor} - Buffer cho dữ liệu UDP chưa xử lý (như cũ)
_audio_buffers = defaultdict(lambda: torch.tensor([], dtype=torch.float32))

_buffer_lock = threading.Lock() # Lock để bảo vệ tất cả các cấu trúc dữ liệu chia sẻ trên

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
        # Chuyển về numpy và định dạng int16 (phổ biến cho WAV)
        # Đảm bảo tensor là 1D
        if audio_tensor.ndim > 1:
            audio_tensor = audio_tensor.squeeze() # Bỏ các chiều không cần thiết
        # Chuyển về CPU nếu đang ở GPU
        audio_np = audio_tensor.cpu().numpy()
        # Scale sang int16
        audio_int16 = (audio_np * 32767).astype(np.int16)

        buffer = io.BytesIO()
        sf.write(buffer, audio_int16, sample_rate, format='WAV', subtype='PCM_16')
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        logging.error(f"Error converting tensor to WAV bytes: {e}", exc_info=True)
        return None

def upload_audio_to_s3(audio_bytes: bytes, client_ip: str, timestamp: float) -> str | None:
    """Tải dữ liệu audio bytes lên S3 và trả về pre-signed URL."""
    if not _s3_client or not config.S3_CONFIGURED:
        logging.error("S3 client not available or not configured. Cannot upload audio.")
        return None
    if not audio_bytes:
        logging.error("No audio bytes provided to upload.")
        return None

    try:
        # Tạo tên file duy nhất
        filename = f"{config.AWS_S3_AUDIO_FOLDER}scream_{client_ip.replace('.', '-')}_{int(timestamp)}.wav"
        bucket_name = config.AWS_S3_BUCKET_NAME

        # Tải lên S3
        _s3_client.put_object(Bucket=bucket_name, Key=filename, Body=audio_bytes, ContentType='audio/wav')
        logging.info(f"Successfully uploaded audio to s3://{bucket_name}/{filename}")

        # Tạo pre-signed URL để truy cập tạm thời
        url = _s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': filename},
            ExpiresIn=config.AWS_S3_URL_EXPIRATION_S
        )
        return url
    except ClientError as e:
        logging.error(f"S3 ClientError during upload/presign URL generation: {e}", exc_info=True)
        # Check for common errors like NoSuchBucket
        if e.response['Error']['Code'] == 'NoSuchBucket':
            logging.error(f"Bucket '{config.AWS_S3_BUCKET_NAME}' does not exist.")
        elif e.response['Error']['Code'] == 'InvalidAccessKeyId' or e.response['Error']['Code'] == 'SignatureDoesNotMatch':
             logging.error("Invalid AWS credentials.")
        return None
    except Exception as e:
        logging.error(f"Unexpected error during S3 upload: {e}", exc_info=True)
        return None

# --- Hàm xử lý chính ---
def _process_audio_data(data_bytes, client_address):
    """
    Xử lý dữ liệu audio nhận được từ một client (ESP32).
    Áp dụng logic phát hiện phức tạp và gửi cảnh báo kèm âm thanh qua S3.
    """
    global _audio_buffers, _prediction_history, _audio_chunk_history, _last_alert_times, _buffer_lock

    client_ip = client_address[0]
    num_bytes_received = len(data_bytes)

    if num_bytes_received == 0: return
    if num_bytes_received % config.AUDIO_BYTES_PER_SAMPLE != 0:
        logging.warning(f"UDP Server: From {client_ip}, received {num_bytes_received} bytes, "
                        f"not a multiple of {config.AUDIO_BYTES_PER_SAMPLE} bytes/sample. Skipping packet.")
        return

    num_samples = num_bytes_received // config.AUDIO_BYTES_PER_SAMPLE

    try:
        samples_np = np.frombuffer(data_bytes, dtype=config.AUDIO_NUMPY_DTYPE)
        audio_tensor_float = torch.from_numpy(samples_np.astype(np.float32) / (2**31)).float()

        with _buffer_lock:
            _audio_buffers[client_ip] = torch.cat((_audio_buffers[client_ip], audio_tensor_float))
            current_buffer = _audio_buffers[client_ip]

            chunks_processed_in_packet = 0 # Đếm số chunk xử lý từ gói tin này
            while len(current_buffer) >= config.AUDIO_CHUNK_SAMPLES:
                chunks_processed_in_packet += 1
                process_chunk = current_buffer[:config.AUDIO_CHUNK_SAMPLES]
                current_buffer = current_buffer[config.AUDIO_CHUNK_SAMPLES:]
                _audio_buffers[client_ip] = current_buffer # Update buffer còn lại

                # --- Thực hiện dự đoán (ngoài lock để không giữ lock quá lâu) ---
                _buffer_lock.release()
                try:
                    # logging.debug(f"UDP Server: Processing chunk for {client_ip} ({len(process_chunk)} samples)")
                    prediction, confidence = ml_handler.predict_scream(process_chunk)
                finally:
                    _buffer_lock.acquire() # Lấy lại lock để cập nhật lịch sử và kiểm tra điều kiện
                # --- Kết thúc dự đoán ---

                current_time = time.time()

                # --- Cập nhật lịch sử và kiểm tra điều kiện (trong lock) ---
                # 1. Lưu chunk âm thanh (lưu trên CPU để tiết kiệm bộ nhớ GPU nếu cần)
                _audio_chunk_history[client_ip].append((current_time, process_chunk.cpu()))

                # 2. Lưu kết quả dự đoán
                _prediction_history[client_ip].append((current_time, prediction))

                # 3. Dọn dẹp lịch sử dự đoán cũ (deque tự dọn dẹp dựa trên maxlen, nhưng kiểm tra lại cho chắc)
                window_start_time = current_time - config.SCREAM_FREQUENCY_WINDOW_S
                while _prediction_history[client_ip] and _prediction_history[client_ip][0][0] < window_start_time:
                     _prediction_history[client_ip].popleft()
                # Audio deque tự dọn dẹp

                # 4. Phân tích lịch sử trong window để kiểm tra điều kiện
                recent_predictions_in_window = list(_prediction_history[client_ip]) # Lấy bản sao để phân tích

                # Điều kiện 1: Ít nhất MIN_CONSECUTIVE_CHUNKS hét liên tiếp
                max_consecutive_in_window = 0
                current_consecutive = 0
                for _, pred_label in recent_predictions_in_window:
                    if pred_label == 'Hét':
                        current_consecutive += 1
                    else:
                        max_consecutive_in_window = max(max_consecutive_in_window, current_consecutive)
                        current_consecutive = 0
                max_consecutive_in_window = max(max_consecutive_in_window, current_consecutive) # Kiểm tra chuỗi hét cuối cùng
                condition1_met = max_consecutive_in_window >= config.SCREAM_MIN_CONSECUTIVE_CHUNKS # e.g., >= 2
                condition1_status = "Đạt" if condition1_met else "Chưa đạt"

                # Điều kiện 2: Ít nhất SCREAM_FREQUENCY_COUNT lần hét ngắt quãng
                total_screams_in_window = sum(1 for _, pred_label in recent_predictions_in_window if pred_label == 'Hét')
                condition2_met = total_screams_in_window >= config.SCREAM_FREQUENCY_COUNT # e.g., >= 3
                condition2_status = "Đạt" if condition2_met else "Chưa đạt"

                # --- Cập nhật LOGGING ---
                # Log chi tiết trạng thái sau mỗi chunk được xử lý
                log_message = (
                    f"UDP Server: Chunk from {client_ip} - Prediction: {prediction} ({confidence*100:.1f}%). "
                    f"Status in {config.SCREAM_FREQUENCY_WINDOW_S}s window: "
                    f"Consecutive: {max_consecutive_in_window}/{config.SCREAM_MIN_CONSECUTIVE_CHUNKS} ({condition1_status}), "
                    f"Total: {total_screams_in_window}/{config.SCREAM_FREQUENCY_COUNT} ({condition2_status})."
                )
                # Chỉ log mức INFO nếu có tiếng hét hoặc lỗi, DEBUG cho các trường hợp khác
                if prediction == 'Hét' or prediction is None:
                    logging.info(log_message)
                else:
                    logging.debug(log_message) # Log 'Không hét' ở mức DEBUG để tránh quá nhiều log
                # --- Kết thúc cập nhật LOGGING ---


                # 5. Kiểm tra và gửi cảnh báo phức tạp
                if condition1_met and condition2_met:
                    last_alert_time = _last_alert_times[client_ip]
                    if current_time - last_alert_time > config.SCREAM_ALERT_COOLDOWN_S:
                        logging.warning(f"--- !!! Complex Scream Pattern Detected from {client_ip} !!! ---")
                        # Log chi tiết đã được ghi ở trên, có thể thêm log này nếu muốn nhấn mạnh
                        # logging.info(f"    Details: Max Consecutive = {max_consecutive_in_window}, Total in Window = {total_screams_in_window}")

                        # --- Chuẩn bị và tải lên âm thanh (ngoài lock) ---
                        _buffer_lock.release()
                        audio_url = None
                        try:
                            # Lấy các chunk âm thanh liên quan từ deque
                            audio_to_save_list = []
                            save_window_start_time = current_time - config.AUDIO_SAVE_DURATION_S
                            # Lặp qua bản sao của deque để tránh lỗi thay đổi kích thước khi lặp
                            for ts, chunk_tensor in list(_audio_chunk_history[client_ip]):
                                if ts >= save_window_start_time:
                                    audio_to_save_list.append(chunk_tensor)

                            if audio_to_save_list:
                                full_audio_tensor = torch.cat(audio_to_save_list)
                                audio_bytes = save_tensor_to_wav_bytes(full_audio_tensor, config.AUDIO_SAMPLE_RATE)
                                if audio_bytes:
                                     # Tải lên S3
                                     audio_url = upload_audio_to_s3(audio_bytes, client_ip, current_time)
                                     if audio_url:
                                         logging.info(f"Uploaded {len(audio_to_save_list) * config.AUDIO_CHUNK_DURATION_S:.1f}s audio segment to S3: {audio_url}")
                                     else:
                                         logging.error("Failed to upload audio segment to S3.")
                                else:
                                     logging.error("Failed to convert audio tensor to WAV bytes.")
                            else:
                                logging.warning("No audio data found in the save window to upload.")
                        except Exception as upload_err:
                            logging.error(f"Error preparing/uploading audio: {upload_err}", exc_info=True)
                        finally:
                             _buffer_lock.acquire() # Lấy lại lock
                        # --- Kết thúc chuẩn bị âm thanh ---

                        # Gửi cảnh báo FCM (trong lock)
                        alert_title = config.HIGH_FREQUENCY_ALERT_TITLE # Tiêu đề cho cảnh báo phức tạp
                        alert_body = config.HIGH_FREQUENCY_ALERT_BODY_TEMPLATE.format(total_screams_in_window, config.SCREAM_FREQUENCY_WINDOW_S, client_ip)
                        payload = {"type": "complex_scream", "ip": client_ip}
                        if audio_url:
                            payload["audio_url"] = audio_url # Thêm URL âm thanh vào payload

                        success = firebase_client.send_alert_to_all(alert_title, alert_body, data=payload)

                        if success:
                            logging.info(f"Sent complex scream alert for {client_ip} to devices.")
                            _last_alert_times[client_ip] = current_time # Cập nhật thời gian cảnh báo
                        else:
                            logging.error(f"Failed to send complex scream alert for {client_ip}.")
                    else:
                        # Log rõ hơn lý do không gửi cảnh báo
                        logging.info(f"Complex scream pattern conditions met for {client_ip}, but within cooldown period. Alert not sent.")
                # --- Kết thúc kiểm tra điều kiện phức tạp ---

            # Nếu không có chunk nào được xử lý từ gói tin này (ví dụ: gói tin quá nhỏ)
            # thì không cần làm gì thêm trong lock.
            # Lock sẽ được giải phóng khi thoát khỏi khối 'with'.

    except Exception as e:
        logging.error(f"UDP Server: Critical error processing data from {client_ip}: {e}", exc_info=True)
        # Cân nhắc xóa lịch sử của client này nếu lỗi liên tục
        with _buffer_lock:
            if client_ip in _prediction_history: del _prediction_history[client_ip]
            if client_ip in _audio_chunk_history: del _audio_chunk_history[client_ip]
            if client_ip in _audio_buffers: del _audio_buffers[client_ip]
            if client_ip in _last_alert_times: del _last_alert_times[client_ip]


def udp_listener():
    """Lắng nghe dữ liệu UDP từ các ESP32 và xử lý."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((config.UDP_HOST, config.UDP_PORT))
        sock.settimeout(1.0)
        logging.info(f"UDP Server: Listening on {config.UDP_HOST}:{config.UDP_PORT}...")

        while not _stop_udp.is_set():
            try:
                data, addr = sock.recvfrom(config.UDP_BUFFER_SIZE)
                if data:
                    _process_audio_data(data, addr)
            except socket.timeout:
                continue
            except OSError as e:
                 logging.error(f"UDP Server: Socket OSError receiving data: {e}", exc_info=True)
                 time.sleep(1)
            except Exception as e:
                logging.error(f"UDP Server: Unknown error receiving/processing data: {e}", exc_info=True)

    except OSError as e:
        logging.error(f"UDP Server: Error binding UDP port {config.UDP_PORT}: {e}. Port might be in use or require privileges.")
    except Exception as e:
        logging.error(f"UDP Server: Unknown error in main listener loop: {e}", exc_info=True)
    finally:
        if sock:
            sock.close()
        logging.info("UDP Server: Listener thread stopped and socket closed.")

def start_udp_thread() -> threading.Thread:
    """Khởi tạo và bắt đầu luồng chạy UDP listener."""
    if not ml_handler._is_model_loaded:
         logging.warning("UDP Server: ML Model not loaded. UDP listener starting but predictions will fail.")

    _stop_udp.clear()
    # Reset trạng thái khi khởi động lại
    with _buffer_lock:
        _audio_buffers.clear()
        _prediction_history.clear()
        _audio_chunk_history.clear()
        _last_alert_times.clear()

    udp_thread = threading.Thread(target=udp_listener, name="UDPListenerThread", daemon=True)
    udp_thread.start()
    logging.info("UDP Server: Listener thread initialized and started.")
    return udp_thread

def stop_udp_listener():
    """Dừng UDP listener một cách an toàn."""
    logging.info("UDP Server: Requesting listener thread stop...")
    _stop_udp.set()
