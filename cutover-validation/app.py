
import os
import io
import contextlib
import threading



from flask import (
    Flask,
    render_template,
    request,
    session,
    send_from_directory,
    abort,
)

from werkzeug.utils import secure_filename

from device_validation import (
    run_validation,
    get_sites_for_selection,
    validate_troubleshooting_commands_file,
)

from utils.config import MAX_CONCURRENT_DEVICE_EXECUTIONS
from flask import jsonify

app = Flask(__name__)

progress_data = {
    "total": 0,
    "completed": 0,
    "successful": 0,
    "failed": 0,
    "current_device": "",
    "status": "idle",
}
last_validation_result = None

# Needed for Flask session
app.secret_key = "change-this-to-a-random-secret"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


def save_uploaded_file(file_storage, prefix):
    """
    Save uploaded file and return full path.
    """
    if not file_storage or file_storage.filename == "":
        return None

    filename = secure_filename(file_storage.filename)
    saved_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        f"{prefix}_{filename}",
    )

    file_storage.save(saved_path)

    return saved_path


def get_report_files(report_folder):
    """
    Return report files from a results folder.
    """

    report_files = []

    if not report_folder:
        return report_files

    if not os.path.isdir(report_folder):
        return report_files

    for filename in os.listdir(report_folder):

        full_path = os.path.join(
            report_folder,
            filename,
        )

        if os.path.isfile(full_path):

            report_files.append(
                {
                    "folder": report_folder,
                    "file": filename,
                }
            )

    return report_files

def validation_worker(
    credentials_path,
    commands_path,
    selected_sites,
    max_workers
):

    global last_validation_result

    buffer = io.StringIO()

    try:

        with contextlib.redirect_stdout(buffer):

            validation_result = run_validation(
                credentials_file=credentials_path,
                troubleshooting_commands_file=commands_path,
                devices_file=None,
                selected_site_ids=selected_sites,
                max_workers=max_workers,
                skip_confirmation=True,
                progress_data=progress_data,
            )

        report_folder = None

        if isinstance(validation_result, dict):
            report_folder = validation_result.get(
                "report_folder"
            )

        last_validation_result = {
            "output": buffer.getvalue(),
            "error": None,
            "report_folder": report_folder,
            "report_files": get_report_files(
                report_folder
            ),
        }


    except Exception as e:

        last_validation_result = {
            "output": buffer.getvalue(),
            "error": str(e),
            "report_folder": None,
            "report_files": [],
        }


    progress_data["status"] = "completed"
    
@app.route("/", methods=["GET", "POST"])
def index():
    output = None
    error = None
    report_folder = None
    report_files = []

    if request.method == "POST":
        action = request.form.get("action")

        credentials_file = request.files.get("credentials")
        commands_file = request.files.get("commands")
        devices_file = request.files.get("devices")

        max_workers_raw = request.form.get("max_workers")

        try:
            max_workers = int(max_workers_raw) if max_workers_raw else MAX_CONCURRENT_DEVICE_EXECUTIONS
        except ValueError:
            max_workers = MAX_CONCURRENT_DEVICE_EXECUTIONS

        credentials_path = save_uploaded_file(credentials_file, "credentials")
        commands_path = save_uploaded_file(commands_file, "commands")
        devices_path = save_uploaded_file(devices_file, "devices")

        if not credentials_path:
            error = "Credentials file is required."
        elif not commands_path:
            error = "Troubleshooting commands YAML file is required."
        else:
            # Save these paths for the next step
            session["credentials_path"] = credentials_path
            session["commands_path"] = commands_path
            session["max_workers"] = max_workers
                      
            #
            # Device file ALWAYS takes precedence if supplied.
            #
            if devices_path:

                buffer = io.StringIO()

                try:

                    with contextlib.redirect_stdout(buffer):
                        validation_result = run_validation(
                            credentials_file=credentials_path,
                            troubleshooting_commands_file=commands_path,
                            devices_file=devices_path,
                            selected_site_ids=None,
                            max_workers=max_workers,
                            skip_confirmation=True,
                            progress_data=progress_data,
                        )

                    output = buffer.getvalue()

                    report_folder = None

                    if isinstance(validation_result, dict):
                        report_folder = validation_result.get(
                            "report_folder"
                        )

                    report_files = get_report_files(
                        report_folder
                    )

                    return render_template(
                        "results.html",
                        output=output,
                        error=None,
                        report_folder=report_folder,
                        report_files=report_files,
                    )

                except Exception as e:
                    error = str(e)
                    output = buffer.getvalue()

            #
            # No device file supplied → Site Selection workflow
            #
            elif action == "load_sites":

                buffer = io.StringIO()

                try:

                    with contextlib.redirect_stdout(buffer):
                        validate_troubleshooting_commands_file(commands_path)
                        sites_data = get_sites_for_selection(
                            credentials_path
                        )

                    return render_template(
                        "select_sites.html",
                        sites=sites_data,
                        log_output=buffer.getvalue(),
                    )

                except Exception as e:
                    error = str(e)
                    output = buffer.getvalue()
            #
            # User clicked "Run with Device File"
            # but forgot to upload one
            #
            elif action == "run_with_device_file":

                error = (
                    "Please upload a device YAML/CSV file "
                    "or choose 'Load Sites for Selection'."
                )

            
    return render_template(
        "index.html",
        output=output,
        error=error,
        default_workers=MAX_CONCURRENT_DEVICE_EXECUTIONS,
    )


@app.route("/run_site_validation", methods=["POST"])
def run_site_validation():

    selected_sites = request.form.getlist("selected_sites")

    credentials_path = session.get("credentials_path")
    commands_path = session.get("commands_path")
    max_workers = session.get(
        "max_workers",
        MAX_CONCURRENT_DEVICE_EXECUTIONS
    )

    if not credentials_path:
        return "Missing credentials path"

    if not commands_path:
        return "Missing commands path"

    if not selected_sites:
        return "No sites selected"

    progress_data["total"] = 0
    progress_data["completed"] = 0
    progress_data["successful"] = 0
    progress_data["failed"] = 0
    progress_data["current_device"] = ""
    progress_data["status"] = "starting"

    threading.Thread(
        target=validation_worker,
        args=(
            credentials_path,
            commands_path,
            selected_sites,
            max_workers,
        ),
        daemon=True,
    ).start()

    return render_template("progress.html")


@app.route("/download/<path:folder>/<path:filename>")
def download_file(folder, filename):

    if not folder.startswith("results_"):
        abort(404)

    if not os.path.isdir(folder):
        abort(404)

    file_path = os.path.join(folder, filename)

    if not os.path.isfile(file_path):
        abort(404)

    # Open HTML in browser
    if filename.endswith(".html"):
        return send_from_directory(
            folder,
            filename,
            as_attachment=False,
        )

    # Download everything else
    return send_from_directory(
        folder,
        filename,
        as_attachment=True,
    )

@app.route("/validation_results")
def validation_results():

    global last_validation_result

    if not last_validation_result:

        return "No validation results available."

    return render_template(
        "results.html",
        output=last_validation_result["output"],
        error=last_validation_result["error"],
        report_folder=last_validation_result["report_folder"],
        report_files=last_validation_result["report_files"],
    )

@app.route("/progress")
def progress():

    return jsonify(progress_data)

if __name__ == "__main__":
    app.run(debug=True)

