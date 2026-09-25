"""
Content Analysis API Routes
Provides endpoints for accessing content analysis results
"""

from flask import Blueprint, jsonify, request
from typing import Dict, List, Optional, Any
from datetime import date, datetime
import logging

from core.errors import client_error

from database.analyzers import ContentAnalysisEngine
from database.analyzers.analysis_engine import AnalysisConfig
from database import DatabaseHub

logger = logging.getLogger(__name__)

content_analysis_bp = Blueprint('content_analysis', __name__)


@content_analysis_bp.route('/api/analysis/file/<int:file_id>', methods=['GET'])
def analyze_file(file_id: int):
    """
    Analyze a single file by ID.
    
    Args:
        file_id: Path ID of the file to analyze
        
    Returns:
        JSON response with analysis results
    """
    try:
        # Initialize analysis engine
        db_hub = DatabaseHub()
        engine = ContentAnalysisEngine(db_hub=db_hub)
        
        # Analyze file
        results = engine.analyze_file(file_id)
        
        if 'error' in results:
            return jsonify({
                'success': False,
                'error': results['error']
            }), 400
        
        return jsonify({
            'success': True,
            'data': results
        })
        
    except Exception as e:
        logger.error(f"Error analyzing file {file_id}: {e}", exc_info=True)
        # Provide user-friendly error message
        error_message = str(e)
        if 'not found' in error_message.lower() or 'does not exist' in error_message.lower():
            user_message = f"File with ID {file_id} was not found in the database."
        elif 'permission' in error_message.lower() or 'access' in error_message.lower():
            user_message = f"Permission denied while accessing file {file_id}. Please check file permissions."
        elif 'timeout' in error_message.lower():
            user_message = f"Analysis timed out for file {file_id}. The file may be too large or the system is busy."
        else:
            user_message = f"An error occurred while analyzing file {file_id}. Please try again or contact support if the problem persists."
        
        # The classification above is internal; the reader gets the sentence
        # it selected and a correlation id, never the exception text - which
        # used to be returned whenever debug logging was on.
        return client_error(e, subsystem="content_analysis",
                            public_message=user_message,
                            success_key="success")


@content_analysis_bp.route('/api/analysis/batch', methods=['POST'])
def analyze_batch():
    """
    Analyze multiple files in batch.
    
    Request body (JSON):
        {
            "file_ids": [1, 2, 3],  // Optional: specific file IDs
            "config": {              // Optional: analysis configuration
                "enable_sentiment": true,
                "enable_topics": true,
                "max_files": 100,
                "file_types": [".pdf", ".docx"],
                "date_from": "2024-01-01",
                "date_to": "2024-12-31"
            }
        }
        
    Returns:
        JSON response with aggregated analysis results
    """
    try:
        data = request.get_json() or {}
        file_ids = data.get('file_ids')
        config_data = data.get('config', {})
        
        # Build configuration
        config = AnalysisConfig()
        
        # Update config from request
        if 'enable_sentiment' in config_data:
            config.enable_sentiment = config_data['enable_sentiment']
        if 'enable_topics' in config_data:
            config.enable_topics = config_data['enable_topics']
        if 'enable_trends' in config_data:
            config.enable_trends = config_data['enable_trends']
        if 'enable_patterns' in config_data:
            config.enable_patterns = config_data['enable_patterns']
        if 'enable_entities' in config_data:
            config.enable_entities = config_data['enable_entities']
        if 'enable_statistics' in config_data:
            config.enable_statistics = config_data['enable_statistics']
        if 'max_files' in config_data:
            config.max_files = config_data['max_files']
        if 'batch_size' in config_data:
            config.batch_size = config_data['batch_size']
        if 'file_types' in config_data:
            config.file_types = config_data['file_types']
        if 'date_from' in config_data:
            try:
                config.date_from = datetime.fromisoformat(config_data['date_from']).date()
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid date_from format '{config_data['date_from']}': {e}")
                return jsonify({
                    'success': False,
                    'error': f"Invalid date format for 'date_from'. Expected format: YYYY-MM-DD, got: {config_data['date_from']}"
                }), 400
        if 'date_to' in config_data:
            try:
                config.date_to = datetime.fromisoformat(config_data['date_to']).date()
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid date_to format '{config_data['date_to']}': {e}")
                return jsonify({
                    'success': False,
                    'error': f"Invalid date format for 'date_to'. Expected format: YYYY-MM-DD, got: {config_data['date_to']}"
                }), 400
        if 'source_ids' in config_data:
            config.source_ids = config_data['source_ids']
        if 'side_ids' in config_data:
            config.side_ids = config_data['side_ids']
        
        # Initialize analysis engine
        db_hub = DatabaseHub()
        engine = ContentAnalysisEngine(db_hub=db_hub, config=config)
        
        # Analyze batch
        results = engine.analyze_batch(path_ids=file_ids)
        
        if 'error' in results:
            return jsonify({
                'success': False,
                'error': results['error']
            }), 400
        
        return jsonify({
            'success': True,
            'data': results
        })
        
    except Exception as e:
        logger.error(f"Error in batch analysis: {e}", exc_info=True)
        # Provide user-friendly error message
        error_message = str(e)
        if 'database' in error_message.lower() or 'connection' in error_message.lower():
            user_message = "Database connection error. Please check your database settings and try again."
        elif 'timeout' in error_message.lower():
            user_message = "Analysis timed out. The batch may be too large. Try processing fewer files at once."
        elif 'memory' in error_message.lower() or 'out of memory' in error_message.lower():
            user_message = "Insufficient memory to process the batch. Try reducing the batch size or processing files individually."
        else:
            user_message = "An error occurred during batch analysis. Please try again or contact support if the problem persists."
        
        # The classification above is internal; the reader gets the sentence
        # it selected and a correlation id, never the exception text - which
        # used to be returned whenever debug logging was on.
        return client_error(e, subsystem="content_analysis",
                            public_message=user_message,
                            success_key="success")


