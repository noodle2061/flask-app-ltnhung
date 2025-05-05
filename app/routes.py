# app/routes.py
from flask import request, jsonify, current_app, abort
import logging
import datetime # <<< THÊM DÒNG NÀY
from datetime import timedelta # <<< THÊM DÒNG NÀY
from google.cloud import firestore
import pytz # <<< THÊM DÒNG NÀY (Cần cài đặt: pip install pytz)

from . import token_storage
from . import firebase_client
# Import S3 client và config từ udp_server (cân nhắc refactor nếu cần)
from .udp_server import _s3_client, config as udp_config

# Định nghĩa múi giờ cho Việt Nam (hoặc múi giờ server của bạn)
# Điều này quan trọng để tính toán ngày bắt đầu/kết thúc tuần chính xác
LOCAL_TIMEZONE = pytz.timezone('Asia/Ho_Chi_Minh')

def register_routes(app):
    """Đăng ký các route cho Flask app."""

    # --- Route đăng ký token (giữ nguyên) ---
    @app.route('/register_token', methods=['POST'])
    def handle_register_token():
        """Nhận FCM token từ ứng dụng Android."""
        try:
            data = request.get_json()
            if not data or 'token' not in data:
                logging.warning("Route /register_token: Thiếu trường 'token' trong JSON.")
                return jsonify({"status": "error", "message": "Missing 'token' in JSON payload"}), 400
            token = data['token']
            token_storage.add_token(token)
            return jsonify({"status": "success", "message": "Token registered"}), 200
        except Exception as e:
            logging.error(f"Lỗi khi xử lý route /register_token: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Internal server error"}), 500

    # --- Route kiểm tra sức khỏe (giữ nguyên) ---
    @app.route('/health', methods=['GET'])
    def health_check():
        """Endpoint kiểm tra sức khỏe đơn giản."""
        return jsonify({"status": "ok"}), 200

    # --- Route lấy lịch sử cảnh báo (giữ nguyên) ---
    @app.route('/alert_history', methods=['GET'])
    def get_alert_history():
        """
        Lấy danh sách lịch sử cảnh báo từ Firestore.
        Hỗ trợ phân trang cơ bản bằng 'limit' và 'last_doc_id'.
        """
        if not firebase_client._firestore_db:
            logging.error("Firestore client not available in /alert_history route.")
            return jsonify({"status": "error", "message": "Firestore not configured on server"}), 500

        try:
            limit = request.args.get('limit', default=20, type=int)
            last_doc_id = request.args.get('last_doc_id', default=None, type=str)

            collection_ref = firebase_client._firestore_db.collection('alert_history')
            query = collection_ref.order_by('timestamp', direction=firestore.Query.DESCENDING)

            if last_doc_id:
                last_doc_snapshot = collection_ref.document(last_doc_id).get()
                if last_doc_snapshot.exists:
                    query = query.start_after(last_doc_snapshot)
                else:
                    logging.warning(f"/alert_history: last_doc_id '{last_doc_id}' not found.")
                    # Trả về trang đầu tiên thay vì lỗi 404
                    # return jsonify({"status": "error", "message": "Invalid last document ID"}), 404
                    logging.info(f"/alert_history: last_doc_id '{last_doc_id}' not found. Returning first page.")


            query = query.limit(limit)
            results = query.stream()

            history_list = []
            last_id_in_page = None
            for doc in results:
                doc_data = doc.to_dict()
                # Chuyển đổi Timestamp của Firestore thành chuỗi ISO 8601 UTC
                if 'timestamp' in doc_data and isinstance(doc_data['timestamp'], datetime.datetime):
                    # Đảm bảo timestamp là timezone-aware (UTC) trước khi format
                    if doc_data['timestamp'].tzinfo is None:
                        doc_data['timestamp'] = doc_data['timestamp'].replace(tzinfo=pytz.utc)
                    else:
                        doc_data['timestamp'] = doc_data['timestamp'].astimezone(pytz.utc)
                    # Format thành ISO 8601 với 'Z' cho UTC
                    doc_data['timestamp'] = doc_data['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                doc_data['id'] = doc.id
                history_list.append(doc_data)
                last_id_in_page = doc.id # Lưu ID của document cuối cùng trong trang này

            response_data = {
                "status": "success",
                "history": history_list,
                # Trả về ID cuối cùng để client có thể dùng cho lần gọi tiếp theo
                "last_doc_id": last_id_in_page
            }
            return jsonify(response_data), 200

        except Exception as e:
            logging.error(f"Lỗi khi xử lý route /alert_history: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Internal server error fetching history"}), 500

    # --- Route lấy URL S3 tạm thời (giữ nguyên) ---
    @app.route('/get_audio_url', methods=['GET'])
    def get_s3_audio_url():
        """
        Tạo và trả về một pre-signed URL cho một S3 key cụ thể.
        Yêu cầu tham số query 's3_key'.
        """
        s3_key = request.args.get('s3_key', default=None, type=str)

        if not s3_key:
            return jsonify({"status": "error", "message": "Missing 's3_key' query parameter"}), 400

        if not _s3_client or not udp_config.S3_CONFIGURED:
             logging.error("S3 client not available or not configured in /get_audio_url route.")
             return jsonify({"status": "error", "message": "S3 storage not configured on server"}), 501 # 501 Not Implemented

        try:
            # Kiểm tra xem key có tồn tại không trước khi tạo URL (tùy chọn nhưng tốt hơn)
            try:
                 _s3_client.head_object(Bucket=udp_config.AWS_S3_BUCKET_NAME, Key=s3_key)
            except ClientError as e:
                 if e.response['Error']['Code'] == '404':
                      logging.warning(f"S3 key not found when generating URL: {s3_key}")
                      return jsonify({"status": "error", "message": "Audio file not found"}), 404
                 else:
                      # Lỗi khác khi kiểm tra key
                      raise e # Ném lại lỗi để block catch bên ngoài xử lý

            # Nếu key tồn tại, tạo URL
            presigned_url = _s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': udp_config.AWS_S3_BUCKET_NAME, 'Key': s3_key},
                ExpiresIn=udp_config.AWS_S3_URL_EXPIRATION_S
            )

            if presigned_url:
                return jsonify({"status": "success", "url": presigned_url}), 200
            else:
                # Trường hợp này ít xảy ra nếu head_object thành công
                logging.error(f"Failed to generate presigned URL for key: {s3_key} even though key exists.")
                return jsonify({"status": "error", "message": "Failed to generate audio URL"}), 500

        except ClientError as e: # Bắt lỗi ClientError cụ thể từ boto3
            error_code = e.response.get('Error', {}).get('Code')
            # Lỗi 404 đã được xử lý ở trên bằng head_object
            if error_code == 'NoSuchBucket':
                 logging.error(f"S3 bucket '{udp_config.AWS_S3_BUCKET_NAME}' not found.")
                 return jsonify({"status": "error", "message": "Server storage configuration error"}), 500
            else:
                logging.error(f"S3 ClientError generating URL for key {s3_key}: {e}", exc_info=True)
                return jsonify({"status": "error", "message": "Error accessing storage"}), 500
        except Exception as e:
            logging.error(f"Lỗi không xác định khi tạo URL S3 cho key {s3_key}: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Internal server error generating URL"}), 500

    # --- THÊM ROUTE MỚI CHO THỐNG KÊ TUẦN ---
    @app.route('/statistics/weekly', methods=['GET'])
    def get_weekly_statistics():
        """
        Lấy thống kê số lượng cảnh báo theo tuần.
        Chấp nhận tham số 'start_date' (YYYY-MM-DD) để xác định tuần.
        Nếu không có 'start_date', mặc định là tuần hiện tại.
        """
        if not firebase_client._firestore_db:
            logging.error("Firestore client not available in /statistics/weekly route.")
            return jsonify({"status": "error", "message": "Firestore not configured on server"}), 500

        try:
            start_date_str = request.args.get('start_date')
            target_date = None

            if start_date_str:
                try:
                    # Parse ngày từ request (Android gửi YYYY-MM-DD)
                    target_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
                    logging.debug(f"Received start_date parameter: {target_date}")
                except ValueError:
                    logging.warning(f"Invalid start_date format received: {start_date_str}. Using current date.")
                    target_date = datetime.datetime.now(LOCAL_TIMEZONE).date() # Dùng ngày hiện tại nếu format sai
            else:
                # Nếu không có tham số, dùng ngày hiện tại theo múi giờ local
                target_date = datetime.datetime.now(LOCAL_TIMEZONE).date()
                logging.debug(f"No start_date parameter. Using current date: {target_date}")

            # --- Tính toán ngày bắt đầu (Thứ Hai) và kết thúc (Chủ Nhật) của tuần ---
            # weekday() trả về 0 cho Thứ Hai, 6 cho Chủ Nhật
            days_since_monday = target_date.weekday()
            week_start_date = target_date - timedelta(days=days_since_monday)
            week_end_date = week_start_date + timedelta(days=6)

            # Tạo datetime bắt đầu và kết thúc tuần (00:00:00 và 23:59:59.999999) THEO MÚI GIỜ LOCAL
            # Sau đó chuyển sang UTC để truy vấn Firestore (giả sử Firestore lưu UTC)
            start_dt_local = LOCAL_TIMEZONE.localize(datetime.datetime.combine(week_start_date, datetime.time.min))
            end_dt_local = LOCAL_TIMEZONE.localize(datetime.datetime.combine(week_end_date, datetime.time.max))

            # Chuyển sang UTC để query Firestore
            start_dt_utc = start_dt_local.astimezone(pytz.utc)
            end_dt_utc = end_dt_local.astimezone(pytz.utc)

            logging.info(f"Querying Firestore for week: {week_start_date.isoformat()} to {week_end_date.isoformat()} "
                         f"(UTC range: {start_dt_utc.isoformat()} to {end_dt_utc.isoformat()})")

            # --- Truy vấn Firestore ---
            collection_ref = firebase_client._firestore_db.collection('alert_history')
            query = collection_ref.where(
                filter=firestore.FieldFilter('timestamp', '>=', start_dt_utc)
            ).where(
                filter=firestore.FieldFilter('timestamp', '<=', end_dt_utc)
            )
            # Không cần order_by ở đây vì chúng ta sẽ duyệt qua tất cả kết quả trong tuần

            results = query.stream()

            # --- Tổng hợp dữ liệu ---
            total_alerts = 0
            alerts_per_day = {
                "monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0,
                "friday": 0, "saturday": 0, "sunday": 0
            }
            # Mapping từ weekday() (0-6) sang key của dict
            weekday_map = {0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday",
                           4: "friday", 5: "saturday", 6: "sunday"}

            for doc in results:
                total_alerts += 1
                doc_data = doc.to_dict()
                timestamp = doc_data.get('timestamp')

                if isinstance(timestamp, datetime.datetime):
                    # Chuyển timestamp (được trả về từ Firestore, thường là UTC) sang múi giờ LOCAL
                    # để xác định đúng ngày trong tuần theo giờ địa phương
                    timestamp_local = timestamp.astimezone(LOCAL_TIMEZONE)
                    day_index = timestamp_local.weekday() # 0 = Thứ Hai, ..., 6 = Chủ Nhật
                    day_key = weekday_map.get(day_index)
                    if day_key:
                        alerts_per_day[day_key] += 1
                    else:
                         logging.warning(f"Invalid weekday index {day_index} for timestamp {timestamp}")
                else:
                     logging.warning(f"Document {doc.id} has missing or invalid timestamp: {timestamp}")


            logging.info(f"Query completed. Total alerts: {total_alerts}. Daily counts: {alerts_per_day}")

            # --- Định dạng và trả về kết quả ---
            response_data = {
                "status": "success",
                "week_start_date": week_start_date.isoformat(),
                "week_end_date": week_end_date.isoformat(),
                "total_alerts": total_alerts,
                "alerts_per_day": alerts_per_day
            }
            return jsonify(response_data), 200

        except ValueError as e:
             # Lỗi khi parse ngày tháng
             logging.error(f"Lỗi khi xử lý route /statistics/weekly - Lỗi định dạng ngày: {e}", exc_info=True)
             return jsonify({"status": "error", "message": f"Invalid date format: {e}"}), 400
        except Exception as e:
            logging.error(f"Lỗi khi xử lý route /statistics/weekly: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Internal server error fetching statistics"}), 500

    # Thêm các route khác ở đây nếu cần
