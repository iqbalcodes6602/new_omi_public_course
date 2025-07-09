import os
import argparse
import logging
import zipfile
from getpass import getpass
from typing import Dict, Any
import requests
from tqdm import tqdm
from urllib.parse import urljoin
import json

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)

USERNAME = None
PASSWORD = None
BASE_URL = None
COOKIES = None

# 👇 Add your course aliases here
COURSE_ALIASES = [
    "omi-public-course"
]

BASE_COURSE_FOLDER = "Courses"


def handle_input():
    global USERNAME, PASSWORD, BASE_URL
    parser = argparse.ArgumentParser(description="Download and extract problems from multiple course assignments")
    parser.add_argument("--username", help="omegaUp username")
    parser.add_argument("--password", help="omegaUp password")
    parser.add_argument("--url", default="https://omegaup.com", help="omegaUp base URL")
    args = parser.parse_args()
    USERNAME = args.username or input("Enter your username: ")
    PASSWORD = args.password or getpass("Enter your password: ")
    BASE_URL = args.url


def login() -> Dict[str, Any]:
    global COOKIES
    login_url = urljoin(BASE_URL, f"/api/user/login?usernameOrEmail={USERNAME}&password={PASSWORD}")
    response = requests.post(login_url)
    response.raise_for_status()
    json_data = response.json()
    if json_data.get("status") != "ok":
        raise Exception(f"Login failed: {json_data}")
    COOKIES = response.cookies.get_dict()
    return COOKIES


def get_json(endpoint: str, params: Dict[str, str]) -> Dict[str, Any]:
    url = urljoin(BASE_URL, endpoint)
    response = requests.get(url, params=params, cookies=COOKIES)
    response.raise_for_status()
    return response.json()


def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_").strip()


def get_course_details(course_alias: str, course_base_folder: str) -> Dict[str, Any]:
    details = get_json("/api/course/details/", {"alias": course_alias})
    course_folder = os.path.join(course_base_folder, course_alias)
    os.makedirs(course_folder, exist_ok=True)

    # Save course_settings.json
    course_settings_path = os.path.join(course_folder, "course_settings.json")
    with open(course_settings_path, "w", encoding="utf-8") as f:
        json.dump(details, f, indent=2, ensure_ascii=False)

    return details


def get_assignments(course_alias: str):
    return get_json("/api/course/listAssignments/", {"course_alias": course_alias})["assignments"]


def get_assignment_details(course_alias: str, assignment_alias: str):
    return get_json("/api/course/assignmentDetails/", {
        "course": course_alias,
        "assignment": assignment_alias
    })


def download_and_unzip(problem_alias: str, assignment_folder: str):
    try:
        download_url = urljoin(BASE_URL, f"/api/problem/download/problem_alias/{problem_alias}/")
        response = requests.get(download_url, cookies=COOKIES, stream=True)

        if response.status_code == 404:
            LOG.warning(f"⚠️  Problem '{problem_alias}' not found or access denied (404).")
            return

        response.raise_for_status()

        problem_folder = os.path.join(assignment_folder, sanitize_filename(problem_alias))
        os.makedirs(problem_folder, exist_ok=True)

        zip_path = os.path.join(problem_folder, f"{problem_alias}.zip")
        with open(zip_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(problem_folder)
            os.remove(zip_path)
            LOG.info(f"✅ Extracted: {problem_alias} → {problem_folder}")
        except zipfile.BadZipFile:
            LOG.error(f"❌ Failed to unzip: {zip_path}")
            return

        settings_path = os.path.join(problem_folder, "settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r+", encoding="utf-8") as f:
                    settings = json.load(f)
                    settings["alias"] = problem_alias
                    settings["title"] = problem_alias
                    f.seek(0)
                    json.dump(settings, f, indent=2, ensure_ascii=False)
                    f.truncate()
                LOG.info(f"🛠️  Updated settings.json with alias: {problem_alias}")
            except Exception as e:
                LOG.warning(f"⚠️  Failed to update settings.json for '{problem_alias}': {e}")
        else:
            LOG.warning(f"⚠️  No settings.json found for '{problem_alias}'")

    except requests.exceptions.RequestException as e:
        LOG.error(f"❌ Failed to download '{problem_alias}': {e}")


def main():
    handle_input()
    login()

    os.makedirs(BASE_COURSE_FOLDER, exist_ok=True)
    all_problems = []

    for course_alias in COURSE_ALIASES:
        LOG.info(f"📘 Starting course: {course_alias}")
        try:
            course_details = get_course_details(course_alias, BASE_COURSE_FOLDER)
            assignments = get_assignments(course_alias)

            if not assignments:
                LOG.warning(f"No assignments found in {course_alias}.")
                continue

            course_folder = os.path.join(BASE_COURSE_FOLDER, course_alias)

            for assignment in tqdm(assignments, desc=f"Assignments in {course_alias}"):
                assignment_alias = assignment["alias"]
                assignment_name = assignment["name"]
                LOG.info(f"📂 Processing assignment: {assignment_name} ({assignment_alias})")

                try:
                    details = get_assignment_details(course_alias, assignment_alias)
                    assignment_folder = os.path.join(course_folder, assignment_alias)  # 👈 use alias
                    os.makedirs(assignment_folder, exist_ok=True)

                    assignment_settings_path = os.path.join(assignment_folder, "assignment_settings.json")
                    with open(assignment_settings_path, "w", encoding="utf-8") as f:
                        json.dump(details, f, indent=2, ensure_ascii=False)

                    problems = details.get("problems", [])

                    for problem in tqdm(problems, desc=f"  ↳ {assignment_alias}", leave=False):
                        try:
                            download_and_unzip(problem["alias"], assignment_folder)
                            rel_path = os.path.join(BASE_COURSE_FOLDER, course_alias, assignment_alias, sanitize_filename(problem["alias"]))
                            all_problems.append({"path": rel_path})
                        except Exception as e:
                            LOG.error(f"❌ Error while processing problem '{problem['alias']}': {e}")

                except Exception as e:
                    LOG.error(f"❌ Failed to process assignment '{assignment_alias}': {e}")


        except Exception as e:
            LOG.error(f"❌ Failed to process course '{course_alias}': {e}")

    # ✅ Write problems.json
    with open("problems.json", "w", encoding="utf-8") as f:
        json.dump({"problems": all_problems}, f, indent=2, ensure_ascii=False)
    LOG.info("📝 Created problems.json with all problem paths.")


if __name__ == "__main__":
    main()
