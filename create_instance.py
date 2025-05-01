#!/usr/bin/env python3
import os
import sys
import stat

def create_instance_directory():
    """Create the Flask instance directory with proper permissions"""
    # Determine the project root directory (where app.py is located)
    project_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Define the instance directory path
    instance_dir = os.path.join(project_dir, 'instance')
    
    print(f"Project directory: {project_dir}")
    print(f"Instance directory path: {instance_dir}")
    
    # Check if instance directory already exists
    if os.path.exists(instance_dir):
        print(f"Instance directory already exists at: {instance_dir}")
    else:
        try:
            # Create the instance directory with full permissions
            os.makedirs(instance_dir, exist_ok=True)
            os.chmod(instance_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 0777 permissions
            print(f"Created instance directory at: {instance_dir}")
        except Exception as e:
            print(f"Error creating instance directory: {e}")
            return False
    
    # Test if we can write to the instance directory
    test_file = os.path.join(instance_dir, 'test_write.txt')
    try:
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        print("Successfully verified write access to instance directory")
    except Exception as e:
        print(f"Error: Cannot write to instance directory: {e}")
        return False
    
    print(f"Instance directory is ready at: {instance_dir}")
    return True

if __name__ == "__main__":
    if create_instance_directory():
        sys.exit(0)
    else:
        sys.exit(1)