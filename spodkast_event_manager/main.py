import os
import logging
logging.basicConfig(level=logging.INFO)
import json
import functions_framework
from google.cloud import pubsub_v1
import openai
import requests
import base64
import datetime
import gcsfs

API_KEY = os.environ.get('OPENAI_KEY')
openai.api_key = os.environ.get('OPENAI_KEY')
TEMPERATURE = 0.0
MAX_EXTRACTION_TOKENS = 3000

PROJECT_ID = os.environ.get('PROJECT_ID')
EVENT_BUS = os.environ.get('EVENT_BUS')
ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_KEY')
VOICE_INTRODUCTION = os.environ.get('VOICE_INTRODUCTION')
VOICE_SECTION = os.environ.get('VOICE_SECTION')
VOICE_CLOSURE = os.environ.get('VOICE_CLOSURE')
SINTONIA_AUDIO = "gs://yggdrasil-ai-hermod-public/sintonia.mp3"
ENTITY = "spodkast"
SPODKAST_ROUTE = "gs://yggdrasil-ai-hermod-spodkast/{owner}/{id}"

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

def write_to_file(file, content):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    with fs.open(file, "w") as file_:
        file_.write(content)

def parse_sections(text):
    # Split the text into sections
    sections = text.split("#section")[1:]  # The first item is empty, so we skip it

    # Remove leading and trailing whitespace from each section
    sections = [section.strip() for section in sections]

    return sections

def generate_sections(workspace, sections = None, requirements = None):
    GENERATE_SECTION_PROMPT = """You are a speaker. You should write a section talking about some ideas and including some statements.
    You should comply with this requirements: {podcast_requirements}"""
    
    if not sections:
        sections = parse_sections(read_file(f'{workspace}/podcast_plan.txt'))
    if not requirements:
        requirements = read_file(f"{workspace}/requirements.txt")

    full_sections = []
    i = 0
    for section in sections:
        i+=1
        generated_section = generate_answer(GENERATE_SECTION_PROMPT.format(podcast_requirements=requirements), [section], "gpt-3.5-turbo")
        write_to_file(f'{workspace}/sections/section{i}.txt', generated_section)
        full_sections += [generated_section]
    return full_sections

def generate_podcast(workspace, introduction, sections, closure):
    fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
    if not introduction:
        introduction = read_file(f'{workspace}/introduction.txt')
    if not sections:
        section_files = fs.glob(f'{workspace}/sections/')
        sections = [read_file(file) for file in section_files]
    if not closure:
        closure = read_file(f'{workspace}/closure.txt')

    def generate_audio(voice, text, output_file):
        fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
        logging.info(f"Generating audio: {text}. With voice: {voice}")
        #audio = generate(text=text, voice=voice, verify=False)
        CHUNK_SIZE = 1024
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"

        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY
        }

        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }

        response = requests.post(url, json=data, headers=headers, verify=False)
        logging.info("Saving audio")
        with fs.open(output_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
        return output_file  
    
    def combine_audios(audio_files, destiny_name):
        fs = gcsfs.GCSFileSystem(project=PROJECT_ID)
        combined_audio = b''
        for file in audio_files:
            with fs.open(file, 'rb') as audio_file:
                combined_audio += audio_file.read()

        with fs.open(destiny_name, 'wb') as destiny_file:
            destiny_file.write(combined_audio)
        return destiny_name
    
    introduction_audio = generate_audio(VOICE_INTRODUCTION, introduction, f'{workspace}/introduction.mp3')
    section_audios = []
    i = 0
    for section in sections:
        i += 1
        section_audio = generate_audio(VOICE_SECTION, section, f'{workspace}/mp3_sections/section{i}.mp3')
        section_audios += [section_audio]
    closure_audio = generate_audio(VOICE_CLOSURE, closure, f'{workspace}/closure.mp3')
    audios = [introduction_audio]
    audios += [SINTONIA_AUDIO]
    audios += section_audios
    audios += [SINTONIA_AUDIO]
    audios += [closure_audio]
    logging.info("Combining audios")
    podcast = combine_audios(audios, f'{workspace}/podcast.mp3')
    return podcast

def _produce_spodkast(author, spodkast_id, payload):
    logging.info(f"Received request from {author} to produce podcast {spodkast_id}: {payload}")
    if payload["author"] == "#spokeAgent#":
        author = payload["conversationId"].split(".")[0]
    user = payload["user"] if payload["user"]!="undefined" else author
    assigned_folder = SPODKAST_ROUTE.format(owner=user, id=spodkast_id)
    
    # Generate podcast
    logging.info("Generating podcast")
    podcast = generate_podcast(assigned_folder)

def _extend_spodkast(author, spodkast_id, payload):
    logging.info(f"Received request from {author} to extend sections for {spodkast_id}:{payload}")
    if author == "#spokeAgent#":
        author = payload["conversationId"].split(".")[0]
    user = payload["user"] if payload["user"]!="undefined" else author
    assigned_folder = SPODKAST_ROUTE.format(owner=user, id=spodkast_id)
    
    # Generate sections
    logging.info("Extending sections")
    generate_sections(assigned_folder)

    if payload["slow"]=="0":
        publish_message(author=author, operation="produce", entity_id=spodkast_id, payload=json.dumps(payload))

def _create_spodkast(author, spodkast_id, payload):
    if payload["slow"]=="0":
        if author == "#spokeAgent#":
            author = payload["conversationId"].split(".")[0]
        publish_message(operation="extend", author = author, entity_id = spodkast_id, 
                      payload = json.dumps({'user': payload["user"], "slow": "0"}))

# Cloud function triggered from a message on a Cloud Pub/Sub topic
@functions_framework.cloud_event
def conversation_event_manager(cloud_event):
    """
    Cloud function triggered from a message on a Cloud Pub/Sub topic
    """
    logging.info("Event received")
    event = json.loads(base64.b64decode(cloud_event.data["message"]["data"]).decode())
    if event['entity']==ENTITY:
        if event['operation']=="create":
            _create_spodkast(event['author'],
                             spodkast_id=event['entityId'],
                             payload=json.loads(event['payload']))
        elif event['operation']=="extend":
            _extend_spodkast(event['author'],
                             event['entityId'],
                             payload=json.loads(event['payload']))
        elif event['operation']=="produce":
            _produce_spodkast(event["author"],
                              event['entityId'],
                              payload=json.loads(event['payload']))