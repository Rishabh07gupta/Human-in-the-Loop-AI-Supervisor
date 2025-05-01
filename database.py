from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

# Create database instance
db = SQLAlchemy()

class HelpRequest(db.Model):
    """Model for tracking customer help requests."""
    __tablename__ = 'help_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.String(50), nullable=False)
    question = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending, resolved, unresolved
    answer = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)
    
    def __repr__(self):
        return f"<HelpRequest {self.id}: {self.status}>"


class KnowledgeItem(db.Model):
    """Model for knowledge base items."""
    __tablename__ = 'knowledge_base'
    
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.Text, nullable=False, unique=True)
    answer = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<KnowledgeItem {self.id}: {self.question[:30]}...>"


class SalonInfo(db.Model):
    """Model for salon information."""
    __tablename__ = 'salon_info'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), nullable=False, unique=True)
    value = db.Column(db.Text, nullable=False)
    
    def __repr__(self):
        return f"<SalonInfo {self.key}: {self.value[:30]}...>"