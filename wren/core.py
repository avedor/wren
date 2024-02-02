import os
import shutil
from pathvalidate import sanitize_filename
import requests
import json
from datetime import datetime
from dateutil import parser
from platformdirs import user_data_dir, user_config_dir
from croniter import croniter
from todoist_api_python.api import TodoistAPI

__version__ = "0.2.1"

# Load config and set up folders

data_dir = user_data_dir("wren", "wren")
config_dir = user_config_dir("wren", "wren")
messages_log = os.path.join(data_dir, "messages.json")

config = {
    "backend": "todoist", # uncomment to override the default backend
    "notes_dir": "~/Notes",
    "done_dir": "~/Notes/done",
    "http_user": "",
    "http_password": "",
    "openai_token": "",
    "telegram_token": "",
    "todoist_token": "",
    "allowed_telegram_chats": [],
    "about_user": "The user chose to specify nothing.",
    "homeserver": "",
    "matrix_localpart": "",
    "matrix_password": "",
}
config_file = os.path.join(config_dir, "wren.json")

try:
    with open(config_file, "r") as file:
        user_config = json.load(file)
except FileNotFoundError:
    user_config = {}
config = {**config, **user_config}


def parse_path(p, base=""):
    return os.path.join(base, os.path.expanduser(p))

now = datetime.now()

def mkdir(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path)

mkdir(data_dir)


# Configure Backend
if "backend" in config:
    if parse_path(config["backend"]) == "todoist":
        if config["todoist_token"]:
            api = TodoistAPI(parse_path(config["todoist_token"]))
        else:
            print("Todoist backend requires Todoist API Token")
            raise SystemExit
        notes_dir = parse_path(config["notes_dir"])
else:
    notes_dir = parse_path(config["notes_dir"])
    done_dir = parse_path(config["done_dir"], notes_dir)
    mkdir(done_dir)

mkdir(notes_dir)

# Common API
def create_new_task(content: str) -> str:
    taskname = sanitize_filename(content.split("\n")[0].replace("*", "＊"))
    content = "\n".join(content.split("\n")[1:])
    if "backend" in config:
        if config["backend"] == "todoist":
            project_id = ""
            project_mapping = {}
            section_mapping = {}
            # Find all projects
            try:
                projects = api.get_projects()
                for index, project in enumerate(projects, start=1):
                    print(f"{index}: {project.name}")
                    project_mapping[index] = {"id": project.id, "name": project.name}
            except Exception as error:
                print(error)
            # Prompt user to select a project
            project_select = input("Select a project: ")
            selected_project = project_mapping.get(int(project_select))
            if selected_project:
                print(f"Selected project: {selected_project['name']}")
                project_id = selected_project['id']
                # If the project has sections, prompt for a section
                try:
                    sections = api.get_sections(project_id=project_id)
                    for index, section in enumerate(sections, start=1):
                        print(f"{index}: {section.name}")
                        section_mapping[index] = {"id": section.id, "name": section.name}
                except Exception as error:
                    print(error)
                section_select = input("Select a section: ")
                selected_section = section_mapping.get(int(section_select))
                print(f"Selected section: {selected_section['name']}")
                section_id = selected_section['id']
            else:
                print("Invalid selection. Please select a valid project.")
            # Add the task
            try:
                task = api.add_task(
                    project_id=project_id,
                    section_id=section_id,
                    content=taskname,
                    description=content
                )
            except Exception as error:
                print(error)
    else:
        with open(os.path.join(notes_dir, taskname), "w") as file:
            file.write(content)
    return taskname


def get_tasks(query="") -> list[str]:
    global now
    now = datetime.now()
    if "backend" in config:
        if config["backend"] == "todoist":
            try:
                tasks = api.get_tasks()
                return tasks
            except Exception as error:
                print(error)
    else:
        return [
            format_task_name(file)
            for file in sorted(
                os.listdir(notes_dir),
                key=lambda x: os.path.getctime(os.path.join(notes_dir, x)),
                reverse=True,
            )
            if os.path.isfile(os.path.join(notes_dir, file))
            and not file.startswith(".")
            and query in file
            and is_present_task(file)
        ]


