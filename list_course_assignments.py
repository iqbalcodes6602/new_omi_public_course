import os
import argparse
import logging
import zipfile
from getpass import getpass
from typing import Dict, Any
import requests
from tqdm import tqdm
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)

USERNAME = None
PASSWORD = None
COURSE_ALIAS = None
BASE_URL = None
COOKIES = None
COURSE_NAME = None


def handle_input():
    global USERNAME, PASSWORD, COURSE_ALIAS, BASE_URL
    parser = argparse.ArgumentParser(description="Download and extract problems from course assignments")
    parser.add_argument("--username", help="omegaUp username")
    parser.add_argument("--password", help="omegaUp password")
    parser.add_argument("--course", help="Course alias")
    parser.add_argument("--url", default="https://omegaup.com", help="omegaUp base URL")
    args = parser.parse_args()
    USERNAME = args.username or input("Enter your username: ")
    PASSWORD = args.password or getpass("Enter your password: ")
    COURSE_ALIAS = args.course or input("Enter course alias: ")
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


def get_course_details(course_alias: str) -> Dict[str, Any]:
    global COURSE_NAME
    details = get_json("/api/course/details/", {"alias": course_alias})
    COURSE_NAME = sanitize_filename(details["name"])
    return details


def get_assignments(course_alias: str):
    return get_json("/api/course/listAssignments/", {"course_alias": course_alias})["assignments"]


def get_assignment_details(course_alias: str, assignment_alias: str):
    return get_json("/api/course/assignmentDetails/", {
        "course": course_alias,
        "assignment": assignment_alias
    })


def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_").strip()



def download_and_unzip(problem_alias: str, assignment_name: str):
    try:
        download_url = urljoin(BASE_URL, f"/api/problem/download/problem_alias/{problem_alias}/")
        response = requests.get(download_url, cookies=COOKIES, stream=True)

        if response.status_code == 404:
            LOG.warning(f"⚠️  Problem '{problem_alias}' not found or access denied (404).")
            return

        response.raise_for_status()

        assignment_dir = os.path.join(COURSE_NAME, sanitize_filename(assignment_name))
        problem_dir = os.path.join(assignment_dir, sanitize_filename(problem_alias))
        os.makedirs(problem_dir, exist_ok=True)

        zip_path = os.path.join(problem_dir, f"{problem_alias}.zip")
        with open(zip_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(problem_dir)
            os.remove(zip_path)
            LOG.info(f"✅ Extracted: {problem_alias} → {problem_dir}")
        except zipfile.BadZipFile:
            LOG.error(f"❌ Failed to unzip: {zip_path}")

    except requests.exceptions.RequestException as e:
        LOG.error(f"❌ Failed to download '{problem_alias}': {e}")



def main():
    handle_input()
    login()
    get_course_details(COURSE_ALIAS)

    LOG.info(f"📘 Fetching assignments for course: {COURSE_ALIAS}")
    assignments = get_assignments(COURSE_ALIAS)

    if not assignments:
        LOG.warning("No assignments found.")
        return

    for assignment in tqdm(assignments, desc="Assignments"):
        assignment_alias = assignment["alias"]
        assignment_name = assignment["name"]
        LOG.info(f"📂 Processing assignment: {assignment_name} ({assignment_alias})")

        details = get_assignment_details(COURSE_ALIAS, assignment_alias)
        problems = details.get("problems", [])

        for problem in tqdm(problems, desc=f"  ↳ {assignment_name}", leave=False):
            try:
                download_and_unzip(problem["alias"], assignment_name)
            except Exception as e:
                LOG.error(f"❌ Unexpected error while processing '{problem['alias']}': {e}")



if __name__ == "__main__":
    main()
