from flask import Flask, request
import functions_framework
import logging
logging.basicConfig(level=logging.INFO)
from google.cloud import pubsub_v1
import os
import gcsfs
import json
import requests
import openai
import datetime

PROJECT_ID = os.environ.get('PROJECT_ID')
EVENT_BUS = os.environ.get('EVENT_BUS')
SPODKAST_ROUTE = "gs://yggdrasil-ai-hermod-spodkast/{owner}/{id}"
ENTITY = "spodkast"

APP = Flask("internal")

def publish_message(author:str, operation:str, entity_id:str, payload:str):
    """
    This function publishes the message.
    Parameters:
        conversation_id: The ID of the conversation
        author: Who is publishing the message (user_id)
        message: The message to publish
    """
    message = {
        "author": author,
        "entity": ENTITY,
        "entityId": entity_id,
        "operation": operation,
        "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "payload": payload
    }
    message_json = json.dumps(message).encode("utf-8")
    print("Publishing ", message_json)
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, EVENT_BUS)
    publish_future = publisher.publish(topic_path,
                                        data=message_json)
    publish_future.result()

def read_file(file):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    with fs.open(file, 'r') as f:
        content = f.read()
    return content

def write_to_file(file, content):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    with fs.open(file, "w") as file_:
        file_.write(content)

def download_file(url, destiny_file):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with fs.open(destiny_file, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return destiny_file
    
def parse_sections(text):
    # Split the text into sections
    sections = text.split("#section")[1:]  # The first item is empty, so we skip it

    # Remove leading and trailing whitespace from each section
    sections = [section.strip() for section in sections]

    return sections

@APP.route('/', methods=['GET', 'POST'])
def unknown_operation():
    response = APP.response_class(
        response="Incomplete path, please select an operation",
        status=400,
        mimetype='text/plain')
    return response

@APP.route('/extend', methods=['POST', ])
def extend_sections():
    logging.info("Received request to extend sections: {}".format(request))
    request_json = json.loads(request.data)
    author = request_json["author"]
    if request_json["author"] == "#spokeAgent#":
        author = request_json["conversationId"].split(".")[0]
    user = request_json["user"] if request_json["user"]!="undefined" else author
    name = request_json["name"]
    
    publish_message(author=author, operation="extend", entity_id=name, payload=json.dumps(request_json))

    response = APP.response_class(
        response=json.dumps({"payload":{
            "user": user,
            "id": name
            },
            "responseMessage": f"Generating sections"}),
        status=200,
        mimetype='text/plain')
    return response

@APP.route('/produce', methods=['POST', ])
def produce_spodkast():
    logging.info("Received request to produce podcast: {}".format(request))
    request_json = json.loads(request.data)
    author = request_json["author"]
    if request_json["author"] == "#spokeAgent#":
        author = request_json["conversationId"].split(".")[0]
    user = request_json["user"] if request_json["user"]!="undefined" else author
    name = request_json["name"]
    
    # Generate podcast
    publish_message(author=author, operation="produce", entity_id=name, payload=json.dumps(request_json))

    response = APP.response_class(
        response=json.dumps({"payload":{
            "user": user,
            "id": name
            },
            "responseMessage": f"Producing podcast"}),
        status=200,
        mimetype='text/plain')
    return response

@APP.route('/create', methods=['POST', ])
def create_spodkast():
    logging.info("Received request: {}".format(request))
    request_json = json.loads(request.data)
    author = request_json["author"]
    if request_json["author"] == "#spokeAgent#":
        author = request_json["conversationId"].split(".")[0]
    user = request_json["user"] if request_json["user"]!="undefined" else author
    name = request_json["name"]
    requirements = request_json["requirements"]
    files = request_json["inputFiles"].split(',')

    # Ensure workspace setup
    assigned_folder = SPODKAST_ROUTE.format(owner=user, id=name)
    logging.info(f"Ensuring {assigned_folder} setup")
    if requirements == "undefined":
        logging.info("Reading requirements")
        requirements = read_file(f"{assigned_folder}/requirements.txt")
    else:
        logging.info("Writing requirements")
        write_to_file(f"{assigned_folder}/requirements.txt", requirements)
    owned_files = []
    if len(files) > 0 and files[0]!="undefined":
        for file in [x.strip() for x in files]:
            filename = file.split('/')[-1]
            logging.info(f"Downloading {filename}")
            saved_file = download_file(file, f'{assigned_folder}/input_files/{filename}')
            owned_files += [saved_file]
    else:
        logging.error("No input files")
        response = APP.response_class(
            response=json.dumps({"payload": {
                "error": "No input files"
            },
            "responseMessage": "ERROR: No input files"}),
            status=200,
            mimetype='text/plain'
        )
        return response

    publish_message(author=author, operation="create", entity_id=name, payload=json.dumps(request_json))

    response = APP.response_class(
        response=json.dumps({"payload":{
            "workspace": assigned_folder,
            "id": name
        },
        "responseMessage": f"Creation of {name} started in {assigned_folder}"}),
        status=200,
        mimetype='text/plain')
    return response
    
    
@functions_framework.http
def actions_spodkast(request):
    internal_ctx = APP.test_request_context(path=request.full_path,
                                            method=request.method)
    internal_ctx.request.data = request.data
    internal_ctx.request.headers = request.headers
    internal_ctx.request.args = request.args
    
    APP.config['PRESERVE_CONTEXT_ON_EXCEPTION']=False
    
    return_value = APP.response_class(
        response="Invalid Request", 
        status=400,
        mimetype='text/plain')
    
    try:
        internal_ctx.push()
        return_value = APP.full_dispatch_request()
        logging.info("Request processed: {}".format(return_value))
        internal_ctx.pop()
    except Exception as e:
        logging.error(e)
    return return_value