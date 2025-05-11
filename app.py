import os
from datetime import datetime, timedelta
import logging
from flask import Flask, current_app, render_template, request, redirect, url_for, jsonify, abort
from apscheduler.schedulers.background import BackgroundScheduler
import click
from flask.cli import with_appcontext
from config import Config
from database import db, HelpRequest, KnowledgeItem
from modules.help_requests import (
    get_help_request as get_hr_by_id,
    get_pending_requests as get_all_pending_hr,
    resolve_request as resolve_hr_func,
    mark_request_unresolved as mark_hr_unresolved_func, 
    memory_help_requests,
    next_request_id as memory_next_hr_id
)
from modules.knowledge_base import (
    get_all_knowledge as get_all_kb_items,
    init_sample_salon_data,
    add_to_knowledge_base as add_kb_item,
    memory_knowledge_items,
    memory_salon_info,
    build_or_load_faiss_index,
    search_knowledge_semantic,
    get_embedding_model
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(daemon=True)

def create_app(config_class=Config):
    app = Flask(__name__, instance_path=config_class.INSTANCE_PATH)
    app.config.from_object(config_class)

    if not os.path.exists(app.instance_path):
        try:
            os.makedirs(app.instance_path)
            logger.info(f"Instance path created at: {app.instance_path}")
        except OSError as e:
            logger.error(f"Could not create instance path {app.instance_path}: {e}")

    db.init_app(app)
    app.cli.add_command(init_db_command)
    app.cli.add_command(build_index_command)

    register_routes(app)
    register_error_handlers(app)

    with app.app_context():
        try:
            db.create_all()
            logger.info("Database tables checked/created.")
        except Exception as e:
            logger.error(f"Error during db.create_all(): {e}", exc_info=True)

        sync_memory_storage_from_db()
        
        logger.info("Initializing semantic search components...")
        try:
            get_embedding_model()
            build_or_load_faiss_index()
            logger.info("Semantic search components initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize semantic search components: {e}", exc_info=True)

        if not scheduler.running:
            try:
                scheduler.start()
                logger.info("APScheduler started.")
            except Exception as e:
                logger.error(f"Failed to start APScheduler: {e}", exc_info=True)

        timeout_job_id = 'timeout_checker_job'
        if scheduler.running and not scheduler.get_job(timeout_job_id):
            try:
                scheduler.add_job(
                    check_request_timeouts_job,
                    'interval',
                    minutes=app.config.get('REQUEST_TIMEOUT_CHECK_INTERVAL_MINUTES', 5),
                    id=timeout_job_id,
                    replace_existing=True,
                    args=[app]
                )
                logger.info(f"'{timeout_job_id}' scheduled successfully.")
            except Exception as e:
                logger.error(f"Error scheduling '{timeout_job_id}': {e}", exc_info=True)
        elif scheduler.get_job(timeout_job_id):
             logger.info(f"'{timeout_job_id}' already scheduled.")
    return app


def sync_memory_storage_from_db():
    logger.info("Syncing in-memory storage with database...")
    global memory_help_requests, memory_next_hr_id, memory_knowledge_items, memory_salon_info
    try:
        help_requests_db = HelpRequest.query.all()
        memory_help_requests.clear()
        highest_hr_id = 0
        for req in help_requests_db:
            memory_help_requests[req.id] = req
            if req.id > highest_hr_id:
                highest_hr_id = req.id
        memory_next_hr_id = highest_hr_id + 1
        logger.info(f"Synced {len(memory_help_requests)} help requests. Next memory ID: {memory_next_hr_id}")

        knowledge_items_db = KnowledgeItem.query.all()
        memory_knowledge_items.clear()
        for item in knowledge_items_db:
            memory_knowledge_items[item.id] = item
        logger.info(f"Synced {len(memory_knowledge_items)} knowledge items.")
        logger.info("In-memory storage sync complete.")
    except Exception as e:
        logger.error(f"Error syncing memory storage: {e}", exc_info=True)


def check_request_timeouts_job(app_instance):
    with app_instance.app_context():
        timeout_minutes = current_app.config.get('REQUEST_TIMEOUT_MINUTES', 30)
        timeout_delta = timedelta(minutes=timeout_minutes)
        if timeout_delta is None:
            logger.error("REQUEST_TIMEOUT_MINUTES not configured correctly. Timeout check skipped.")
            return

        cutoff_time = datetime.utcnow() - timeout_delta
        logger.info(f"Running request timeout check for requests older than {cutoff_time} (timeout: {timeout_minutes} mins)")
        
        try:
            timed_out_requests = HelpRequest.query.filter(
                HelpRequest.status == 'pending',
                HelpRequest.created_at < cutoff_time
            ).all()
            
            if not timed_out_requests:
                logger.info("No requests timed out in this check.")
                return

            for req in timed_out_requests:
                req.status = 'unresolved'
                if req.id in memory_help_requests:
                     memory_help_requests[req.id].status = 'unresolved'
                logger.warning(
                    f"Request {req.id} (Customer: {req.customer_id}, Q: '{req.question[:50]}...') "
                    f"timed out after {timeout_minutes} minutes and marked unresolved."
                )
            
            db.session.commit()
            logger.info(f"Marked {len(timed_out_requests)} requests as unresolved due to timeout.")
        except Exception as e:
            logger.error(f"Error checking request timeouts: {e}", exc_info=True)
            db.session.rollback()


@click.command('init-db')
@with_appcontext
def init_db_command():
    click.echo(f"Initializing database at: {current_app.config['SQLALCHEMY_DATABASE_URI']}")
    click.echo('Initializing sample salon and knowledge data...')
    try:
        init_sample_salon_data()
        db.session.commit()
        click.echo('Sample data initialization process finished.')
        
        sync_memory_storage_from_db()
        click.echo('Memory storage synced with database.')

        click.echo('Building/verifying FAISS index...')
        build_or_load_faiss_index(force_rebuild=True)
        click.echo('FAISS index process finished.')
        click.echo('Database initialization complete.')
    except Exception as e:
        click.echo(f'Error during init-db: {str(e)}')
        logger.error(f"Error during init-db command: {e}", exc_info=True)
        db.session.rollback()


@click.command('build-index')
@with_appcontext
def build_index_command():
    click.echo('Starting FAISS index build/rebuild...')
    try:
        get_embedding_model()
        build_or_load_faiss_index(force_rebuild=True)
        click.echo('FAISS index build/rebuild completed successfully.')
    except Exception as e:
        click.echo(f'Error building FAISS index: {str(e)}')
        logger.error(f"Error during build-index command: {e}", exc_info=True)

def register_routes(app):
    @app.route('/')
    def dashboard(): # Endpoint name: 'dashboard'
        try:
            pending_count = HelpRequest.query.filter_by(status='pending').count()
            resolved_count = HelpRequest.query.filter_by(status='resolved').count()
            unresolved_count = HelpRequest.query.filter_by(status='unresolved').count()
            knowledge_count = KnowledgeItem.query.count()
            
            stats = {
                'pending': pending_count,
                'resolved': resolved_count,
                'unresolved': unresolved_count,
                'knowledge': knowledge_count
            }
            return render_template('dashboard.html', stats=stats)
        except Exception as e:
            logger.error(f"Error loading dashboard: {e}", exc_info=True)
            abort(500, description="Could not load dashboard statistics.")


    @app.route('/pending')
    def pending_requests(): # Endpoint name: 'pending_requests'
        try:
            requests_data = get_all_pending_hr()
            return render_template('pending_requests.html', requests=requests_data)
        except Exception as e:
            logger.error(f"Error loading pending requests: {e}", exc_info=True)
            abort(500, description="Could not load pending requests.")


    @app.route('/resolve/<int:request_id>', methods=['POST'])
    def resolve_request(request_id): # Endpoint name: 'resolve_request'
        answer = request.form.get('answer')
        if not answer or not answer.strip():
            return jsonify({'success': False, 'error': 'Answer is required and cannot be empty.'}), 400
        
        try:
            help_request_obj = resolve_hr_func(request_id, answer) 
            if not help_request_obj:
                return jsonify({'success': False, 'error': 'Request not found or could not be resolved.'}), 404
            
            return jsonify({
                'success': True,
                'message': f'Request {request_id} resolved successfully.',
                'request': {
                    'id': help_request_obj.id,
                    'question': help_request_obj.question,
                    'answer': help_request_obj.answer,
                    'status': help_request_obj.status, 
                    'resolved_at': help_request_obj.resolved_at.isoformat() if help_request_obj.resolved_at else None
                }
            })
        except Exception as e:
            logger.error(f"Error resolving request {request_id}: {str(e)}", exc_info=True)
            return jsonify({'success': False, 'error': f'An unexpected error occurred: {str(e)}'}), 500


    @app.route('/unresolved/<int:request_id>', methods=['POST'])
    def mark_unresolved(request_id): # Endpoint name: 'mark_unresolved'
        try:
            help_request_obj = mark_hr_unresolved_func(request_id)
            if not help_request_obj:
                return jsonify({'success': False, 'error': 'Request not found.'}), 404
        
            # We return the status as 'unresolved' because that's the action performed.
            return jsonify({
                'success': True,
                'message': f'Request {request_id} marked as unresolved.',
                 'request_status': 'unresolved' 
            })
        except Exception as e:
            logger.error(f"Error marking request {request_id} as unresolved: {str(e)}", exc_info=True)
            # Check if it's a DetachedInstanceError and handle if specifically needed
            if "DetachedInstanceError" in str(e):
                 logger.error(f"DetachedInstanceError encountered for request {request_id}. This might indicate a session issue.")
                 return jsonify({'success': False, 'error': 'Session issue after marking unresolved. Please refresh.'}), 500
            return jsonify({'success': False, 'error': str(e)}), 500


    @app.route('/knowledge')
    def knowledge_base(): # Endpoint name: 'knowledge_base'
        try:
            items = get_all_kb_items()
            return render_template('knowledge_base.html', items=items)
        except Exception as e:
            logger.error(f"Error loading knowledge base: {e}", exc_info=True)
            abort(500, description="Could not load knowledge base.")

    @app.route('/unresolved')
    def unresolved_requests(): # Endpoint name: 'unresolved_requests'
        try:
            requests_data = HelpRequest.query.filter_by(status='unresolved').order_by(HelpRequest.created_at.desc()).all()
            return render_template('unresolved_requests.html', requests=requests_data)
        except Exception as e:
            logger.error(f"Error loading unresolved requests: {e}", exc_info=True)

    # --- API Routes ---
    @app.route('/api/request/<int:request_id>')
    def api_request_details(request_id): # Endpoint name: 'api_request_details'
        try:
            help_request = get_hr_by_id(request_id)
            if not help_request:
                return jsonify({'success': False, 'error': 'Request not found'}), 404
            
            return jsonify({
                'success': True,
                'id': help_request.id,
                'customer_id': help_request.customer_id,
                'question': help_request.question,
                'status': help_request.status,
                'created_at': help_request.created_at.isoformat() if help_request.created_at else None,
                'resolved_at': help_request.resolved_at.isoformat() if help_request.resolved_at else None,
                'answer': help_request.answer
            })
        except Exception as e:
            logger.error(f"Error getting request details for ID {request_id}: {str(e)}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    
    @app.route('/api/sync-request', methods=['POST'])
    def api_sync_request(): # Endpoint name: 'api_sync_request'
        try:
            data = request.json
            if not data or not all(k in data for k in ['customer_id', 'question', 'webhook_url', 'created_at']):
                return jsonify({'success': False, 'error': 'Missing required fields in sync request.'}), 400

            new_request = HelpRequest(
                customer_id=data['customer_id'],
                question=data['question'],
                status='pending',
                webhook_url=data['webhook_url'],
                created_at=datetime.fromisoformat(data['created_at'])
            )
            db.session.add(new_request)
            db.session.commit()
            
            if new_request.id:
                memory_help_requests[new_request.id] = new_request
                logger.info(f"Help request {new_request.id} synced from agent and added to DB & memory.")
                return jsonify({'success': True, 'id': new_request.id, 'message': 'Request synced successfully.'}), 201
            else:
                logger.error("Failed to get new_request.id after commit during API sync.")
                return jsonify({'success': False, 'error': "Failed to create request in DB."}), 500
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error syncing request from agent: {str(e)}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    
    @app.route('/api/check-request/<int:request_id>')
    def api_check_request(request_id): # Endpoint name: 'api_check_request'
        try:
            help_request = get_hr_by_id(request_id)
            if not help_request:
                return jsonify({'success': False, 'error': 'Request not found'}), 404
            
            return jsonify({
                'success': True,
                'id': help_request.id,
                'status': help_request.status,
                'answer': help_request.answer if help_request.status == 'resolved' else None
            })
        except Exception as e:
            logger.error(f"Error checking request status for ID {request_id}: {str(e)}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    
    @app.route('/api/knowledge/query', methods=['POST'])
    def api_knowledge_query(): # Endpoint name: 'api_knowledge_query'
        try:
            data = request.json
            question_text = data.get('question')
            
            if not question_text or not question_text.strip():
                return jsonify({'success': False, 'found': False, 'error': 'Question is required and cannot be empty.'}), 400
            
            top_k_semantic = current_app.config.get('SEMANTIC_SEARCH_TOP_K', 3)
            semantic_score_threshold = current_app.config.get('SEMANTIC_SCORE_THRESHOLD', 0.70)
            keyword_score_threshold = current_app.config.get('KEYWORD_SCORE_THRESHOLD', 0.85)
            final_result_threshold = current_app.config.get('FINAL_RESULT_THRESHOLD', 0.65)

            semantic_matches_details = []
            raw_semantic_matches = search_knowledge_semantic(question_text, top_k=top_k_semantic)
            for match in raw_semantic_matches:
                if match['score'] >= semantic_score_threshold:
                    item = KnowledgeItem.query.get(match["id"])
                    if item:
                        semantic_matches_details.append({
                            "id": item.id, "question": item.question, "answer": item.answer,
                            "score": match["score"], "match_type": "semantic"
                        })
            
            keyword_matches_details = []
            exact_match_item = KnowledgeItem.query.filter(KnowledgeItem.question.ilike(question_text)).first()
            if exact_match_item:
                keyword_matches_details.append({
                    "id": exact_match_item.id, "question": exact_match_item.question, "answer": exact_match_item.answer,
                    "score": 1.0, "match_type": "exact_keyword"
                })
            else:
                all_db_items = KnowledgeItem.query.all()
                query_words_lower = set(question_text.lower().split())
                for item in all_db_items:
                    item_words_lower = set(item.question.lower().split())
                    if not item_words_lower: continue
                    
                    intersection = query_words_lower.intersection(item_words_lower)
                    union_len = len(query_words_lower.union(item_words_lower))
                    overlap_score = len(intersection) / union_len if union_len > 0 else 0.0

                    if overlap_score >= keyword_score_threshold:
                         keyword_matches_details.append({
                            "id": item.id, "question": item.question, "answer": item.answer,
                            "score": overlap_score, "match_type": "keyword_overlap"
                        })
                keyword_matches_details = sorted(keyword_matches_details, key=lambda x: x['score'], reverse=True)

            final_candidates = {}
            for res_list in [keyword_matches_details, semantic_matches_details]:
                for res in res_list:
                    if res['id'] not in final_candidates:
                        final_candidates[res['id']] = res
                    else:
                        if res['match_type'] == 'exact_keyword':
                            final_candidates[res['id']] = res
                        elif final_candidates[res['id']]['match_type'] != 'exact_keyword' and \
                             res['score'] > final_candidates[res['id']]['score']:
                             final_candidates[res['id']] = res

            if not final_candidates:
                return jsonify({'success': True, 'found': False, 'message': 'No relevant knowledge found.'})

            ranked_results = sorted(
                list(final_candidates.values()),
                key=lambda x: (1 if x['match_type'] == 'exact_keyword' else 0, x['score']),
                reverse=True
            )

            best_match = ranked_results[0]
            if best_match['score'] >= final_result_threshold:
                return jsonify({
                    'success': True, 'found': True,
                    'id': best_match['id'], 'question': best_match['question'],
                    'answer': best_match['answer'], 'score': best_match['score'],
                    'match_type': best_match['match_type']
                })
            else:
                return jsonify({
                    'success': True, 'found': False,
                    'message': f'Best match score {best_match["score"]:.2f} below threshold {final_result_threshold}.',
                    'debug_best_match_type': best_match['match_type']
                })

        except Exception as e:
            logger.error(f"Error querying knowledge base API: {e}", exc_info=True)
            return jsonify({'success': False, 'found': False, 'error': f'An internal error occurred: {str(e)}'}), 500

def register_error_handlers(app):
    @app.errorhandler(404)
    def page_not_found_error(error):
        logger.warning(f"404 error encountered for path: {request.path}. Description: {error.description if hasattr(error, 'description') else 'Not found'}")
        return jsonify(error="Not Found", message=str(error.description if hasattr(error, 'description') else "The requested URL was not found on the server.")), 404

    @app.errorhandler(500)
    def internal_server_error_handler(error):
        err_description = "Internal Server Error"
        if hasattr(error, 'description') and error.description:
            err_description = str(error.description)
        elif hasattr(error, 'original_exception') and error.original_exception:
             err_description = str(error.original_exception)

        if 'db' in globals() and hasattr(db, 'session') and db.session.is_active:
            db.session.rollback()
            
        logger.error(f"500 internal server error: {err_description}", exc_info=True)
        return jsonify(error="Internal Server Error", message=err_description), 500


app = create_app()

if __name__ == '__main__':
    app.run(
        host=app.config.get('HOST', '0.0.0.0'),
        port=app.config.get('PORT', 5000),
        debug=app.config.get('DEBUG', False)
    )