@content_analysis_bp.route('/api/analysis/sentiment/<int:file_id>', methods=['GET'])
def get_sentiment(file_id: int):
    """Get sentiment analysis for a single file"""
    try:
        db_hub = DatabaseHub()
        config = AnalysisConfig()
        config.enable_topics = False
        config.enable_trends = False
        config.enable_patterns = False
        config.enable_entities = False
        
        engine = ContentAnalysisEngine(db_hub=db_hub, config=config)
        results = engine.analyze_file(file_id)
        
        if 'error' in results:
            return jsonify({
                'success': False,
                'error': results['error']
            }), 400
        
        return jsonify({
            'success': True,
            'data': results.get('sentiment', {})
        })
        
    except Exception as e:
        logger.error(f"Error getting sentiment for file {file_id}: {e}",
                     exc_info=True)
        return client_error(e, subsystem="content_analysis",
                            public_message="The sentiment analysis could not be read",
                            success_key="success")


@content_analysis_bp.route('/api/analysis/topics/<int:file_id>', methods=['GET'])
def get_topics(file_id: int):
    """Get topic analysis for a single file"""
    try:
        db_hub = DatabaseHub()
        config = AnalysisConfig()
        config.enable_sentiment = False
        config.enable_trends = False
        config.enable_patterns = False
        config.enable_entities = False
        
        engine = ContentAnalysisEngine(db_hub=db_hub, config=config)
        results = engine.analyze_file(file_id)
        
        if 'error' in results:
            return jsonify({
                'success': False,
                'error': results['error']
            }), 400
        
        return jsonify({
            'success': True,
            'data': results.get('topics', {})
        })
        
    except Exception as e:
        logger.error(f"Error getting topics for file {file_id}: {e}",
                     exc_info=True)
        return client_error(e, subsystem="content_analysis",
                            public_message="The topics analysis could not be read",
                            success_key="success")


@content_analysis_bp.route('/api/analysis/entities/<int:file_id>', methods=['GET'])
def get_entities(file_id: int):
    """Get entity analysis for a single file"""
    try:
        db_hub = DatabaseHub()
        config = AnalysisConfig()
        config.enable_sentiment = False
        config.enable_topics = False
        config.enable_trends = False
        config.enable_patterns = False
        
        engine = ContentAnalysisEngine(db_hub=db_hub, config=config)
        results = engine.analyze_file(file_id)
        
        if 'error' in results:
            return jsonify({
                'success': False,
                'error': results['error']
            }), 400
        
        return jsonify({
            'success': True,
            'data': results.get('entities', {})
        })
        
    except Exception as e:
        logger.error(f"Error getting entities for file {file_id}: {e}",
                     exc_info=True)
        return client_error(e, subsystem="content_analysis",
                            public_message="The entities analysis could not be read",
                            success_key="success")


@content_analysis_bp.route('/api/analysis/statistics/<int:file_id>', methods=['GET'])
def get_statistics(file_id: int):
    """Get statistical analysis for a single file"""
    try:
        db_hub = DatabaseHub()
        config = AnalysisConfig()
        config.enable_sentiment = False
        config.enable_topics = False
        config.enable_trends = False
        config.enable_patterns = False
        config.enable_entities = False
        
        engine = ContentAnalysisEngine(db_hub=db_hub, config=config)
        results = engine.analyze_file(file_id)
        
        if 'error' in results:
            return jsonify({
                'success': False,
                'error': results['error']
            }), 400
        
        return jsonify({
            'success': True,
            'data': results.get('statistics', {})
        })
        
    except Exception as e:
        logger.error(f"Error getting statistics for file {file_id}: {e}",
                     exc_info=True)
        return client_error(e, subsystem="content_analysis",
                            public_message="The statistics analysis could not be read",
                            success_key="success")

