import logging
from typing import Dict, List, Optional
from datetime import datetime
from database import db, KnowledgeItem
from flask import has_app_context

# Configure logging
logger = logging.getLogger(__name__)

# In-memory storage for when outside Flask context
memory_knowledge_items = {}
memory_salon_info = {
    "name": "Elegant Beauty Salon",
    "address": "123 Style Street, Fashion City, FC 12345",
    "phone": "555-123-4567",
    "hours": "Monday-Friday: 9:00 AM - 7:00 PM, Saturday: 10:00 AM - 5:00 PM, Sunday: Closed",
    "services_overview": "Haircuts, Coloring, Styling, Manicures, Pedicures, Facials",
    "website": "www.elegantbeauty.com",
    "booking": "Call 555-123-4567 or book online at www.elegantbeauty.com/book",
    "services_detailed": {
        "men_haircut": {"name": "Men's Haircut", "price": "$30"},
        "women_haircut": {"name": "Women's Haircut", "price": "$50"},
        # Other services can be added 
    }
}

class MockKnowledgeItem:
    """Mock KnowledgeItem for use outside of Flask context"""
    def __init__(self, id, question, answer):
        self.id = id
        self.question = question
        self.answer = answer
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

def get_salon_info_standalone() -> str:
    """
    Get formatted salon information without requiring Flask app context.
    Uses pre-defined values.
    
    Returns:
        A formatted string containing salon information
    """
    # Format as a string from the memory dict
    formatted_info = ""
    
    # Add basic info
    for key, value in memory_salon_info.items():
        if key != "services_detailed": 
            formatted_info += f"{key}: {value}\n"
    
    # Add detailed services if available
    if "services_detailed" in memory_salon_info:
        formatted_info += "\nDetailed Services:\n"
        for service_id, service_info in memory_salon_info["services_detailed"].items():
            formatted_info += f"- {service_info['name']}: {service_info['price']}\n"
    
    return formatted_info

def get_salon_info() -> str:
    """
    Get formatted salon information for agent instructions.
    Tries database first, falls back to in-memory data.
    
    Returns:
        A formatted string containing all salon information
    """
    try:
        # Try to use Flask-SQLAlchemy
        from database import SalonInfo
        from flask import has_app_context
        
        if not has_app_context():
            return get_salon_info_standalone()
        
        info_items = SalonInfo.query.all()
        
        # Format as a string
        formatted_info = ""
        for item in info_items:
            formatted_info += f"{item.key}: {item.value}\n"
        
        return formatted_info
    
    except Exception as e:
        # If Flask context is not available, use in-memory storage
        logger.warning(f"Could not query salon info from database: {e}. Using in-memory data.")
        return get_salon_info_standalone()

def add_to_knowledge_base(question: str, answer: str):
    """
    Add a new item to the knowledge base.
    Works with or without Flask context.
    
    Args:
        question: The question
        answer: The answer
        
    Returns:
        The created KnowledgeItem object or MockKnowledgeItem if outside Flask context
    """
    try:
        # Try to use Flask-SQLAlchemy
        
        
        if not has_app_context():
            raise ValueError("No Flask app context")
        
        # Check if question already exists
        existing = KnowledgeItem.query.filter_by(question=question).first()
        
        if existing:
            # Update existing knowledge
            existing.answer = answer
            existing.updated_at = datetime.utcnow()
            db.session.commit()
            # Update memory version too
            if existing.id in memory_knowledge_items:
                memory_knowledge_items[existing.id].answer = answer
                memory_knowledge_items[existing.id].updated_at = datetime.utcnow()
            return existing
        
        # Create new knowledge item
        knowledge_item = KnowledgeItem(
            question=question,
            answer=answer
        )
        
        db.session.add(knowledge_item)
        db.session.commit()
        
        # Add to memory storage for consistency
        memory_knowledge_items[knowledge_item.id] = knowledge_item
        
        return knowledge_item
    
    except Exception as e:
        # If Flask context is not available, use in-memory storage
        logger.warning(f"Could not add to knowledge base in database: {e}. Using in-memory storage.")
        
        # Check if question already exists in memory
        for item in memory_knowledge_items.values():
            if item.question == question:
                item.answer = answer
                item.updated_at = datetime.utcnow()
                return item
        
        # Create new in-memory item
        new_id = len(memory_knowledge_items) + 1
        item = MockKnowledgeItem(new_id, question, answer)
        memory_knowledge_items[new_id] = item
        
        return item

