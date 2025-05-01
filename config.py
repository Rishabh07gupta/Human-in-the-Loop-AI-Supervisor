import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Base directory and instance path
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    INSTANCE_PATH = os.path.join(BASE_DIR, 'instance')
    
    # Flask Configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-for-development')
    DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    # Database Configuration - Using absolute path
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL', 
        f"sqlite:///{os.path.join(INSTANCE_PATH, 'supervisor.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # LiveKit Configuration
    LIVEKIT_URL = os.environ.get('LIVEKIT_URL')
    LIVEKIT_API_KEY = os.environ.get('LIVEKIT_API_KEY')
    LIVEKIT_API_SECRET = os.environ.get('LIVEKIT_API_SECRET')
    
    # AI Provider API Keys
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    DEEPGRAM_API_KEY = os.environ.get('DEEPGRAM_API_KEY')
    
    # Request Timeout Configuration
    REQUEST_TIMEOUT_MINUTES = int(os.environ.get('REQUEST_TIMEOUT_MINUTES', 30))