import logging
from typing import Dict, List, Optional
from datetime import datetime
import numpy as np
from sentence_transformers import SentenceTransformer
import faiss
import os
from flask import current_app, has_app_context
from database import KnowledgeItem, SalonInfo, db

# Configure logging
logger = logging.getLogger(__name__)

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
    }
}

# --- Semantic Search Components ---
embedding_model_name = 'all-MiniLM-L6-v2'
_embedding_model_instance = None # Store the loaded model instance
faiss_index = None
FAISS_INDEX_PATH = "instance/knowledge_base.index"
knowledge_item_ids_for_faiss = [] # Maps FAISS index position to KnowledgeItem.id

def get_embedding_model():
    """Loads or returns the loaded sentence transformer model."""
    global _embedding_model_instance
    if _embedding_model_instance is None:
        try:
            _embedding_model_instance = SentenceTransformer(embedding_model_name)
            logger.info(f"Successfully loaded SentenceTransformer model: {embedding_model_name}")
        except Exception as e:
            logger.error(f"Failed to load SentenceTransformer model '{embedding_model_name}': {e}")
            raise # Re-raise to indicate critical failure
    return _embedding_model_instance

def generate_embedding(text: str) -> Optional[np.ndarray]:
    """Generates an embedding for a given text."""
    try:
        model = get_embedding_model()
        if not isinstance(text, str):
            logger.warning(f"Invalid input type for embedding generation: {type(text)}. Expected str.")
            return None
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.astype('float32')
    except Exception as e:
        logger.error(f"Error generating embedding for text '{str(text)[:50]}...': {e}")
        return None

def _get_all_knowledge_items_for_indexing():
    """Helper to get all knowledge items, trying DB first then memory."""
    items_for_indexing = []
    if has_app_context() and current_app:
        try:
            # It's crucial that current_app.app_context() is active when this is called
            # or db operations will fail.
            all_items = KnowledgeItem.query.order_by(KnowledgeItem.id).all() # Consistent order is important
            items_for_indexing = [(item.id, item.question) for item in all_items]
        except Exception as e:
            logger.warning(f"Could not query database for FAISS indexing (app context: {has_app_context()}): {e}. Falling back to memory.")
            # Ensure memory_knowledge_items is up-to-date if this fallback is critical
            items_for_indexing = sorted([(item.id, item.question) for item_id, item in memory_knowledge_items.items() if hasattr(item, 'id') and hasattr(item, 'question')], key=lambda x: x[0])
    else:
        logger.info("No Flask app context for DB query during FAISS indexing. Using in-memory items.")
        items_for_indexing = sorted([(item.id, item.question) for item_id, item in memory_knowledge_items.items() if hasattr(item, 'id') and hasattr(item, 'question')], key=lambda x: x[0])
    return items_for_indexing