def get_summary() -> str:
    if not config["openai_token"]:
        return "Please specify your OpenAI token in the Wren config file"
    url = "https://api.openai.com/v1/chat/completions"
    current_time = datetime.now().isoformat()
    tasks = get_tasks()
    current_message = {"role": "user", "content": f"{current_time}\n{tasks}"}

    try:
        with open(messages_log, "r") as file:
            existing_data = json.load(file)
    except FileNotFoundError:
        existing_data = []

    payload = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that helps the user be on top of their schedule and tasks. every once in a while, the user is going to send you the current time and a list of currently pending tasks. your role is to tell the user in a simple language what they need to do today. IF AND ONLY IF a task has been ongoing for a long time, let the user know about it. IF AND ONLY IF you see a task that appeared earlier in the chat but doesn't appear anymore, add a small congratulation to acknowledge the fact that the task was completed. the user will send each task in a new line starting with a dash. words starting with a plus sign are tags related to task. when writing back to the user, try to mention tasks that share the same tags or concept together and be concise. The user added the following context: "
                + config["about_user"],
            },
        ]
        + existing_data
        + [current_message],
    }
    headers = {
        "content-type": "application/json",
        "Authorization": "Bearer " + config["openai_token"],
    }

    response = requests.post(url, json=payload, headers=headers)
    data = response.json()
    response = data["choices"][0]["message"]["content"]

    existing_data.extend([current_message, data["choices"][0]["message"]])

    with open(messages_log, "w") as file:
        json.dump(existing_data, file, indent=2)

    return response


def get_task_file(name: str) -> tuple[bool, str]:
    if "backend" in config:
        if config["backend"] == "todoist":
            matching_tasks = [
                task
                for task in api.get_tasks()
                if name.lower() in task.content.lower()
            ]
            if len(matching_tasks) == 1:
                return (True, matching_tasks[0])
            elif len(matching_tasks) > 1:
                return (False, "Error: Multiple matching tasks found.")
            else:
                return (False, f"Error: No matching task for '{name}' found.")
    else:
        matching_files = [
            file
            for file in os.listdir(notes_dir)
            if name.lower() in file.lower()
            and os.path.isfile(os.path.join(notes_dir, file))
        ]
        if len(matching_files) == 1:
            return (True, matching_files[0])
        elif len(matching_files) > 1:
            return (False, "Error: Multiple matching files found.")
        else:
            return (False, f"Error: No matching file for '{name}' found.")


def mark_task_done(name: str) -> str:
    found, taskname = get_task_file(name)
    if "backend" in config:
        if config["backend"] == "todoist":
            if found:
                api.close_task(task_id=taskname.id)
                response = f'marked "{taskname.content}" as done'
            else:
                response = taskname.content
            return response
    else:
        if found:
            if is_cron_task(taskname):
                shutil.copy(
                    os.path.join(notes_dir, taskname), os.path.join(done_dir, taskname)
                )
            else:
                shutil.move(
                    os.path.join(notes_dir, taskname), os.path.join(done_dir, taskname)
                )
            response = f'marked "{taskname}" as done'
        else:
            response = taskname
        return response


def get_task_content(name: str) -> str:
    if "backend" in config:
        if config["backend"] == "todoist":
            found, taskname = get_task_file(name)
            if found:
                task_id = taskname
                task = api.get_task(task_id=task_id)
                return task.description
    else:
        found, filename = get_task_file(name)
        if found:
            file_to_read = os.path.join(notes_dir, filename)
            with open(file_to_read, "r") as file:
                file_content = file.read()
                response = f"{filename}\n\n{file_content}"
        else:
            response = filename
        return response


# Helper functions

def is_present_task(file: str) -> bool:
    if not file[0].isdigit():
        return True
    if is_cron_task(file):
        cron = " ".join(file.replace("＊", "*").split(" ")[:5])
        last_task = None
        path = os.path.join(done_dir, file)
        if os.path.exists(path):
            last_modified_date = datetime.fromtimestamp(os.path.getmtime(path))
            if last_task is None or last_modified_date > last_task:
                last_task = last_modified_date
            if not last_task or croniter(cron, last_task).get_next(datetime) <= now:
                return True
        else:
            return True
    elif is_dated_task(file):
        time = file.split(" ")[0]
        task_time = parser.parse(time)
        if task_time <= now:
            return True
    return False


def format_task_name(taskname: str) -> str:
    if is_cron_task(taskname):
        return " ".join(taskname.split()[5:])
    if is_dated_task(taskname):
        return " ".join(taskname.split()[1:])
    return taskname


def is_dated_task(taskname: str) -> bool:
    try:
        parser.parse(taskname.split()[0])
        return True
    except:
        return False


def is_cron_task(taskname: str) -> bool:
    splitted = taskname.split()
    if len(splitted) < 6 or not all(
        (s.isdigit() or s in ["＊", "*"]) for s in splitted[:3]
    ):
        return False
    return True
