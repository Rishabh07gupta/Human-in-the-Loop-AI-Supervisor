#!/usr/bin/env python3
import os
import sys
import traceback
import sqlite3
import stat
from app import create_app, db
from modules.knowledge_base import init_sample_salon_data

def setup_database():
    print("Starting database setup...")
    
    # Create and configure the app
    app = create_app()
    
    # Display database URI for debugging
    print(f"Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
    
    # Create instance directory if it doesn't exist
    instance_path = app.instance_path
    print(f"Checking instance path: {instance_path}")
    
    if not os.path.exists(instance_path):
        try:
            os.makedirs(instance_path, mode=0o777)  # Full permissions
            print(f"Created instance folder at {instance_path}")
        except Exception as e:
            print(f"Error creating instance directory: {e}")
            traceback.print_exc()
            return False
    else:
        print(f"Instance directory already exists")
        
        # Ensure the instance directory has proper permissions
        try:
            os.chmod(instance_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 0777 permissions
            print("Updated instance directory permissions")
        except Exception as e:
            print(f"Warning: Could not update instance directory permissions: {e}")
    
    # Check write permissions
    try:
        test_file_path = os.path.join(instance_path, "test_write.txt")
        with open(test_file_path, 'w') as f:
            f.write("test")
        os.remove(test_file_path)
        print(f"Write permissions to instance directory confirmed")
    except Exception as e:
        print(f"Warning: Cannot write to instance directory: {e}")
        traceback.print_exc()
        return False
    
    # Define database path
    db_path = os.path.join(instance_path, "supervisor.db")
    print(f"Database path: {db_path}")
    
    # If the database file exists but might be causing issues, remove it
    if os.path.exists(db_path):
        try:
            print(f"Found existing database. Attempting to open it...")
            conn = sqlite3.connect(db_path)
            conn.close()
            print("Existing database seems valid.")
        except sqlite3.Error:
            print("Existing database appears to be corrupted or locked.")
            try:
                print("Removing problematic database file...")
                os.remove(db_path)
                print("Removed problematic database file.")
            except Exception as e:
                print(f"Warning: Could not remove existing database: {e}")
                return False
    
    # Test SQLite connection directly
    try:
        print("Testing direct SQLite connection...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT sqlite_version();")
        version = cursor.fetchone()
        print(f"SQLite version: {version[0]}")
        conn.close()
        
        # Ensure file has proper permissions
        os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)  # 0666 permissions
        print("Direct SQLite connection successful")
    except Exception as e:
        print(f"Warning: Direct SQLite connection failed: {e}")
        traceback.print_exc()
        return False
    
    # Initialize the database with SQLAlchemy
    try:
        with app.app_context():
            print("Creating database tables...")
            db.create_all()
            print("Initializing sample salon data...")
            init_sample_salon_data()
            print("Database initialized successfully!")
            return True
    except Exception as e:
        print(f"Error initializing database: {e}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    if setup_database():
        print("Setup complete!")
    else:
        print("Setup failed! See errors above.")
        sys.exit(1)