def build_or_load_faiss_index(force_rebuild=False):
    """Builds a new FAISS index or loads from disk."""
    global faiss_index, knowledge_item_ids_for_faiss, FAISS_INDEX_PATH
    if not force_rebuild and os.path.exists(FAISS_INDEX_PATH):
        try:
            faiss_index = faiss.read_index(FAISS_INDEX_PATH)
            indexed_items = _get_all_knowledge_items_for_indexing()
            temp_ids = [item[0] for item in indexed_items]

            if faiss_index.ntotal == len(temp_ids):
                knowledge_item_ids_for_faiss = temp_ids
                logger.info(f"FAISS index loaded from {FAISS_INDEX_PATH} with {faiss_index.ntotal} vectors. ID mapping successful.")
                return
            else:
                logger.warning(f"FAISS index size ({faiss_index.ntotal}) mismatches item count ({len(temp_ids)}). Forcing rebuild.")
        except Exception as e:
            logger.error(f"Error loading FAISS index from {FAISS_INDEX_PATH}: {e}. Will attempt to rebuild.")

    logger.info("Building new FAISS index...")
    items_to_index = _get_all_knowledge_items_for_indexing()

    if not items_to_index:
        logger.warning("No knowledge items found to build FAISS index. Index will be empty.")
        faiss_index = None 
        knowledge_item_ids_for_faiss = []
        # Optionally, delete old index file if it exists and is now invalid
        if os.path.exists(FAISS_INDEX_PATH):
            try:
                os.remove(FAISS_INDEX_PATH)
            except OSError as e_os:
                logger.error(f"Could not remove old FAISS index file {FAISS_INDEX_PATH}: {e_os}")
        return

    questions = [item[1] for item in items_to_index]
    current_knowledge_item_ids = [item[0] for item in items_to_index]

    try:
        model = get_embedding_model()
        valid_questions = [q for q in questions if isinstance(q, str)]
        if len(valid_questions) != len(questions):
            logger.warning(f"Some questions were invalid for embedding. Original: {len(questions)}, Valid: {len(valid_questions)}")

        if not valid_questions:
            logger.warning("No valid questions to generate embeddings. FAISS index will be empty.")
            faiss_index = None
            knowledge_item_ids_for_faiss = []
            return

        embeddings = model.encode(valid_questions, convert_to_numpy=True, show_progress_bar=False).astype('float32')

        if embeddings.ndim == 1 and embeddings.size > 0:
            embeddings = np.expand_dims(embeddings, axis=0)
        elif embeddings.shape[0] == 0:
             logger.warning("No embeddings generated. FAISS index will be empty.")
             faiss_index = None
             knowledge_item_ids_for_faiss = []
             return

        dimension = embeddings.shape[1]
        faiss_index = faiss.IndexFlatL2(dimension)
        faiss_index.add(embeddings)
        knowledge_item_ids_for_faiss = current_knowledge_item_ids # Store the IDs corresponding to the current index order

        logger.info(f"FAISS index built successfully with {faiss_index.ntotal} vectors.")

        instance_dir = os.path.dirname(FAISS_INDEX_PATH)
        if not os.path.exists(instance_dir):
            try:
                os.makedirs(instance_dir)
                logger.info(f"Created instance directory: {instance_dir}")
            except OSError as e_os:
                logger.error(f"Could not create instance directory {instance_dir}: {e_os}")
                return

        faiss.write_index(faiss_index, FAISS_INDEX_PATH)
        logger.info(f"FAISS index saved to {FAISS_INDEX_PATH}")

    except Exception as e:
        logger.error(f"Error building or saving FAISS index: {e}", exc_info=True)
        faiss_index = None 
        knowledge_item_ids_for_faiss = []


def search_knowledge_semantic(question_text: str, top_k=5) -> List[Dict]:
    """Searches the knowledge base using semantic similarity."""
    global faiss_index, knowledge_item_ids_for_faiss
    if faiss_index is None or faiss_index.ntotal == 0:
        logger.warning("FAISS index is not available or empty. Attempting to load/build.")
        return []

    query_embedding = generate_embedding(question_text)
    if query_embedding is None:
        logger.error("Failed to generate query embedding. Semantic search cannot proceed.")
        return []

    query_embedding_2d = np.expand_dims(query_embedding, axis=0)

    try:
        distances, indices = faiss_index.search(query_embedding_2d, top_k)
        results = []
        if indices.size == 0 or distances.size == 0: 
            return []

        for i in range(len(indices[0])):
            faiss_list_idx = indices[0][i]
            if faiss_list_idx == -1:
                continue

            if 0 <= faiss_list_idx < len(knowledge_item_ids_for_faiss):
                original_db_id = knowledge_item_ids_for_faiss[faiss_list_idx]
                distance = distances[0][i]
                similarity_score = float(1 / (1 + distance)) if distance >= 0 else 0.0
                results.append({"id": original_db_id, "score": similarity_score, "match_type": "semantic"})
            else:
                logger.warning(f"FAISS returned out-of-bounds index: {faiss_list_idx} for knowledge_item_ids_for_faiss length {len(knowledge_item_ids_for_faiss)}")
        return sorted(results, key=lambda x: x['score'], reverse=True)
    except Exception as e:
        logger.error(f"Error during FAISS search: {e}", exc_info=True)
        return []