def get_all_knowledge():
    """Get all knowledge items, works with or without Flask context"""
    try:
        # Try to use Flask-SQLAlchemy
        from database import KnowledgeItem
        from flask import has_app_context
        
        if not has_app_context():
            raise ValueError("No Flask app context")
            
        return KnowledgeItem.query.all()
    except Exception as e:
        # If Flask context is not available, use in-memory storage
        logger.warning(f"Could not query knowledge base: {e}. Using in-memory storage.")
        return list(memory_knowledge_items.values())

def add_salon_info(key: str, value: str):
    """
    Add or update salon information.
    Works with or without Flask context.
    
    Args:
        key: Information key (e.g., "name", "hours", "address")
        value: Information value
    """
    try:
        # Try to use Flask-SQLAlchemy
        from database import db, SalonInfo
        from flask import has_app_context
        
        if not has_app_context():
            raise ValueError("No Flask app context")
            
        existing = SalonInfo.query.filter_by(key=key).first()
        
        if existing:
            existing.value = value
            db.session.commit()
            # Update memory storage too
            memory_salon_info[key] = value
            return existing
        
        new_info = SalonInfo(key=key, value=value)
        db.session.add(new_info)
        db.session.commit()
        
        # Update memory storage too
        memory_salon_info[key] = value
        
        return new_info
    
    except Exception as e:
        # If Flask context is not available, use in-memory storage
        logger.warning(f"Could not add salon info to database: {e}. Using in-memory storage.")
        memory_salon_info[key] = value
        
        # Create a simple object to return that matches the interface
        class InfoObj:
            def __init__(self, k, v):
                self.key = k
                self.value = v
        
        return InfoObj(key, value)

def init_sample_salon_data():
    """Initialize sample salon data for testing."""
    sample_data = {
        # Basic Info
        "name": "Elegant Beauty Salon & Spa",
        "address": "123 Style Street, Fashion City, FC 12345",
        "phone": "555-123-4567",
        "emergency_contact": "555-987-6543",
        "hours": "Monday-Friday: 9:00 AM - 7:00 PM\nSaturday: 10:00 AM - 5:00 PM\nSunday: Closed",
        "holiday_hours": "Closed on Christmas Day and New Year's Day",
        
        # Services with detailed pricing
        "services": """Hair Services:
        - Women's Haircut: $60-$90 (based on length)
        - Men's Haircut: $35-$50
        - Kid's Haircut (12 & under): $25-$40
        - Blowout/Styling: $40-$75
        - Full Highlights: $120-$180
        - Partial Highlights: $90-$140
        - Balayage: $150-$220
        - Perm: $80-$150
        - Deep Conditioning: $25-$45

        Nail Services:
        - Basic Manicure: $25
        - Deluxe Manicure: $40
        - Gel Manicure: $45
        - Basic Pedicure: $40
        - Spa Pedicure: $60
        - Gel Pedicure: $65

        Skincare:
        - Basic Facial: $80
        - Anti-Aging Facial: $120
        - Acne Treatment: $100
        - Microdermabrasion: $90""",
                
                # Team Information
                "stylists": """Our Specialists:
        - Mia (Master Colorist): Specializes in balayage and corrective color
        - James (Barber): Expert in fades and beard grooming
        - Sophia (Extensions Specialist): Certified in tape-in and keratin extensions
        - David (Texture Expert): Specializes in curly hair and perms""",
                
                # Policies
                "cancellation_policy": """We require 24 hours notice for cancellations.
        Late cancellations incur a 50% fee.
        No-shows will be charged 100% of the service price.""",
                
                "child_policy": """Children under 12 must be accompanied by an adult.
        We have a dedicated kids' area with toys and entertainment.""",
                
                "accessibility": """Our salon is fully wheelchair accessible.
        We offer sensory-friendly appointments upon request.""",
                
                # Products
                "retail_products": """We carry:
        - Olaplex Hair Repair System
        - Redken Color Protect Shampoo
        - OPI GelColor Polish
        - Dermalogica Skincare Line
        - Brazilian Blowout Products"""
            }
    
    for key, value in sample_data.items():
        add_salon_info(key, value)
    
    # Add detailed service information
    add_salon_info("services_details_men_haircut", "Men's Haircut: $30")
    add_salon_info("services_details_Women_haircut", "Women's Haircut: $50")