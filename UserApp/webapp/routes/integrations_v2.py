"""Integrations routes — v2 passthrough.

All routes proxy to v1 implementations unchanged.
HealthKit and Garmin sync endpoints.
"""
from flask import Blueprint

from .integrations import (
    upload_healthkit,
    get_healthkit_job_status,
    list_healthkit_jobs,
    garmin_connect,
    garmin_status,
    garmin_disconnect,
    garmin_sync,
    get_garmin_job_status,
    list_garmin_jobs,
)

bp = Blueprint('integrations_v2', __name__, url_prefix='/api/v2')

# HealthKit
bp.add_url_rule('/healthkit/upload', 'upload_healthkit', upload_healthkit, methods=['POST'])
bp.add_url_rule('/healthkit/jobs/<job_id>', 'get_healthkit_job_status', get_healthkit_job_status, methods=['GET'])
bp.add_url_rule('/healthkit/jobs', 'list_healthkit_jobs', list_healthkit_jobs, methods=['GET'])

# Garmin
bp.add_url_rule('/garmin/connect', 'garmin_connect', garmin_connect, methods=['POST'])
bp.add_url_rule('/garmin/status', 'garmin_status', garmin_status, methods=['GET'])
bp.add_url_rule('/garmin/disconnect', 'garmin_disconnect', garmin_disconnect, methods=['POST'])
bp.add_url_rule('/garmin/sync', 'garmin_sync', garmin_sync, methods=['POST'])
bp.add_url_rule('/garmin/jobs/<job_id>', 'get_garmin_job_status', get_garmin_job_status, methods=['GET'])
bp.add_url_rule('/garmin/jobs', 'list_garmin_jobs', list_garmin_jobs, methods=['GET'])