class MockKnowledgeItem:
    """Mock KnowledgeItem for use outside of Flask context"""
    def __init__(self, id_val, question, answer):
        self.id = id_val
        self.question = question
        self.answer = answer
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

def get_salon_info_standalone() -> str:
    """Get formatted salon information without requiring Flask app context."""
    formatted_info = []
    for key, value in memory_salon_info.items():
        if key != "services_detailed":
            formatted_info.append(f"{key}: {value}")
    if "services_detailed" in memory_salon_info:
        formatted_info.append("\nDetailed Services:")
        for service_info in memory_salon_info["services_detailed"].values():
            formatted_info.append(f"- {service_info['name']}: {service_info['price']}")
    return "\n".join(formatted_info)

def get_salon_info() -> str:
    """Get formatted salon information for agent instructions."""
    from database import SalonInfo
    if not has_app_context() or not current_app:
        logger.warning("No Flask app context in get_salon_info. Using standalone info.")
        return get_salon_info_standalone()
    try:
        info_items = SalonInfo.query.all()
        if not info_items and memory_salon_info:
            logger.warning("Salon info from DB is empty, using in-memory defaults for formatting.")
            return get_salon_info_standalone()
        return "\n".join([f"{item.key}: {item.value}" for item in info_items])
    except Exception as e:
        logger.error(f"Could not query salon info from database: {e}. Using standalone info.")
        return get_salon_info_standalone()


def add_to_knowledge_base(question: str, answer: str):
    """Adds or updates a knowledge item and rebuilds the FAISS index."""
    created_or_updated_item = None
    app_ctx_available = has_app_context() and current_app is not None

    if app_ctx_available:
        try:
            with current_app.app_context():
                existing = KnowledgeItem.query.filter_by(question=question).first()
                if existing:
                    existing.answer = answer
                    existing.updated_at = datetime.utcnow()
                    created_or_updated_item = existing
                else:
                    knowledge_item = KnowledgeItem(question=question, answer=answer)
                    db.session.add(knowledge_item)
                    created_or_updated_item = knowledge_item
                db.session.commit()
                logger.info(f"Knowledge item '{question[:50]}...' {'updated' if existing else 'added'} to DB.")
                # Update memory version for consistency if other parts rely on it as fallback
                if created_or_updated_item and created_or_updated_item.id:
                     memory_knowledge_items[created_or_updated_item.id] = created_or_updated_item

        except Exception as e:
            logger.error(f"DB error adding/updating knowledge item '{question[:50]}...': {e}. Falling back to memory.", exc_info=True)
            app_ctx_available = False # Indicate DB operation failed

    if not app_ctx_available:
        logger.warning(f"Adding/updating knowledge item '{question[:50]}...' in memory only.")
        found_in_memory = False
        for item_id, item_obj in memory_knowledge_items.items():
            if hasattr(item_obj, 'question') and item_obj.question == question:
                item_obj.answer = answer
                if hasattr(item_obj, 'updated_at'): item_obj.updated_at = datetime.utcnow()
                created_or_updated_item = item_obj
                found_in_memory = True
                logger.info(f"Knowledge item '{question[:50]}...' updated in memory.")
                break
        if not found_in_memory:
            new_id = (max(memory_knowledge_items.keys() or [0]) + 1)
            item = MockKnowledgeItem(new_id, question, answer)
            memory_knowledge_items[new_id] = item
            created_or_updated_item = item
            logger.info(f"Knowledge item '{question[:50]}...' added to memory with ID {new_id}.")

    if created_or_updated_item:
        logger.info("Knowledge base changed. Rebuilding FAISS index.")
        build_or_load_faiss_index(force_rebuild=True)
    else:
        logger.warning("No item was created or updated. FAISS index not rebuilt.")

    return created_or_updated_item


def get_all_knowledge():
    """Get all knowledge items, works with or without Flask context"""
    if has_app_context() and current_app:
        try:
            return KnowledgeItem.query.all()
        except Exception as e:
            logger.warning(f"Could not query knowledge base from DB: {e}. Using in-memory items.")
            return list(memory_knowledge_items.values())
    else:
        logger.info("No Flask app context. Returning in-memory knowledge items.")
        return list(memory_knowledge_items.values())

