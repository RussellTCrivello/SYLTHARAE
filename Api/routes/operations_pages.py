"""Page routes for the unified Operations UI (Input, Import, Jobs)."""
import logging

from flask import render_template

logger = logging.getLogger(__name__)


def register_operations_pages(app):
    """Register the Operations Center pages (authenticated by middleware)."""

    @app.route("/operations/input")
    def operations_input_page():
        return render_template("Operations/input.html")

    @app.route("/operations/import")
    def operations_import_page():
        return render_template("Operations/import_center.html")

    @app.route("/operations/jobs")
    def operations_jobs_page():
        return render_template("Operations/jobs.html")

    @app.route("/operations/jobs/<job_id>")
    def operations_job_detail_page(job_id):
        return render_template("Operations/job_detail.html", job_id=job_id)
