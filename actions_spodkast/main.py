from flask import Flask, request
import functions_framework
import logging
logging.basicConfig(level=logging.INFO)
from google.cloud import pubsub_v1
import os
import gcsfs
import json
import requests
from pdfminer.high_level import extract_text
import openai
from io import BytesIO
import datetime
from nltk.tokenize import word_tokenize
import nltk
nltk.download('punkt')

PROJECT_ID = os.environ.get('PROJECT_ID')
EVENT_BUS = os.environ.get('EVENT_BUS')
SPODKAST_ROUTE = "gs://yggdrasil-ai-hermod-spodkast/{owner}/{id}"
ENTITY = "spodkast"

APP = Flask("internal")
openai.api_key = os.environ.get('OPENAI_KEY')

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

def generate_answer(prompt, message_list, model, attempt=0):
        """
        This function will continue the conversation.
        Parameters:
            conversation_info: The information of the conversation
            message: The message to append
        """
        logging.info("Generating answer")
        messages = [{"role": "system", "content": prompt}]
        messages.extend([{"role": "user", "content": message} for message in message_list])
        print(messages)
        full_prompt = {
            "model": model,
            "messages": messages
        }

        logging.info("Calling OpenAI: {}".format(full_prompt))

        try:
          response = openai.ChatCompletion.create(**full_prompt)["choices"][0]["message"]["content"]
        except Exception as e:
          if attempt < 3:
            response = generate_answer(prompt, message_list, model, attempt+1)
          else:
            raise e
        return response

def read_file(file):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    with fs.open(file, 'r') as f:
        content = f.read()
    return content

def read_bytes(file):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    with fs.open(file, 'rb') as f:
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
    

def summarizer(text, max_tokens=2000):
    """Summarize a given piece of text using GPT-3"""
    # Tokenize text and split into chunks of 2000 tokens
    SUMMARIZER_PROMPT = """You are a text analyst. You will receive a fragment of a text and you should summarize it, and select its more original and remarkable statements and present them in a particular format. Example:
    ```
    user: very advanced school, by amusing the poor.
    But this is not a solution: it is an aggravation of the difficulty. The proper
    aim is to try and reconstruct society on such a basis that poverty will be
    impossible. And the altruistic virtues have really prevented the carrying out of
    this aim. Just as the worst slave-owners were those who were kind to their
    slaves, and so prevented the horror of the system being realised by those who
    suffered from it, and understood by those who contemplated it, so, in the
    present state of things in England, the people who do most harm are the people
    who try to do most good; and at last we have had the spectacle of men who
    have really studied the problem and know the life-educated men who live in
    the East End—coming forward and imploring the community to restrain its
    altruistic impulses of charity, benevolence, and the
    assistant: #summary#
    The argument criticizes the prevailing approach to addressing poverty, which it considers not only ineffective but also harmful. Instead of focusing on "amusing the poor" or temporarily relieving their suffering, it suggests that society should be restructured to prevent poverty from existing in the first place. The text interestingly equates altruistic virtues, like charity and benevolence, to a form of slavery, as these actions, though seemingly kind, prevent the true severity of poverty from being fully understood and addressed.

    #original statements#
    - comparison between well-intentioned but potentially harmful philanthropists and kind slave-owners.
    - plea from those who have studied the problem and live in the impacted areas, asking the community to reconsider its charitable actions.
    ```
    It's very important to use #summary# and #original statements# flags as used in the example, and to use a list to separate original statements.
    """

    SUMMARIZATION_MAPREDUCE_PROMPT = """You coordinate a team of text analysts.
    Each text analyst have read a section from a text and has extracted summary and original statements. 
    Your job is putting all analysts' work together. Extract the combined summary and original statements. 
    They don't have to convey the same information, summary has to reflect the general arguments of the text, while original statements must be a selection of concrete, original points.
    Summary and original statements must always provide different information, summary shouldn't be deductible from original statements.
    Your answer should have a #summary# and an #original statements# section"""
        
    tokens = word_tokenize(text)
    chunks = [tokens[i:i + 2000] for i in range(0, len(tokens), max_tokens)]

    # Summarize each chunk
    summaries = [generate_answer(SUMMARIZER_PROMPT, [' '.join(chunk)], "gpt-3.5-turbo") for chunk in chunks]

    def reduce_summaries(summaries):
        # Pack summaries into groups with sum of tokens <= 3000
        summary_groups = []
        current_group = []
        current_group_tokens = 0
        for summary in summaries:
            summary_tokens = len(word_tokenize(summary))
            if current_group_tokens + summary_tokens > 2000:
                # Start a new group
                summary_groups.append(current_group)
                current_group = [summary]
                current_group_tokens = summary_tokens
            else:
                # Add to the current group
                current_group.append(summary)
                current_group_tokens += summary_tokens
        # Add the last group if it isn't empty
        if current_group:
            summary_groups.append(current_group)
        mapreduced = [generate_answer(SUMMARIZATION_MAPREDUCE_PROMPT, group, "gpt-3.5-turbo") for group in summary_groups]
        return mapreduced
    
    while len(summaries) > 1:
        summaries = reduce_summaries(summaries)
    
    return summaries[0]

