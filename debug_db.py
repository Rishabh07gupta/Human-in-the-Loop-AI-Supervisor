#!/usr/bin/env python3
import os
import sys
import stat
import sqlite3

def debug_database_setup():
    """Detailed debugging of database setup issues"""
    # Determine the project root directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    instance_dir = os.path.join(project_dir, 'instance')
    db_path = os.path.join(instance_dir, 'supervisor.db')
    
    print("\n=== DEBUG DATABASE SETUP ===")
    print(f"Project directory: {project_dir}")
    print(f"Instance directory: {instance_dir}")
    print(f"Database path: {db_path}")
    
    # Check instance directory
    if os.path.exists(instance_dir):
        print("\n[✓] Instance directory exists")
        
        # Get directory permissions
        mode = os.stat(instance_dir).st_mode
        permissions = stat.filemode(mode)
        print(f"Instance directory permissions: {permissions}")
        
        # Check if directory is readable and writable
        if os.access(instance_dir, os.R_OK | os.W_OK | os.X_OK):
            print("[✓] Instance directory is readable, writable, and executable")
        else:
            print("[✗] WARNING: Instance directory has permission issues")
            if not os.access(instance_dir, os.R_OK):
                print("   - Not readable")
            if not os.access(instance_dir, os.W_OK):
                print("   - Not writable")
            if not os.access(instance_dir, os.X_OK):
                print("   - Not executable")
    else:
        print("\n[✗] ERROR: Instance directory does not exist")
        return False
    
    # Check database file
    if os.path.exists(db_path):
        print(f"\nDatabase file exists at: {db_path}")
        
        # Get file permissions
        mode = os.stat(db_path).st_mode
        permissions = stat.filemode(mode)
        print(f"Database file permissions: {permissions}")
        
        # Check if file is readable and writable
        if os.access(db_path, os.R_OK | os.W_OK):
            print("[✓] Database file is readable and writable")
        else:
            print("[✗] WARNING: Database file has permission issues")
            if not os.access(db_path, os.R_OK):
                print("   - Not readable")
            if not os.access(db_path, os.W_OK):
                print("   - Not writable")
        
        # Check if file is locked
        try:
            print("\nAttempting to open existing database...")
            conn = sqlite3.connect(db_path, timeout=1)
            cursor = conn.cursor()
            cursor.execute("PRAGMA quick_check;")
            result = cursor.fetchone()
            print(f"Database check result: {result[0]}")
            conn.close()
            print("[✓] Successfully opened and checked existing database")
        except sqlite3.Error as e:
            print(f"[✗] ERROR: Failed to open existing database: {e}")
            print("The database file might be locked or corrupted")
    else:
        print(f"\nDatabase file does not exist at: {db_path}")
        print("Attempting to create a test database...")
        
        try:
            # Test direct database creation
            test_conn = sqlite3.connect(db_path)
            test_cursor = test_conn.cursor()
            test_cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT);")
            test_cursor.execute("INSERT INTO test (name) VALUES ('test');")
            test_conn.commit()
            test_cursor.execute("SELECT * FROM test;")
            result = test_cursor.fetchone()
            print(f"Test query result: {result}")
            test_conn.close()
            
            # Remove test database
            os.remove(db_path)
            print("[✓] Successfully created and accessed test database")
        except sqlite3.Error as e:
            print(f"[✗] ERROR: Failed to create test database: {e}")
            print("This indicates SQLite cannot write to this path")
            return False
        except Exception as e:
            print(f"[✗] ERROR: Unexpected error: {e}")
            return False
    
    # Check Flask app configuration
    print("\nChecking Flask app configuration...")
    try:
        # Try to import and check Flask app config
        from app import create_app
        app = create_app()
        print(f"SQLALCHEMY_DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
        
        # Check if database URI is correct
        expected_uri = f"sqlite:///{db_path}"
        normalized_expected = expected_uri.replace('\\', '/')
        normalized_actual = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('\\', '/')
        
        if normalized_actual == normalized_expected:
            print("[✓] Database URI is correctly configured")
        else:
            print(f"[✗] WARNING: Database URI mismatch")
            print(f"  Expected: {expected_uri}")
            print(f"  Actual:   {app.config.get('SQLALCHEMY_DATABASE_URI')}")
            
        # Try initializing database with app context
        print("\nTesting database initialization with app context...")
        with app.app_context():
            from flask_sqlalchemy import SQLAlchemy
            db = SQLAlchemy(app)
            db.engine.connect()
            print("[✓] Successfully connected to the database through SQLAlchemy")
    except ImportError as e:
        print(f"[✗] ERROR: Could not import Flask app: {e}")
    except Exception as e:
        print(f"[✗] ERROR: Flask app configuration test failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n=== END DEBUG ===")
    return True

if __name__ == "__main__":
    debug_database_setup()