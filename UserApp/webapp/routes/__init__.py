"""
Route Blueprints Registration

This module registers all API blueprints with the Flask app.
Auth routes remain in app.py due to template rendering and session complexity.

v1 blueprints: original API (no embedding in CRUD routes)
v2 blueprints: embedding-aware API (accepts optional vectors on create/update)
"""


def register_blueprints(app):
    """Register all API blueprints with the Flask application."""

    # v1 blueprints (unchanged)
    from routes import health_inputs
    from routes import food
    from routes import logging_routes
    from routes import vitals
    from routes import analytics
    from routes import integrations
    from routes import feedback
    from routes import embeddings
    from routes import clinical_history
    from routes import documents
    from routes import folders
    from routes import dietary_settings
    from routes import diet_catalog
    from routes import reminders
    from routes import correlation_report
    from routes import provider_contacts
    from routes import appointments
    from routes import projected_reminders
    from routes import metrics
    from routes import search
    from routes import healthz

    app.register_blueprint(health_inputs.bp)
    app.register_blueprint(food.bp)
    app.register_blueprint(logging_routes.bp)
    app.register_blueprint(vitals.bp)
    app.register_blueprint(analytics.bp)
    app.register_blueprint(integrations.bp)
    app.register_blueprint(feedback.bp)
    app.register_blueprint(embeddings.bp)
    app.register_blueprint(clinical_history.bp)
    app.register_blueprint(documents.bp)
    app.register_blueprint(folders.bp)
    app.register_blueprint(dietary_settings.bp)
    app.register_blueprint(diet_catalog.bp)
    app.register_blueprint(reminders.bp)
    app.register_blueprint(correlation_report.bp)
    app.register_blueprint(provider_contacts.bp)
    app.register_blueprint(appointments.bp)
    app.register_blueprint(projected_reminders.bp)
    app.register_blueprint(metrics.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(healthz.bp)

    # v2 blueprints (embedding-aware or passthrough)
    from routes import health_inputs_v2
    from routes import food_v2
    from routes import vitals_v2
    from routes import logging_routes_v2
    from routes import analytics_v2
    from routes import integrations_v2
    from routes import feedback_v2
    from routes import embeddings_v2
    from routes import mobile_events_v2

    app.register_blueprint(health_inputs_v2.bp)
    app.register_blueprint(food_v2.bp)
    app.register_blueprint(vitals_v2.bp)
    app.register_blueprint(logging_routes_v2.bp)
    app.register_blueprint(analytics_v2.bp)
    app.register_blueprint(integrations_v2.bp)
    app.register_blueprint(feedback_v2.bp)
    app.register_blueprint(embeddings_v2.bp)
    app.register_blueprint(mobile_events_v2.bp)

    app.logger.info("Registered %d route blueprints (v1 + v2 + appointments + projected-reminders + metrics + diet-catalog + healthz)", 30)
