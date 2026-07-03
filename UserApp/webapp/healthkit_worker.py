#!/usr/bin/env python3
"""
HealthKit Background Worker

Handles asynchronous processing of HealthKit import jobs.
Uses threading for simple in-process background processing — Home Edition
runs all background work in-process (no broker).
"""


import threading
import shutil
import logging
from pathlib import Path
import traceback


def setup_job_logger(job_id: str):
    """
    Set up a logger that writes to /app/logs/import_{job_id}.log
    
    Args:
        job_id: Job identifier
        
    Returns:
        Logger instance configured for this job
    """
    log_dir = Path('/app/logs')
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / f'import_{job_id}.log'
    
    # Create logger
    logger = logging.getLogger(f'healthkit.{job_id}')
    logger.setLevel(logging.INFO)
    
    # Remove any existing handlers
    logger.handlers.clear()
    
    # Create file handler
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    fh.setFormatter(formatter)
    
    logger.addHandler(fh)
    
    return logger


def process_healthkit_job(user_id: str, job_id: str, export_path: Path,
                          tenant_id: int = 1):
    """
    Process a HealthKit import job in background.

    This function runs in a separate thread and:
    1. Updates job status to 'processing'
    2. Runs the import
    3. Updates job status to 'completed' or 'failed'
    4. Cleans up temp files

    Args:
        user_id: UUID of user
        job_id: Job identifier
        export_path: Path to extracted apple_health_export directory
        tenant_id: Tenant ID for multi-tenant isolation
    """
    from db_manager import get_direct_connection_for_user
    from healthkit_importer import import_healthkit_export
    
    # Set up logging for this job
    logger = setup_job_logger(job_id)

    conn = None

    try:
        # Get database connection using user_id directly
        logger.info(f"Getting database connection for user_id: {user_id}")
        conn = get_direct_connection_for_user(user_id)
        cur = conn.cursor()

        # Update status to processing
        logger.info("Updating job status to 'processing'")
        cur.execute("""
            UPDATE healthkit_import_jobs
            SET status = 'processing', started_at = now()
            WHERE id = %s::uuid AND user_id = %s
        """, (job_id, user_id))
        conn.commit()
        cur.close()
        
        logger.info(f"Starting HealthKit import for user_id {user_id}")
        
        # Run import (this may take several minutes)
        counts = import_healthkit_export(user_id, export_path, conn=conn, job_id=job_id,
                                        tenant_id=tenant_id)
        
        total = sum(counts.values())
        logger.info("Import completed successfully")
        logger.info(f"  Records: {counts['records']}")
        logger.info(f"  Activity summaries: {counts['activity_summaries']}")
        logger.info(f"  Workouts: {counts['workouts']}")
        logger.info(f"  Clinical records: {counts['clinical_records']}")
        logger.info(f"  Lab results: {counts['lab_results']}")
        logger.info(f"  Total: {total}")

        # Update status to completed
        logger.info("Updating job status to 'completed'")
        cur = conn.cursor()
        cur.execute("""
            UPDATE healthkit_import_jobs
            SET status = 'completed',
                completed_at = now(),
                total_records = %s,
                processed_records = %s
            WHERE id = %s::uuid AND user_id = %s
        """, (total, total, job_id, user_id))
        conn.commit()
        cur.close()
        
    except Exception as e:
        error_msg = str(e)
        error_trace = traceback.format_exc()
        
        logger.error(f"Import failed: {error_msg}")
        logger.error(f"Traceback:\n{error_trace}")
        
        # Update status to failed
        try:
            if conn is None:
                conn = get_direct_connection_for_user(user_id)

            if conn is None:
                logger.error("Cannot update job status - no database connection")
                return

            cur = conn.cursor()
            cur.execute("""
                UPDATE healthkit_import_jobs
                SET status = 'failed',
                    completed_at = now(),
                    error_message = %s
                WHERE id = %s::uuid AND user_id = %s
            """, (error_msg, job_id, user_id))
            conn.commit()
            cur.close()
        except Exception as update_error:
            logger.error(f"Failed to update error status: {update_error}")
    
    finally:
        # Close connection
        if conn:
            conn.close()
        
        # Clean up temp files
        try:
            if export_path.exists():
                # Delete the entire upload directory (parent of export_path)
                upload_dir = export_path.parent.parent
                if upload_dir.exists() and 'healthkit-uploads' in str(upload_dir):
                    logger.info(f"Cleaning up temp files: {upload_dir}")
                    shutil.rmtree(upload_dir)
        except Exception as cleanup_error:
            logger.error(f"Cleanup failed: {cleanup_error}")


def queue_healthkit_import(user_id: str, job_id: str, export_path: Path,
                           tenant_id: int = 1):
    """
    Queue a HealthKit import job for background processing.

    Starts a daemon thread that will process the import asynchronously.
    The thread will automatically clean up when the import completes.

    Args:
        user_id: UUID of user
        job_id: Job identifier
        export_path: Path to extracted apple_health_export directory
        tenant_id: Tenant ID for multi-tenant isolation
    """
    # Set up logger for this job
    logger = setup_job_logger(job_id)
    logger.info("Queuing HealthKit import job")

    # Start background thread
    thread = threading.Thread(
        target=process_healthkit_job,
        args=(user_id, job_id, export_path, tenant_id),
        daemon=True,
        name=f"healthkit-import-{job_id}"
    )
    thread.start()

    logger.info("Background thread started")
