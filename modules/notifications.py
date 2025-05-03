import logging
import requests
from datetime import datetime
from typing import Optional
import os

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self):
        self.log_file = os.getenv('NOTIFICATION_LOG_FILE', 'supervisor_alerts.log')
        
    def _format_message(self, request_id: int, question: str, customer_id: str) -> str:
        return (
            f"[SUPERVISOR ALERT] New help request (#{request_id}):\n"
            f"Question: \"{question}\"\n"
            f"Customer ID: {customer_id}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"--------------------------"
        )

    def notify_supervisor(self, request_id: int, question: str, customer_id: str) -> bool:
        """Main method to handle all notification channels"""
        message = self._format_message(request_id, question, customer_id)
        
        try:
            # 1. Console output (primary channel)
            print(f"\n\033[93m{message}\033[0m")  # Yellow color for visibility
            
            # 2. Log file (audit trail)
            with open(self.log_file, 'a') as f:
                f.write(message + '\n')
                
            # 3. Optional webhook
            webhook_url = os.getenv('SUPERVISOR_WEBHOOK_URL')
            if webhook_url:
                requests.post(
                    webhook_url,
                    json={
                        'request_id': request_id,
                        'message': message,
                        'timestamp': datetime.now().isoformat()
                    },
                    timeout=3
                )
                
            return True
        except Exception as e:
            logger.error(f"Notification failed: {str(e)}")
            return False

# Singleton instance
notification_service = NotificationService()