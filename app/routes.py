# app/routes.py
from flask import request, jsonify, current_app, abort
import logging
import datetime # <<< THÊM DÒNG NÀY
from google.cloud import firestore

from . import token_storage
from . import firebase_client
# Import S3 client và config từ udp_server (cân nhắc refactor nếu cần)
from .udp_server import _s3_client, config as udp_config

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

    # --- Route lấy lịch sử cảnh báo ---
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
                    return jsonify({"status": "error", "message": "Invalid last document ID"}), 404

            query = query.limit(limit)
            results = query.stream()

            history_list = []
            last_id_in_page = None
            for doc in results:
                doc_data = doc.to_dict()
                # Chuyển đổi Timestamp của Firestore thành chuỗi ISO 8601
                # Giờ đây 'datetime' đã được import nên không lỗi NameError
                if 'timestamp' in doc_data and isinstance(doc_data['timestamp'], datetime.datetime):
                    doc_data['timestamp'] = doc_data['timestamp'].isoformat()
                doc_data['id'] = doc.id
                history_list.append(doc_data)
                last_id_in_page = doc.id

            response_data = {
                "status": "success",
                "history": history_list,
                "last_doc_id": last_id_in_page
            }
            return jsonify(response_data), 200

        except Exception as e:
            logging.error(f"Lỗi khi xử lý route /alert_history: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Internal server error fetching history"}), 500

    # --- Route lấy URL S3 tạm thời ---
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
             return jsonify({"status": "error", "message": "S3 storage not configured on server"}), 501

        try:
            presigned_url = _s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': udp_config.AWS_S3_BUCKET_NAME, 'Key': s3_key},
                ExpiresIn=udp_config.AWS_S3_URL_EXPIRATION_S
            )

            if presigned_url:
                return jsonify({"status": "success", "url": presigned_url}), 200
            else:
                logging.error(f"Failed to generate presigned URL for key: {s3_key}")
                return jsonify({"status": "error", "message": "Failed to generate audio URL"}), 500

        except _s3_client.exceptions.ClientError as e: # Sửa lại cách bắt ClientError
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == 'NoSuchKey':
                logging.warning(f"S3 key not found: {s3_key}")
                return jsonify({"status": "error", "message": "Audio file not found"}), 404
            elif error_code == 'NoSuchBucket':
                 logging.error(f"S3 bucket '{udp_config.AWS_S3_BUCKET_NAME}' not found.")
                 return jsonify({"status": "error", "message": "Server storage configuration error"}), 500
            else:
                logging.error(f"S3 ClientError generating URL for key {s3_key}: {e}", exc_info=True)
                return jsonify({"status": "error", "message": "Error accessing storage"}), 500
        except Exception as e:
            logging.error(f"Lỗi không xác định khi tạo URL S3 cho key {s3_key}: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Internal server error generating URL"}), 500

    # Thêm các route khác ở đây nếu cần
