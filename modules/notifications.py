import logging
from typing import Optional

logger = logging.getLogger(__name__)

def notify_supervisor(request_id: int, customer_id: str, question: str) -> bool:
    """
    Simulate notifying a supervisor about a help request.
    
    In a real implementation, this would send an SMS, email, or push notification.
    
    Args:
        request_id: ID of the help request
        customer_id: ID of the customer
        question: The question that needs assistance
        
    Returns:
        True if notification was sent successfully
    """
    # Simulate notification
    message = f"Help Request #{request_id} - Customer {customer_id} asked: {question}"
    logger.info(f"SUPERVISOR NOTIFICATION: {message}")
    print(f"[Supervisor Notification] {message}")
    
    return True


def notify_customer(customer_id: str, message: str) -> bool:
    """
    Simulate sending a notification to a customer.
    
    Args:
        customer_id: ID of the customer
        message: Message to send
        
    Returns:
        True if notification was sent successfully
    """
    # Simulate SMS or other notification
    logger.info(f"CUSTOMER NOTIFICATION to {customer_id}: {message}")
    print(f"[Customer Notification] To {customer_id}: {message}")
    
    return True