def add_salon_info(key: str, value: str):
    """Add or update salon information."""
    app_ctx_available = has_app_context() and current_app is not None
    updated_info = None

    if app_ctx_available:
        try:
            with current_app.app_context():
                existing = SalonInfo.query.filter_by(key=key).first()
                if existing:
                    existing.value = value
                    updated_info = existing
                else:
                    new_info = SalonInfo(key=key, value=value)
                    db.session.add(new_info)
                    updated_info = new_info
                db.session.commit()
                memory_salon_info[key] = value # Sync memory
                logger.info(f"Salon info for key '{key}' {'updated' if existing else 'added'} to DB.")
                return updated_info
        except Exception as e:
            logger.error(f"Could not add/update salon info to database for key '{key}': {e}. Using in-memory only.")
    
    # Memory-only operation
    logger.warning(f"Salon info for key '{key}' stored in memory only.")
    memory_salon_info[key] = value
    class InfoObj:
        def __init__(self, k, v): self.key = k; self.value = v
    return InfoObj(key, value)


def init_sample_salon_data():
    """Initialize sample salon data for testing if not present."""
    logger.info("Initializing sample salon data...")
    sample_data = {
        "name": "Elegant Beauty Salon & Spa",
        "address": "123 Style Street, Fashion City, FC 12345",
        "phone": "555-123-4567",
        "emergency_contact": "555-987-6543",
        "hours": "Monday-Friday: 9:00 AM - 7:00 PM\nSaturday: 10:00 AM - 5:00 PM\nSunday: Closed",
        "holiday_hours": "Closed on Christmas Day and New Year's Day",
        "services": """Hair Services:\n- Women's Haircut: $60-$90 (based on length)\n- Men's Haircut: $35-$50\nNail Services:\n- Basic Manicure: $25\nSkincare:\n- Basic Facial: $80""",
        "stylists": """Our Specialists:\n- Mia (Master Colorist)\n- James (Barber)""",
        "cancellation_policy": "We require 24 hours notice for cancellations. Late cancellations incur a 50% fee.",
        "child_policy": "Children under 12 must be accompanied by an adult.",
        "accessibility": "Our salon is fully wheelchair accessible.",
        "retail_products": "We carry: Olaplex, Redken, OPI, Dermalogica"
    }
    added_any = False
    for key, value in sample_data.items():
        if has_app_context() and current_app:
            with current_app.app_context():
                if not SalonInfo.query.filter_by(key=key).first():
                    add_salon_info(key, value)
                    added_any = True
        else: 
            if key not in memory_salon_info:
                 add_salon_info(key,value)
                 added_any = True


    if not added_any and not memory_salon_info:
        logger.info("No new sample salon data added as it might already exist or no app context for DB check.")
    elif added_any :
        logger.info("Sample salon data initialization complete.")

    sample_kb = [
        {"question": "How much is a men's haircut?", "answer": "Our men's haircuts range from $35 to $50."},
        {"question": "Do you take walk-ins?", "answer": "Yes, we accept walk-ins based on availability, but appointments are recommended."}
    ]
    kb_added_any = False
    for item_data in sample_kb:
        q, a = item_data["question"], item_data["answer"]
        if has_app_context() and current_app:
             with current_app.app_context():
                if not KnowledgeItem.query.filter_by(question=q).first():
                    add_to_knowledge_base(q, a) # This will also trigger FAISS rebuild
                    kb_added_any = True
        else:
            if not any(kb_item.question == q for kb_item in memory_knowledge_items.values() if hasattr(kb_item, 'question')):
                add_to_knowledge_base(q, a)
                kb_added_any = True


    if kb_added_any:
        logger.info("Sample knowledge base items added.")
        
    if added_any or kb_added_any:
        logger.info("Sample data added, ensuring FAISS index is up-to-date.")
        build_or_load_faiss_index(force_rebuild=True)