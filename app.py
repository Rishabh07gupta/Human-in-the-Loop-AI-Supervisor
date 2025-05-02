import os
from datetime import datetime, timedelta
import logging
import threading
import time

from flask import Flask, current_app, render_template, request, redirect, url_for, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import click
from flask.cli import with_appcontext

from config import Config
from database import db, HelpRequest
from modules.help_requests import (
    get_help_request, get_pending_requests, 
    resolve_request, mark_request_unresolved,
    memory_help_requests, next_request_id
)
from modules.knowledge_base import (
    get_all_knowledge, init_sample_salon_data,
    memory_knowledge_items, memory_salon_info
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
scheduler.start()

def create_app(config_class=Config):

    app = Flask(__name__)
    app.config.from_object(config_class)
    
    db.init_app(app)
    app.cli.add_command(init_db_command)
    
    register_routes(app)
    
    with app.app_context():
        db.create_all()
        sync_memory_storage()
        
        if not getattr(app, '_timeout_checker_started', False):
            app._timeout_checker_started = True
            scheduler.add_job(
                check_request_timeouts,
                'interval',
                minutes=5,
                id='timeout_checker',
                replace_existing=True,
                args=[app]
            )
    
    return app

def sync_memory_storage():
    """Sync in-memory storage with database for consistent state"""
    logger.info("Syncing memory storage with database...")
    try:
        from database import HelpRequest
        help_requests = HelpRequest.query.all()
        
        global memory_help_requests, next_request_id
        memory_help_requests.clear()
        
        highest_id = 0
        for req in help_requests:
            memory_help_requests[req.id] = req
            if req.id > highest_id:
                highest_id = req.id
        
        next_request_id = highest_id + 1
        from database import KnowledgeItem
        knowledge_items = KnowledgeItem.query.all()
        
        global memory_knowledge_items
        memory_knowledge_items.clear()
        
        for item in knowledge_items:
            memory_knowledge_items[item.id] = item
        
        from database import SalonInfo
        salon_info = SalonInfo.query.all()
        
        global memory_salon_info
        memory_salon_info.clear()
        
        for info in salon_info:
            memory_salon_info[info.key] = info.value
            
        logger.info("Memory storage synced successfully")
    except Exception as e:
        logger.error(f"Error syncing memory storage: {e}")

def check_request_timeouts(app):
    """Check for pending requests that have timed out."""
    with app.app_context():
        timeout_minutes = app.config.get('REQUEST_TIMEOUT_MINUTES', 30)
        timeout_time = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        
        try:
       
            timed_out_requests = HelpRequest.query.filter_by(status='pending')\
                .filter(HelpRequest.created_at < timeout_time).all()
            
            for req in timed_out_requests:
                req.status = 'unresolved'
                logger.info(f"Request {req.id} timed out and marked unresolved")
   
                if req.id in memory_help_requests:
                    memory_help_requests[req.id].status = 'unresolved'
            
            if timed_out_requests:
                db.session.commit()
                logger.info(f"Marked {len(timed_out_requests)} requests as unresolved due to timeout")
        except Exception as e:
            logger.error(f"Error checking request timeouts: {e}")

@click.command('init-db')
@with_appcontext
def init_db_command():
    """Clear existing data and create new tables."""
    try:
        instance_path = current_app.instance_path
        click.echo(f'Checking instance path: {instance_path}')
        
        if not os.path.exists(instance_path):
            try:
                os.makedirs(instance_path)
                click.echo(f'Created instance folder at {instance_path}')
            except Exception as e:
                click.echo(f'Error creating instance directory: {str(e)}')
                raise
        
        click.echo(f"Database URI: {current_app.config['SQLALCHEMY_DATABASE_URI']}")
        
        db_path = os.path.join(instance_path, "supervisor.db")
        db_dir = os.path.dirname(db_path)
        click.echo(f"Database path: {db_path}")
        
        test_file = os.path.join(instance_path, "test_write.txt")
        try:
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
            click.echo("Write permission confirmed")
        except Exception as e:
            click.echo(f"Warning: Cannot write to instance directory: {str(e)}")
        
        click.echo('Initializing sample data...')
        init_sample_salon_data()
        click.echo('Initialized the database and added sample data.')
        
        # Sync memory storage
        sync_memory_storage()
        click.echo('Memory storage synced with database.')
    except Exception as e:
        click.echo(f'Error initializing database: {str(e)}')
        import traceback
        traceback.print_exc()
        raise

def initialize_app():
    """Function to initialize app data (can be called from shell)"""
    init_sample_salon_data()
    # Sync memory storage
    sync_memory_storage()

def get_all_knowledge():
    """Safe wrapper for get_all_knowledge function"""
    try:
        from database import KnowledgeItem
        return KnowledgeItem.query.all()
    except Exception as e:
        logger.warning(f"Could not query knowledge from database: {e}. Using memory storage.")
        return list(memory_knowledge_items.values())

def register_routes(app):
    # Dashboard route
    @app.route('/')
    def dashboard():
        try:
            pending_count = len(get_pending_requests())
            resolved_count = HelpRequest.query.filter_by(status='resolved').count()
            unresolved_count = HelpRequest.query.filter_by(status='unresolved').count()
            knowledge_count = len(get_all_knowledge())
            
            stats = {
                'pending': pending_count,
                'resolved': resolved_count,
                'unresolved': unresolved_count,
                'knowledge': knowledge_count
            }
            
            return render_template('dashboard.html', stats=stats)
        except Exception as e:
            logger.error(f"Error loading dashboard: {e}")
            return render_template('error.html', error=str(e))

    # Pending requests route
    @app.route('/pending')
    def pending_requests():
        try:
            requests = get_pending_requests()
            return render_template('pending_requests.html', requests=requests)
        except Exception as e:
            logger.error(f"Error loading pending requests: {e}")
            return render_template('error.html', error=str(e))

    # Resolve request route
    @app.route('/resolve/<int:request_id>', methods=['POST'])
    def resolve(request_id):
        answer = request.form.get('answer')
        
        if not answer:
            return jsonify({'success': False, 'error': 'Answer is required'}), 400
        
        try:
            help_request = resolve_request(request_id, answer)
            if not help_request:
                return jsonify({'success': False, 'error': 'Request not found'}), 404
            
            from modules.knowledge_base import add_to_knowledge_base
            add_to_knowledge_base(help_request.question, answer)
                
            return jsonify({
                'success': True, 
                'message': f'Request {request_id} resolved successfully',
                'request': {
                    'id': help_request.id,
                    'question': help_request.question,
                    'answer': help_request.answer,
                    'status': help_request.status,
                    'resolved_at': help_request.resolved_at.isoformat() if help_request.resolved_at else None
                }
            })
        except Exception as e:
            logger.error(f"Error resolving request {request_id}: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    # Mark request as unresolved route
    @app.route('/unresolved/<int:request_id>', methods=['POST'])
    def mark_unresolved(request_id):
        try:
            help_request = mark_request_unresolved(request_id)
            if not help_request:
                return jsonify({'success': False, 'error': 'Request not found'}), 404
                
            return jsonify({
                'success': True, 
                'message': f'Request {request_id} marked as unresolved'
            })
        except Exception as e:
            logger.error(f"Error marking request {request_id} as unresolved: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    # Knowledge base route
    @app.route('/knowledge')
    def knowledge_base():
        try:
            knowledge_items = get_all_knowledge()
            return render_template('knowledge_base.html', items=knowledge_items)
        except Exception as e:
            logger.error(f"Error loading knowledge base: {e}")
            return render_template('error.html', error=str(e))

    # API route for request details
    @app.route('/api/request/<int:request_id>')
    def request_details(request_id):
        try:
            help_request = get_help_request(request_id)
            
            if not help_request:
                return jsonify({'error': 'Request not found'}), 404
            
            return jsonify({
                'id': help_request.id,
                'customer_id': help_request.customer_id,
                'question': help_request.question,
                'status': help_request.status,
                'created_at': help_request.created_at.isoformat(),
                'resolved_at': help_request.resolved_at.isoformat() if help_request.resolved_at else None,
                'answer': help_request.answer
            })
        except Exception as e:
            logger.error(f"Error getting request details: {str(e)}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/sync-request', methods=['POST'])
    def sync_request_from_agent():
        try:
            data = request.json
            existing_request = HelpRequest.query.get(data['id']) if 'id' in data else None
            
            if existing_request:
                return jsonify({'success': True, 'id': existing_request.id})
                
            new_request = HelpRequest(
                customer_id=data['customer_id'],
                question=data['question'],
                status='pending',
                webhook_url=data['webhook_url'],
                created_at=datetime.fromisoformat(data['created_at'])
            )
            db.session.add(new_request)
            db.session.commit()
            return jsonify({'success': True, 'id': new_request.id})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/check-request/<int:request_id>')
    def check_request_status(request_id):
        try:
            help_request = get_help_request(request_id)
            
            if not help_request:
                return jsonify({'error': 'Request not found'}), 404
            
            return jsonify({
                'id': help_request.id,
                'status': help_request.status,
                'answer': help_request.answer if help_request.status == 'resolved' else None
            })
        except Exception as e:
            logger.error(f"Error checking request status: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @app.route('/simulate/call', methods=['GET'])
    def simulate_call():
        return render_template('simulate_call.html')

    @app.route('/error')
    def error():
        error_message = request.args.get('error', 'An unknown error occurred')
        return render_template('error.html', error=error_message)

app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=Config.DEBUG)