def parse_sections(text):
    # Split the text into sections
    sections = text.split("#section")[1:]  # The first item is empty, so we skip it

    # Remove leading and trailing whitespace from each section
    sections = [section.strip() for section in sections]

    return sections

def process_input_files(workspace, input_files=None):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    if not input_files:
        # Get list of files in {workspace}/input_files
        input_files = fs.glob(f'{workspace}/input_files/')
    summaries = []
    for file in input_files:
        # Extract text
        print('Processing:', file)
        fp = BytesIO(read_bytes(file))
        extracted_text = extract_text(fp)
        print('Summarizing extracted text')
        summarized_text = summarizer(extracted_text)
        filename = file.split('/')[-1]
        summaries += [summarized_text]
        write_to_file(f"{workspace}/input_summaries/{filename}", summarized_text)
    return summaries

def generate_introduction(podcast_plan, requirements):
    GENERATE_INTRODUCTION_PROMPT = """You are a podcast speaker. You should write the introduction of a podcast which skeleton will be provided by the user.
    Keep it really short and interesting. You don't have to include all data, just to present the podcast, your colleagues will do the different sections after you.
    Keep it short. You should comply with this requirements: {podcast_requirements}"""
    return generate_answer(GENERATE_INTRODUCTION_PROMPT.format(podcast_requirements=requirements), [podcast_plan], "gpt-3.5-turbo")

def generate_closure(podcast_plan, requirements):
    GENERATE_CLOSURE_PROMPT = """You are a podcast speaker. You should write the closure of a podcast which skeleton will be provided by the user.
    Keep it short and engaging. You don't have to talk about all topics, as your colleagues have already tackled them.
    You should comply with this requirements: {podcast_requirements}"""
    return generate_answer(GENERATE_CLOSURE_PROMPT.format(podcast_requirements=requirements), [podcast_plan], "gpt-3.5-turbo")

def generate_skeleton(workspace, requirements=None, summaries=None):
    GENERATE_STRUCTURE_PROMPT = """
    You are a podcast planner. You must create the skeleton of a podcast based on different summaries of some arguments, each with some original statements that must be stated in different moments of the podcast.
    You should divide it in sections, with the following structure:
    ```
    #section <number>#
    - Title: section title
    - Ideas: ideas section must talk about
    - Original statements: original statements that should appear in this section, separated by commands
    ```
    The podcast must comply with the following requirements: {podcast_requirements}
    """
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)

    if not requirements:
        requirements = read_file(f'{workspace}/requirements.txt')
    if not summaries:
        summary_files = fs.glob(f'{workspace}/input_summaries/')
        summaries = [read_file(file) for file in summary_files]
    podcast_plan = generate_answer(GENERATE_STRUCTURE_PROMPT.format(podcast_requirements=requirements), message_list=summaries, model="gpt-3.5-turbo")
    write_to_file(f'{workspace}/podcast_plan.txt', podcast_plan)
    sections = parse_sections(podcast_plan)
    introduction = generate_introduction(podcast_plan, requirements)
    write_to_file(f'{workspace}/introduction.txt', introduction)
    closure = generate_closure(podcast_plan, requirements)
    write_to_file(f'{workspace}/closure.txt', closure)
    return introduction, sections, closure


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
    

    # Process input files
    logging.info("Summarizing")
    summaries = process_input_files(assigned_folder, owned_files)

    # Generate podcast skeleton
    logging.info("Generating skeleton")
    introduction, sections, closure = generate_skeleton(assigned_folder, requirements, summaries)

    publish_message(author=author, operation="create", entity_id=name, payload=json.dumps(request_json))

    response = APP.response_class(
        response=json.dumps({"payload":{
            "workspace": assigned_folder,
            "id": name,
            "skeleton": json.dumps({"introduction": introduction,
                                    "sections": sections,
                                    "closure": closure}),
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